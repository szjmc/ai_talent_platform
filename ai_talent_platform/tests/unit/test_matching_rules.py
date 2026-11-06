"""
模块：tests/unit/test_matching_rules.py
职责：MATCH-002 四态判定器单元测试（纯函数，无数据库、无 LLM）。

覆盖任务书要求的核心场景：
    学历缺失 -> UNKNOWN / 年限缺失 -> UNKNOWN / 技能缺失 -> UNKNOWN
    学历明确不达标 -> FAIL / 必备技能满足 -> PASS / 必备技能不满足 -> 按配置口径
    条件描述模糊 -> NEEDS_REVIEW

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：[Agent组] [P1]（匹配规则单元用例，MATCH-002）
"""

from __future__ import annotations

from app.schemas.matching import HardStatus
from app.services.matching_rules import (
    HardRuleConfig,
    aggregate_status,
    apply_unknown_penalty,
    build_profile_view,
    compute_data_completeness,
    count_by_status,
    has_needs_review,
    has_unknown,
    is_excluded,
    judge_hard_conditions,
    preferred_skill_hits,
)

POSITION_ID = 3


def _position(**requirements_overrides):
    requirements = {
        "min_education": "本科",
        "min_work_years": 3,
        "required_skills": ["Python", "FastAPI"],
    }
    requirements.update(requirements_overrides)
    return {"id": POSITION_ID, "title": "高级后端工程师", "requirements": requirements}


def _talent(**overrides):
    data = {
        "id": 12,
        "name": "张三",
        "education": "本科",
        "work_years": 5,
        "skill_tags": ["Python", "FastAPI", "MySQL"],
        "potential_level": "B",
    }
    data.update(overrides)
    return data


def _status_map(results):
    return {item.item: item.status for item in results}


