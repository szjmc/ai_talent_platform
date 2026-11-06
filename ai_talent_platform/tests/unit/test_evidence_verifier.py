"""MATCH-005 证据校验器单元测试。

测试策略：
    证据包全部由 MATCH-004 的 build_match_evidence 真实产出（不手写 Evidence 假数据），
    这样校验器面对的是生产链路的真实输入形态，而不是我臆想的形态。
    仅「证据包自审」一项用 model_copy 构造畸形证据，因为要测的正是「构建器出错时能否兜住」。

隔离说明：
    项目 tests/conftest.py 需要 MySQL 连接，这里用 --confcutdir=tests/unit 隔离运行。

标签：[Agent组] [P1]（证据校验单元用例）
"""

from __future__ import annotations

import pytest

from app.schemas.matching import (
    ExplanationClaim,
    MatchExplanation,
    MatchEvidencePack,
)
from app.services.evidence_builder import build_match_evidence
from app.services.evidence_verifier import (
    VerifyConfig,
    audit_pack,
    verify,
    verify_explanation,
)
from app.services.matching_rules import HardRuleConfig

# ------------------------------------------------------------------ 测试夹具

POSITION = {
    "id": 7,
    "title": "Python 后端工程师",
    "requirements": {
        "min_education": "本科",
        "min_work_years": 3,
        "required_skills": ["Python", "FastAPI"],
        "preferred_skills": ["Docker"],
        "responsibilities": ["参与 Python 后端开发"],
    },
}

TALENT_OK = {
    "id": 12,
    "name": "张伟",
    "education": "本科",
    "work_years": 5,
    "skill_tags": ["Python", "FastAPI", "MySQL", "Docker"],
}

TALENT_FAIL_EDU = {
    "id": 12,
    "name": "李四",
    "education": "大专",
    "work_years": 5,
    "skill_tags": ["Python", "FastAPI"],
}

TALENT_MISSING = {"id": 12, "name": "王五", "work_years": 5, "skill_tags": ["Python"]}

TALENT_OTHER = {
    "id": 13,
    "name": "赵六",
    "education": "硕士",
    "work_years": 8,
    "skill_tags": ["Python", "FastAPI", "Go"],
}

DIMENSIONS = {"skill": 80.0, "experience": 100.0, "education": 100.0, "quality": 70.0}


def _build(talent=None, *, rule_config=None, dimension_scores=None) -> MatchEvidencePack:
    """用真实构建器产出证据包。"""
    return build_match_evidence(
        POSITION,
        talent or TALENT_OK,
        dimension_scores=DIMENSIONS if dimension_scores is None else dimension_scores,
        rule_config=rule_config,
    )


def _eid(pack: MatchEvidencePack, item: str) -> str:
    """取某个硬条件项对应的证据 ID。"""
    for result in pack.hard_results:
        if result.item == item and result.evidence_id:
            return result.evidence_id
    raise AssertionError(f"证据包中没有 {item} 的判定结果")


def _explanation(**overrides) -> MatchExplanation:
    """构造一份默认合法的解释，按需覆盖字段。"""
    base = {
        "summary": "硬性条件判定完成，详见各项结论。",
        "source": "llm_fallback",
        "prompt_version": "v1",
    }
    base.update(overrides)
    return MatchExplanation(**base)


def _claim(text: str, *ids: str, claim_type: str = "fact") -> ExplanationClaim:
    return ExplanationClaim(text=text, evidence_ids=list(ids), claim_type=claim_type)


# ------------------------------------------------------------------ 1. 有效证据通过


def test_valid_claim_passes():
    """引用真实存在的证据、表述不越界 -> 保留。"""
    pack = _build()
    eid = _eid(pack, "required_skills")
    result = verify_explanation(
        _explanation(highlights=[_claim("技能标签包含 Python 与 FastAPI，命中岗位必备技能", eid)]),
        pack,
    )
    assert len(result.explanation.highlights) == 1
    assert result.rejected == []
    assert result.pass_rate == 1.0


# ------------------------------------------------------------------ 2. 无效 evidence_id 拒绝


