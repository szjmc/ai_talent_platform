"""
模块：tests/unit/test_matching_schemas.py
职责：MATCH-001 Schema 契约单元测试（纯内存，不依赖 MySQL/Redis/Milvus）。

运行方式（纯单测，跳过依赖数据库的 tests/conftest.py）：
    pytest tests/unit --confcutdir=tests/unit -q

标签：[Agent组] [P1]（匹配 Schema 单元用例，MATCH-001）
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.matching import (
    Evidence,
    ExplanationClaim,
    HardConditionResult,
    HardStatus,
    MatchExplanation,
    MatchItem,
    MatchRunResponse,
)
from app.schemas.position import ConditionItem, JobProfileParsed


def _profile(**overrides):
    data = {
        "job_title": "高级后端工程师",
        "required_skills": ["Python", "FastAPI"],
        "min_education": "本科",
        "min_work_years": 3,
    }
    data.update(overrides)
    return JobProfileParsed(**data)


class TestJobProfileParsed:
    """岗位画像契约。"""

    def test_rejects_unknown_field(self):
        """extra=forbid：Dify 塞入未声明字段必须被拒绝，防止脏数据入库。"""
        with pytest.raises(ValidationError):
            _profile(unexpected_field="AI 自己加的字段")

    def test_missing_education_stays_none(self):
        """JD 未提及学历时必须保持 None，不允许补默认值。"""
        profile = _profile(min_education=None)
        assert profile.min_education is None

    def test_force_review_when_missing_or_conflict(self):
        """有缺失/冲突时强制作废 review_required=False 的尝试，必须转人工确认。"""
        profile = _profile(missing_fields=["min_work_years"], review_required=False)
        assert profile.review_required is True

        conflict = _profile(conflicts=["学历要求同时写了本科与大专"], review_required=False)
        assert conflict.review_required is True

    def test_skills_cleaned_and_deduped(self):
        """技能列表去空、去空格、大小写去重，避免污染集合匹配。"""
        profile = _profile(required_skills=["Python", " python ", "", "FastAPI"])
        assert profile.required_skills == ["Python", "FastAPI"]

    def test_condition_item_confidence_range(self):
        """置信度必须在 0~1。"""
        with pytest.raises(ValidationError):
            ConditionItem(text="熟悉 Python 优先", confidence=1.5)


class TestHardConditionResult:
    """四态判定结果契约。"""

    def test_reason_is_required(self):
        """任务书要求「每项判断必须提供原因」，reason 缺失必须报错。"""
        with pytest.raises(ValidationError):
            HardConditionResult(item="min_education", status=HardStatus.UNKNOWN)

    def test_status_enum_values(self):
        """四态取值固定为大写，与任务书示例一致。"""
        assert [s.value for s in HardStatus] == ["PASS", "FAIL", "UNKNOWN", "NEEDS_REVIEW"]


class TestEvidence:
    """证据契约。"""

    def test_evidence_id_pattern(self):
        """证据 ID 必须符合 EVID-{position}-{talent}-{seq}，否则无法做结构化校验。"""
        with pytest.raises(ValidationError):
            Evidence(
                evidence_id="随便一个字符串",
                claim="技能命中 2/2",
                source_table="talents",
                source_field="skill_tags",
                source_value=["Python", "FastAPI"],
                requirement_ref="required_skills",
            )

    def test_source_value_keeps_original(self):
        """source_value 必须原样保留（含大小写），供后续一致性比对。"""
        evidence = Evidence(
            evidence_id="EVID-3-12-1",
            claim="技能命中 2/2",
            source_table="talents",
            source_field="skill_tags",
            source_value=["Python", "FastAPI"],
            requirement_ref="required_skills",
            capacity_terms=["Python", "FastAPI"],
        )
        assert evidence.source_value == ["Python", "FastAPI"]


class TestExplanationClaim:
    """解释结论契约（防编造第一道闸）。"""

    def test_requires_at_least_one_evidence(self):
        """没有证据 ID 的结论在 Schema 层直接拒绝。"""
        with pytest.raises(ValidationError):
            ExplanationClaim(text="具备大型高并发系统架构经验", evidence_ids=[])

    def test_accepts_with_evidence(self):
        claim = ExplanationClaim(text="技能命中 2/2", evidence_ids=["EVID-3-12-1"])
        assert claim.claim_type == "fact"


class TestMatchExplanation:
    def test_default_source_is_rule_fallback(self):
        """默认来源必须是 rule_fallback，禁止把非 AI 产物伪装成 AI 生成。"""
        explanation = MatchExplanation(summary="技能命中 2/2，年限达标")
        assert explanation.source == "rule_fallback"
        assert explanation.rejected_claims == []


class TestMatchRunResponse:
    def test_minimal_payload(self):
        """跑批响应可用最小字段构造，且 items/excluded 默认互不共享（无可变默认值问题）。"""
        first = MatchRunResponse(batch_id="b7f3c1d2e8a94f60", position_id=3)
        second = MatchRunResponse(batch_id="c8f4d2e3f9a05g71", position_id=4)

        first.items.append(MatchItem(talent_id=12, score=88.5))

        assert second.items == []  # 证明 default_factory 生效，没有共享列表
        assert first.recall_mode == "sql_fallback"