class TestMissingFieldsAreUnknown:
    """重点回归：缺字段绝不能判 FAIL（原实现会把 NULL 算成 0 而淘汰）。"""

    def test_missing_education_is_unknown(self):
        results = judge_hard_conditions(
            _talent(education=None), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["min_education"] == HardStatus.UNKNOWN
        assert not is_excluded(results), "缺学历不能被淘汰"

    def test_missing_work_years_is_unknown(self):
        results = judge_hard_conditions(
            _talent(work_years=None), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["min_work_years"] == HardStatus.UNKNOWN
        assert not is_excluded(results)

    def test_missing_skills_is_unknown(self):
        results = judge_hard_conditions(
            _talent(skill_tags=None), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["required_skills"] == HardStatus.UNKNOWN
        assert not is_excluded(results)

    def test_empty_skills_is_unknown_too(self):
        """空列表无法区分「真的没技能」和「解析未产出」，按保守口径判 UNKNOWN。"""
        results = judge_hard_conditions(
            _talent(skill_tags=[]), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["required_skills"] == HardStatus.UNKNOWN

    def test_unknown_is_not_pass(self):
        """UNKNOWN 不得被当成满足：聚合状态必须是 UNKNOWN 而不是 PASS。"""
        results = judge_hard_conditions(
            _talent(education=None, work_years=None, skill_tags=None),
            _position(),
            position_id=POSITION_ID,
            talent_id=12,
        )
        assert {item.status for item in results} == {HardStatus.UNKNOWN}
        assert aggregate_status(results) == HardStatus.UNKNOWN
        assert has_unknown(results) is True


class TestExplicitFailures:
    def test_education_below_requirement_is_fail(self):
        results = judge_hard_conditions(
            _talent(education="大专"), _position(), position_id=POSITION_ID, talent_id=31
        )
        assert _status_map(results)["min_education"] == HardStatus.FAIL
        assert is_excluded(results) is True

    def test_work_years_not_enough_is_fail(self):
        results = judge_hard_conditions(
            _talent(work_years=2), _position(), position_id=POSITION_ID, talent_id=31
        )
        assert _status_map(results)["min_work_years"] == HardStatus.FAIL

    def test_fail_carries_reason_and_evidence(self):
        """FAIL 必须带原因与可追溯证据 ID（原实现只有一句拼接文本、无结构化证据）。"""
        results = judge_hard_conditions(
            _talent(education="大专"), _position(), position_id=POSITION_ID, talent_id=31
        )
        failed = next(item for item in results if item.status == HardStatus.FAIL)
        assert "学历不满足" in failed.reason
        assert failed.evidence_id is not None
        assert failed.evidence_id.startswith("EVID-3-31-")

    def test_unknown_talent_never_excluded(self):
        """任务书明确：UNKNOWN 人才不得进入 excluded。"""
        results = judge_hard_conditions(
            _talent(education="大专", work_years=None),
            _position(),
            position_id=POSITION_ID,
            talent_id=12,
        )
        # 学历明确不达标 -> 仍然 FAIL（有证据），说明 UNKNOWN 不会掩盖其他 FAIL
        assert is_excluded(results) is True

        only_unknown = judge_hard_conditions(
            _talent(education=None), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert is_excluded(only_unknown) is False


class TestSkillsRules:
    def test_all_required_skills_hit_is_pass(self):
        results = judge_hard_conditions(
            _talent(), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["required_skills"] == HardStatus.PASS

    def test_missing_skill_default_mode_is_fail(self):
        """B-1 未确认，默认 all：缺 1 项必备技能判 FAIL（口径可配置）。"""
        results = judge_hard_conditions(
            _talent(skill_tags=["Python"]), _position(), position_id=POSITION_ID, talent_id=31
        )
        skill_item = _status_map(results)["required_skills"]
        assert skill_item == HardStatus.FAIL

    def test_missing_skill_partial_review_mode(self):
        """B-1 的另一种口径：部分命中转人工，不淘汰。"""
        config = HardRuleConfig(required_skill_mode="partial_review")
        results = judge_hard_conditions(
            _talent(skill_tags=["Python"]),
            _position(),
            config=config,
            position_id=POSITION_ID,
            talent_id=31,
        )
        assert _status_map(results)["required_skills"] == HardStatus.NEEDS_REVIEW
        assert is_excluded(results) is False

    def test_preferred_skill_never_causes_fail(self):
        """优先条件绝不能作为硬性淘汰条件。"""
        position = _position(preferred_skills=["Go", "Rust"])
        results = judge_hard_conditions(
            _talent(skill_tags=["Python", "FastAPI"]),
            position,
            position_id=POSITION_ID,
            talent_id=12,
        )
        assert is_excluded(results) is False
        assert all(item.item != "preferred_skills" for item in results)

    def test_skill_tags_not_hard_by_default(self):
        """岗位只填了 skill_tags（无 required_skills）时，不做技能硬门槛（修复过度淘汰）。"""
        position = {"id": POSITION_ID, "skill_tags": ["Go", "Rust"], "requirements": {}}
        results = judge_hard_conditions(
            _talent(skill_tags=["Python"]), position, position_id=POSITION_ID, talent_id=12
        )
        assert results == []
        assert is_excluded(results) is False

    def test_skill_tags_as_hard_switch(self):
        """兼容旧行为的开关：显式开启后 skill_tags 才当必备技能。"""
        config = HardRuleConfig(skill_tags_as_hard=True)
        position = {"id": POSITION_ID, "skill_tags": ["Go"], "requirements": {}}
        results = judge_hard_conditions(
            _talent(skill_tags=["Python"]),
            position,
            config=config,
            position_id=POSITION_ID,
            talent_id=31,
        )
        assert _status_map(results)["required_skills"] == HardStatus.FAIL


class TestVagueConditions:
    def test_vague_condition_is_needs_review(self):
        """非结构化条件（如「具备良好的业务理解能力」）无法自动判定 -> NEEDS_REVIEW。"""
        position = _position()
        position["requirements"]["required_conditions"] = ["具备良好的业务理解能力"]
        results = judge_hard_conditions(
            _talent(), position, position_id=POSITION_ID, talent_id=12
        )
        vague = [item for item in results if item.status == HardStatus.NEEDS_REVIEW]
        assert len(vague) == 1
        assert "业务理解能力" in vague[0].reason
        assert is_excluded(results) is False, "模糊条件不得导致淘汰"
        assert has_needs_review(results) is True

    def test_condition_covered_by_structured_is_not_duplicated(self):
        """已被学历/技能覆盖的条件不再产生 NEEDS_REVIEW 重复项。"""
        position = _position(required_conditions=["本科及以上学历", "熟悉 Python"])
        results = judge_hard_conditions(
            _talent(), position, position_id=POSITION_ID, talent_id=12
        )
        assert not has_needs_review(results)

    def test_unrecognized_education_is_needs_review(self):
        """学历表述无法映射（如「统招本科」）时应转人工，而不是当 0 级判 FAIL。"""
        results = judge_hard_conditions(
            _talent(education="统招本科"), _position(), position_id=POSITION_ID, talent_id=12
        )
        assert _status_map(results)["min_education"] == HardStatus.NEEDS_REVIEW
        assert is_excluded(results) is False


class TestProfileCoercion:
    def test_dict_and_model_are_equivalent(self):
        """同一个岗位用 dict 与 JobProfileParsed 表达，判定结果应一致。"""
        from app.schemas.position import JobProfileParsed

        model = JobProfileParsed(
            job_title="高级后端工程师",
            required_skills=["Python", "FastAPI"],
            min_education="本科",
            min_work_years=3,
        )
        from_model = judge_hard_conditions(_talent(), model, talent_id=12)
        from_dict = judge_hard_conditions(_talent(), _position(), talent_id=12)

        assert [(i.item, i.status) for i in from_model] == [(i.item, i.status) for i in from_dict]

    def test_position_without_requirements(self):
        """岗位无结构化要求时不产生任何硬性判定（不猜、不补值）。"""
        view = build_profile_view({"id": 3})
        assert view.min_education is None
        assert view.min_work_years is None
        assert view.required_skills == []


class TestAggregations:
    def test_aggregate_priority(self):
        """聚合优先级：FAIL > NEEDS_REVIEW > UNKNOWN > PASS。"""
        results = judge_hard_conditions(
            _talent(education="大专", skill_tags=["Python"]), _position(), talent_id=31
        )
        assert aggregate_status(results) == HardStatus.FAIL
        assert count_by_status(results)["FAIL"] >= 1

    def test_empty_results_is_pass(self):
        assert aggregate_status([]) == HardStatus.PASS

    def test_data_completeness(self):
        assert compute_data_completeness(_talent()) == 1.0
        assert compute_data_completeness(_talent(education=None)) == 0.67
        assert compute_data_completeness({"id": 1}) == 0.0

    def test_unknown_penalty_only_when_unknown(self):
        results = judge_hard_conditions(_talent(education=None), _position(), talent_id=12)
        assert apply_unknown_penalty(90.0, results) == 76.5  # 90 * 0.85

        clean = judge_hard_conditions(_talent(), _position(), talent_id=12)
        assert apply_unknown_penalty(90.0, clean) == 90.0

    def test_preferred_skill_hits(self):
        position = _position(preferred_skills=["Go", "Python"])
        assert preferred_skill_hits(_talent(), position) == ["Python"]