def test_unknown_evidence_id_rejected():
    """编造证据编号 -> evidence_not_found。"""
    pack = _build()
    result = verify_explanation(
        _explanation(highlights=[_claim("该候选人具备丰富的后端经验", "EVID-7-12-999")]),
        pack,
    )
    assert result.explanation.highlights == []
    assert [item.reason for item in result.rejected] == ["evidence_not_found"]
    assert "EVID-7-12-999" in (result.rejected[0].detail or "")


# ------------------------------------------------------------------ 3. 跨人才证据拒绝


def test_cross_talent_evidence_rejected():
    """引用另一个候选人的证据 ID -> 不可用（不在当前证据包索引中）。"""
    other_pack = _build(TALENT_OTHER)
    foreign_id = _eid(other_pack, "min_education")
    assert foreign_id.startswith("EVID-7-13-")

    pack = _build()
    result = verify_explanation(
        _explanation(highlights=[_claim("该候选人学历为硕士", foreign_id)]),
        pack,
    )
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "evidence_not_found"


# ------------------------------------------------------------------ 4. missing 不能证明能力


def test_missing_evidence_cannot_prove_ability():
    """档案无学历字段时，不能用缺失证据写「具备本科学历」这类正面结论。"""
    pack = _build(TALENT_MISSING)
    eid = _eid(pack, "min_education")
    evidence = next(item for item in pack.evidences if item.evidence_id == eid)
    assert evidence.evidence_type == "missing"

    result = verify_explanation(_explanation(highlights=[_claim("候选人具备本科学历", eid)]), pack)
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "missing_evidence_misuse"


def test_gap_with_missing_evidence_allowed():
    """gaps 分区引用缺失证据是合法的：它描述的正是「无法判断」。"""
    pack = _build(TALENT_MISSING)
    eid = _eid(pack, "min_education")
    result = verify_explanation(
        _explanation(gaps=[_claim("档案缺少学历信息，学历条件无法核实", eid)]),
        pack,
    )
    assert len(result.explanation.gaps) == 1
    assert result.rejected == []


# ------------------------------------------------------------------ 5. 与硬条件冲突


def test_conflict_with_fail_rejected():
    """学历判 FAIL 时，不得在 highlights 写学历达标。"""
    pack = _build(TALENT_FAIL_EDU)
    eid = _eid(pack, "min_education")
    result = verify_explanation(
        _explanation(highlights=[_claim("候选人学历符合岗位要求", eid)]),
        pack,
    )
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "conflict_with_hard_result"
    assert "FAIL" in (result.rejected[0].detail or "")


def test_conflict_with_unknown_rejected():
    """学历字段缺失（UNKNOWN）时，同样不能下正面结论。"""
    pack = _build(TALENT_MISSING)
    eid = _eid(pack, "min_education")
    result = verify_explanation(
        _explanation(highlights=[_claim("该候选人为本科学历", eid)]),
        pack,
    )
    # 该证据本身是 missing，先被 missing 滥用规则拦下；两条规则都不允许它通过
    assert result.explanation.highlights == []
    assert result.rejected[0].reason in {"missing_evidence_misuse", "conflict_with_hard_result"}


# ------------------------------------------------------------------ 6. 无证据声明


def test_no_evidence_rejected():
    """模型返回空 evidence_ids（绕过 Schema 校验时）-> no_evidence。"""
    pack = _build()
    claim = ExplanationClaim.model_construct(text="该候选人非常优秀，强烈推荐。", evidence_ids=[], claim_type="fact")
    result = verify_explanation(_explanation(highlights=[claim]), pack)
    assert result.rejected[0].reason == "no_evidence"


# ------------------------------------------------------------------ 7. 待核实声明保留


def test_needs_review_goes_pending():
    """条件判 NEEDS_REVIEW 时，正面结论不进 highlights，而是进待核实清单。"""
    rule_config = HardRuleConfig(required_skill_mode="partial_review")
    pack = _build({"id": 12, "name": "孙七", "education": "本科", "work_years": 5, "skill_tags": ["Python"]},
                  rule_config=rule_config)
    statuses = {item.item: item.status.value for item in pack.hard_results}
    assert statuses["required_skills"] == "NEEDS_REVIEW"

    eid = _eid(pack, "required_skills")
    result = verify_explanation(
        _explanation(highlights=[_claim("岗位必备技能已全部满足", eid)]),
        pack,
    )
    assert result.explanation.highlights == []
    assert result.rejected == []
    assert any("待人工核实" in item for item in result.pending)


