"""
模块：tests/unit/test_evidence_builder.py
职责：MATCH-004 结构化匹配证据构建器单元测试（纯函数，无数据库、无 LLM）。

覆盖任务书要求的场景：
    学历 / 工作年限 / 技能证据生成
    缺失字段不生成虚假证据（且不引用模型生成的画像摘要）
    FAIL 结果能关联证据 / UNKNOWN 能说明缺失原因 / NEEDS_REVIEW 保留待复核信息
    evidence_id 格式正确且无冲突
    Evidence 通过 Pydantic 校验 / MatchEvidencePack 可序列化

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：[Agent组] [P1]（证据包构建单元用例，MATCH-004）
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import ParamError
from app.schemas.matching import Evidence, HardStatus
from app.services.evidence_builder import build_job_profile, build_match_evidence
from app.services.matching_rules import judge_hard_conditions

EVID_PATTERN = re.compile(r"^EVID-\d+-\d+-\d+$")
POSITION_ID = 7
TALENT_ID = 12


def _position(**requirement_overrides):
    requirements = {
        "min_education": "本科",
        "min_work_years": 3,
        "required_skills": ["Python", "FastAPI"],
        "preferred_skills": ["Docker"],
        "responsibilities": ["负责 Python 后端 API 开发与维护"],
    }
    requirements.update(requirement_overrides)
    return {"id": POSITION_ID, "title": "Python 后端工程师", "requirements": requirements}


def _talent(**overrides):
    data = {
        "id": TALENT_ID,
        "name": "张伟",
        "education": "硕士",
        "work_years": 5,
        "skill_tags": ["Python", "FastAPI", "MySQL", "Docker"],
    }
    data.update(overrides)
    return data


def _by_field(pack, field: str):
    return [item for item in pack.evidences if item.source_field == field]


# ------------------------------------------------------------------ 覆盖范围


def test_education_evidence_uses_raw_value():
    """学历证据：来源字段为 talents.education，原始值保留原样（不是归一化等级）。"""
    pack = build_match_evidence(_position(), _talent())

    evidences = _by_field(pack, "education")
    hard = next(item for item in pack.hard_results if item.item == "min_education")

    assert evidences, "应产出学历证据"
    education_ev = next(item for item in evidences if item.requirement_ref == "min_education")
    assert education_ev.source_table == "talents"
    assert education_ev.source_value == "硕士"  # 原始值，非 4 级
    assert education_ev.evidence_type == "derived"
    assert "本科" in education_ev.capacity_terms
    # 判定结果与证据双向可追溯
    assert hard.evidence_id == education_ev.evidence_id


def test_work_years_evidence():
    """工作年限证据：来源字段 talents.work_years，原始值为 5（不是 5.0 文本）。"""
    pack = build_match_evidence(_position(), _talent())

    evidence = next(item for item in pack.evidences if item.requirement_ref == "min_work_years")
    assert evidence.source_table == "talents"
    assert evidence.source_field == "work_years"
    assert evidence.source_value == 5
    assert "5年" in evidence.capacity_terms


def test_required_skill_evidence():
    """必备技能证据：source_value 为人才技能原始列表，白名单含要求项与实际项。"""
    pack = build_match_evidence(_position(), _talent())

    evidence = next(item for item in pack.evidences if item.requirement_ref == "required_skills")
    assert evidence.source_field == "skill_tags"
    assert evidence.source_value == ["Python", "FastAPI", "MySQL", "Docker"]  # 原样保留大小写
    assert {"Python", "FastAPI"} <= set(evidence.capacity_terms)


def test_preferred_skill_evidence():
    """优先技能证据：命中产出 fact 证据，且明确「不参与淘汰」。"""
    pack = build_match_evidence(_position(), _talent())

    evidences = [item for item in pack.evidences if item.requirement_ref == "preferred_skills"]
    assert len(evidences) == 1
    assert evidences[0].source_value == "Docker"
    assert evidences[0].evidence_type == "fact"
    assert "不参与淘汰" in evidences[0].claim


def test_responsibility_evidence():
    """职责证据：职责文本与人才技能标签重合时产出事实证据。"""
    pack = build_match_evidence(_position(), _talent())

    evidences = [item for item in pack.evidences if item.requirement_ref.startswith("responsibilities[")]
    assert evidences, "职责中含 Python，应产出职责证据"
    assert evidences[0].evidence_type == "fact"
    assert "Python" in evidences[0].source_value


def test_dimension_evidence_marks_default_value():
    """维度证据：档案缺潜力等级时，素质分必须标注为默认值（防把默认分说成能力）。"""
    pack = build_match_evidence(
        _position(),
        _talent(),
        dimension_scores={"skill": 80, "experience": 100, "education": 100, "quality": 70},
    )

    quality = next(item for item in pack.evidences if item.requirement_ref == "dimension.quality")
    assert quality.evidence_type == "missing"
    assert "默认值" in quality.claim
    assert quality.source_value is None

    skill = next(item for item in pack.evidences if item.requirement_ref == "dimension.skill")
    assert skill.evidence_type == "derived"
    assert skill.source_field == "skill_tags"


# ------------------------------------------------------------------ 缺失与防编造


def test_missing_fields_do_not_fabricate_evidence():
    """缺失字段：只产出 missing 证据，不得凭空补出学历/年限/技能。"""
    pack = build_match_evidence(
        _position(), {"id": TALENT_ID, "name": "李娜"}, dimension_scores={"skill": 80, "quality": 70}
    )

    assert pack.data_completeness == 0.0
    assert all(item.evidence_type == "missing" for item in pack.evidences)
    assert all(item.source_value is None for item in pack.evidences)
    # 岗位要求值不应被当作人才实际值写进证据
    assert all(item.source_value != "本科" for item in pack.evidences)
    assert all(item.source_value != ["Python", "FastAPI"] for item in pack.evidences)


def test_portrait_summary_is_never_used_as_evidence():
    """模型生成的画像摘要不得作为证据来源（避免模型自证循环）。"""
    talent = _talent(
        skill_tags=None,
        portrait_summary="AI 生成：该候选人精通高并发架构与分布式事务",
    )
    pack = build_match_evidence(_position(), talent)

    assert all(item.source_field != "portrait_summary" for item in pack.evidences)
    assert all("高并发" not in term for item in pack.evidences for term in item.capacity_terms)


def test_fail_result_linked_to_evidence():
    """FAIL 结果必须能关联到对应证据，且证据说明不满足原因。"""
    pack = build_match_evidence(_position(), _talent(education="大专"))

    failed = [item for item in pack.hard_results if item.status == HardStatus.FAIL]
    assert len(failed) == 1
    evidence = next(item for item in pack.evidences if item.evidence_id == failed[0].evidence_id)
    assert "不满足" in evidence.claim
    assert evidence.source_value == "大专"


def test_unknown_result_explains_missing_reason():
    """UNKNOWN 必须说明缺什么，并回填 evidence_id 指向 missing 证据。"""
    pack = build_match_evidence(_position(), _talent(work_years=None))

    unknown = next(
        item for item in pack.hard_results if item.status == HardStatus.UNKNOWN and item.item == "min_work_years"
    )
    assert unknown.evidence_id, "UNKNOWN 结果也应回填证据 ID，否则前端无法定位缺失说明"
    evidence = next(item for item in pack.evidences if item.evidence_id == unknown.evidence_id)
    assert evidence.evidence_type == "missing"
    assert "缺少工作年限" in evidence.claim


def test_needs_review_keeps_review_info():
    """模糊条件 -> NEEDS_REVIEW，证据保留待复核说明（不得判为满足或淘汰）。"""
    position = _position(required_conditions=["具备良好的跨部门沟通能力"])
    pack = build_match_evidence(position, _talent())

    review = [item for item in pack.hard_results if item.status == HardStatus.NEEDS_REVIEW]
    assert review
    evidence = next(item for item in pack.evidences if item.evidence_id == review[0].evidence_id)
    assert evidence.evidence_type == "missing"
    assert "人工" in evidence.claim
    assert evidence.source_table == "positions"


# ------------------------------------------------------------------ 编号与 Schema


def test_evidence_id_format_and_uniqueness():
    """所有证据 ID 必须符合 EVID-{pos}-{talent}-{seq}，且互不冲突。"""
    pack = build_match_evidence(
        _position(), _talent(), dimension_scores={"skill": 80, "experience": 100}
    )

    ids = [item.evidence_id for item in pack.evidences]
    assert ids
    assert all(EVID_PATTERN.match(item) for item in ids)
    assert len(ids) == len(set(ids)), "证据 ID 出现重复"
    assert all(item.startswith(f"EVID-{POSITION_ID}-{TALENT_ID}-") for item in ids)


def test_new_ids_continue_after_existing_hard_result_ids():
    """新增证据必须接在四态判定已用序号之后，避免 ID 覆盖导致链接错位。"""
    talent = _talent()
    position = _position()
    hard_results = judge_hard_conditions(
        talent, position, position_id=POSITION_ID, talent_id=TALENT_ID
    )
    existing = {item.evidence_id for item in hard_results if item.evidence_id}
    max_seq = max(int(item.rsplit("-", 1)[1]) for item in existing)

    pack = build_match_evidence(position, talent, hard_results)

    for item in hard_results:
        assert item.evidence_id in {ev.evidence_id for ev in pack.evidences}, "既有 ID 必须保留"
    new_ids = [item.evidence_id for item in pack.evidences if item.evidence_id not in existing]
    assert new_ids
    assert all(int(item.rsplit("-", 1)[1]) > max_seq for item in new_ids)


def test_input_hard_results_not_mutated():
    """回填 evidence_id 不得修改入参对象（调用方可能复用判定结果）。"""
    talent = _talent(work_years=None)
    position = _position()
    hard_results = judge_hard_conditions(
        talent, position, position_id=POSITION_ID, talent_id=TALENT_ID
    )
    snapshot = [item.model_dump() for item in hard_results]

    pack = build_match_evidence(position, talent, hard_results)

    assert [item.model_dump() for item in hard_results] == snapshot
    assert any(item.evidence_id for item in pack.hard_results)


def test_evidence_schema_rejects_invalid_payload():
    """Evidence 的 Pydantic 校验必须拦住非法 ID 与空结论。"""
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="BAD-ID",
            claim="结论",
            source_table="talents",
            source_field="education",
            source_value="本科",
            requirement_ref="min_education",
        )

    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="EVID-1-1-1",
            claim="",
            source_table="talents",
            source_field="education",
            source_value="本科",
            requirement_ref="min_education",
        )


def test_pack_serializable_and_deterministic():
    """证据包可 JSON 序列化，且两次构建结果完全一致（可复现、可审计）。"""
    position, talent = _position(), _talent()
    scores = {"skill": 80, "experience": 100, "education": 100, "quality": 70}

    first = build_match_evidence(position, talent, dimension_scores=scores)
    second = build_match_evidence(position, talent, dimension_scores=scores)

    payload = json.loads(first.model_dump_json())
    assert payload["position_id"] == POSITION_ID
    assert payload["talent_id"] == TALENT_ID
    assert payload["data_completeness"] == 1.0
    assert payload["evidences"][0]["evidence_type"] in {"fact", "derived", "missing"}
    assert payload["job_profile_summary"]["job_title"] == "Python 后端工程师"

    assert first.model_dump() == second.model_dump()


def test_requires_identifiers():
    """缺少 ID 时必须报错，不允许生成无法校验的假证据编号。"""
    with pytest.raises(ParamError):
        build_match_evidence({"title": "无 ID 岗位"}, {"name": "无 ID 人才"})


def test_build_job_profile_rejects_missing_title():
    """岗位无名称时报错，不允许用占位名称伪造画像。"""
    with pytest.raises(ParamError):
        build_job_profile({"id": 1, "requirements": {"min_education": "本科"}})


def test_build_job_profile_keeps_unspecified_fields_empty():
    """JD 未提及的条件保持 None（禁止补默认值）——这是四态判定 UNKNOWN 的前提。"""
    profile = build_job_profile({"id": 1, "title": "测试岗"})

    assert profile.min_education is None
    assert profile.min_work_years is None
    assert profile.required_skills == []