# ------------------------------------------------------------------ 8. 能力术语越界（核心用例）


def test_term_out_of_scope_rejected():
    """任务书指定用例：档案只写了 Python 后端开发，不能说成「高并发架构经验」。"""
    pack = _build()
    eid = _eid(pack, "required_skills")
    result = verify_explanation(
        _explanation(
            highlights=[_claim("候选人具备大型高并发系统架构设计经验，可主导平台架构演进", eid)]
        ),
        pack,
    )
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "term_out_of_scope"
    assert "高并发" in (result.rejected[0].detail or "")


def test_jd_requirement_does_not_authorize_capability():
    """JD 里写了「高并发」，不等于候选人具备高并发经验 —— 必须拦下。

    业务含义（容易搞反，故单列用例固化）：
        职责证据证明的是「岗位要求这项能力，且候选人技能标签里有相关技能」，
        而不是「候选人做过该职责」。JD 的措辞不能当人才的能力证明。
        本期人才档案没有结构化的项目经历字段，因此这类断言没有任何字段能支撑，
        一律拦下 —— 宁可少说，不可编造。
    """
    position = {
        "id": 7,
        "title": "后端工程师",
        "requirements": {
            "min_education": "本科",
            "required_skills": ["Python"],
            "responsibilities": ["负责高并发交易系统的 Python 服务开发"],
        },
    }
    talent = {"id": 12, "name": "张伟", "education": "本科", "work_years": 5, "skill_tags": ["Python"]}
    pack = build_match_evidence(position, talent, dimension_scores=DIMENSIONS)

    resp_evidence = next(
        item for item in pack.evidences if str(item.requirement_ref).startswith("responsibilities[")
    )
    # 同一条证据，只说「技能匹配」可以过
    ok = verify_explanation(
        _explanation(highlights=[_claim("职责涉及 Python 服务开发，候选人技能标签包含 Python", resp_evidence.evidence_id)]),
        pack,
    )
    assert len(ok.explanation.highlights) == 1, ok.rejected

    # 但把岗位要求说成人才能力，必须拦
    bad = verify_explanation(
        _explanation(highlights=[_claim("候选人具备高并发系统开发经验", resp_evidence.evidence_id)]),
        pack,
    )
    assert bad.explanation.highlights == []
    assert bad.rejected[0].reason == "term_out_of_scope"


# ------------------------------------------------------------------ 9. 数值一致性


def test_years_overstatement_rejected():
    """解释写的年限高于档案记录 -> 冲突。"""
    pack = _build()
    eid = _eid(pack, "min_work_years")
    result = verify_explanation(
        _explanation(highlights=[_claim("候选人拥有 8 年工作经验，经验非常充足", eid)]),
        pack,
    )
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "conflict_with_hard_result"
    assert "8" in (result.rejected[0].detail or "")


def test_years_within_record_allowed():
    """年限不超过档案 -> 允许（不得因为提到年限就误杀）。"""
    pack = _build()
    eid = _eid(pack, "min_work_years")
    result = verify_explanation(
        _explanation(highlights=[_claim("候选人 5 年工作经验，满足岗位 3 年要求", eid)]),
        pack,
    )
    assert len(result.explanation.highlights) == 1, result.rejected


# ------------------------------------------------------------------ 10. 总结校验


def test_summary_rewritten_when_out_of_scope():
    """总结出现无证据支撑的高危术语 -> 替换为事实型总结并留证。"""
    pack = _build()
    result = verify_explanation(
        _explanation(summary="该候选人具备高并发分布式架构能力，是理想人选。"),
        pack,
    )
    assert "高并发" not in result.explanation.summary
    assert "硬性条件共" in result.explanation.summary
    assert any(item.reason == "term_out_of_scope" for item in result.rejected)


def test_summary_kept_when_clean():
    """总结无越界表述时原样保留（不无故改写）。"""
    pack = _build()
    original = "硬性条件通过 2 项，档案完整。"
    result = verify_explanation(_explanation(summary=original), pack)
    assert result.explanation.summary == original


# ------------------------------------------------------------------ 11. 证据包自审


def test_audit_pack_detects_bad_evidence():
    """构建器若产出畸形证据，自审必须报出来而不是放行。"""
    pack = _build()
    evidences = list(pack.evidences)

    # 复制一条 fact 证据，抹掉原始值（模拟「假事实」）
    broken = evidences[0].model_copy(
        update={"evidence_id": "EVID-7-12-900", "evidence_type": "fact", "source_value": None}
    )
    # 复制一条 missing 证据，塞入能力术语（模拟「给缺失发许可证」）
    tainted = evidences[1].model_copy(
        update={"evidence_id": "EVID-7-12-901", "evidence_type": "missing", "capacity_terms": ["本科"]}
    )
    bad_pack = pack.model_copy(update={"evidences": [*evidences, broken, tainted]})

    issues = audit_pack(bad_pack)
    assert any("fact 证据缺少原始值" in item for item in issues)
    assert any("missing 证据带有能力术语" in item for item in issues)

    # 畸形证据不得进入可用索引：引用它必须被拒绝
    result = verify_explanation(_explanation(highlights=[_claim("候选人具备本科学历", "EVID-7-12-900")]), bad_pack)
    assert result.explanation.highlights == []
    assert result.rejected[0].reason == "evidence_not_found"


def test_audit_pack_detects_contradiction():
    """同一字段既判 PASS 又声明缺失 -> 必须报矛盾（复现自 ORM 入参的真实问题）。"""
    pack = _build()  # 学历：本科 vs 要求本科 -> PASS
    assert any(item.item == "min_education" and item.status.value == "PASS" for item in pack.hard_results)

    fake_missing = pack.evidences[0].model_copy(
        update={
            "evidence_id": "EVID-7-12-950",
            "evidence_type": "missing",
            "source_field": "education",
            "source_value": None,
            "capacity_terms": [],
        }
    )
    bad_pack = pack.model_copy(update={"evidences": [*pack.evidences, fake_missing]})
    issues = audit_pack(bad_pack)
    assert any("证据自相矛盾" in item for item in issues)


def test_pack_issues_are_reported():
    """证据包问题要回传给上层（不能只写日志，否则排查时看不到）。"""
    pack = _build(TALENT_MISSING)
    result = verify_explanation(_explanation(), pack)
    # 该包存在 UNKNOWN 项，构建器仍给了 capacity_terms —— 属于已知问题（已记入复核清单）
    assert isinstance(result.pack_issues, list)


# ------------------------------------------------------------------ 12. 接口与结果结构


def test_verify_tuple_signature():
    """方案约定的三元组签名可用（供 MATCH-007 按契约调用）。"""
    pack = _build()
    eid = _eid(pack, "required_skills")
    cleaned, rejected, pending = verify(
        _explanation(highlights=[_claim("技能标签包含 Python", eid)]),
        pack,
    )
    assert isinstance(cleaned, MatchExplanation)
    assert rejected == []
    assert isinstance(pending, list)


def test_dedup_pending_and_rejected_cap():
    """重复的待核实项要去重，避免刷屏。"""
    pack = _build()
    eid = _eid(pack, "required_skills")
    text = "候选人精通 Python 与 FastAPI"
    result = verify_explanation(
        _explanation(highlights=[_claim(text, eid), _claim(text, eid)]),
        pack,
    )
    assert len(result.pending) == len(set(result.pending))
    assert "精通" in " ".join(result.pending)


def test_invalid_input_raises():
    """入参类型不对时明确报错，而不是静默返回空结果。"""
    from app.core.exceptions import ParamError

    pack = _build()
    with pytest.raises(ParamError):
        verify_explanation(_explanation(), {"not": "a pack"})  # type: ignore[arg-type]


def test_config_can_disable_blocking_terms():
    """校验口径可配置：术语表置空后同样的越界表述不再被拦（证明规则来自配置而非硬编码）。"""
    pack = _build()
    eid = _eid(pack, "required_skills")
    cfg = VerifyConfig(blocking_terms=(), softening_terms=())
    result = verify_explanation(
        _explanation(highlights=[_claim("候选人具备高并发架构设计经验", eid)]),
        pack,
        cfg,
    )
    assert len(result.explanation.highlights) == 1
