"""
文件：tests/unit/test_explanation_service.py
职责：解释编排与证据校验单元测试（无网络 / 无数据库）。

覆盖：
1. 规则兜底：只输出事实、每条结论都挂真实 evidence_id；
2. Dify 失败降级：通道必须如实回落，且错误留痕；
3. Dify 成功：编造 evidence_id 的结论被剔除，合法结论保留；
4. 缺失证据滥用：用 missing 证据证明能力，必须被拒绝。

标签：[Agent组] [P1]（解释服务单元用例）
"""

from __future__ import annotations

from typing import Any

from app.adapters.dify.workflow_adapter import DifyTimeoutError
from app.schemas.matching import HardStatus
from app.services.evidence_builder import build_match_evidence
from app.services.explanation_service import ExplanationService
from app.services.matching_rules import HardRuleConfig

POSITION = {
    "id": 1,
    "title": "后端开发工程师",
    "skill_tags": ["Python", "MySQL"],
    "requirements": {
        "job_title": "后端开发工程师",
        "required_skills": ["Python", "MySQL"],
        "min_education": "本科",
        "min_work_years": 3,
    },
}

TALENT = {
    "id": 2,
    "name": "张三",
    "education": "本科",
    "work_years": 5,
    "skill_tags": ["Python", "MySQL"],
    "potential_level": "B",
}


def make_pack(talent: dict[str, Any] | None = None):
    """构造证据包（同一口径：四态判定 -> 证据 -> 校验）。"""
    return build_match_evidence(
        POSITION,
        talent or TALENT,
        position_id=1,
        talent_id=2,
        dimension_scores={"skill": 100.0, "experience": 100.0, "education": 100.0, "quality": 80.0},
        rule_config=HardRuleConfig(zero_work_years_as_review=True),
    )


class _FakeDify:
    """可控的 Dify 适配器替身（只实现被编排器用到的两个成员）。"""

    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.configured = True
        self._payload = payload
        self._error = error

    async def run_match_explanation(self, pack: Any) -> dict[str, Any]:
        if self._error:
            raise self._error
        return {"parsed": self._payload or {}}


class TestRuleFallback:
    async def test_rule_explanation_is_fact_based(self):
        pack = make_pack()
        outcome = await ExplanationService(dify=None).generate(pack, use_external=False)

        assert outcome.channel == "rule_fallback"
        explanation = outcome.explanation
        assert explanation.source == "rule_fallback"
        assert "项硬性条件" in explanation.summary

        ids = {item.evidence_id for item in pack.evidences}
        claims = explanation.highlights + explanation.gaps + explanation.risks
        assert claims, "规则解释至少要给出事实结论"
        for claim in claims:
            assert claim.evidence_ids
            assert set(claim.evidence_ids) <= ids
        assert outcome.result.rejected == []

    async def test_rule_explanation_marks_unknown_as_pending(self):
        talent = {"id": 2, "name": "李四", "education": None, "work_years": 0, "skill_tags": None}
        pack = make_pack(talent)
        outcome = await ExplanationService(dify=None).generate(pack, use_external=False)

        assert outcome.explanation.pending_verification
        assert any(item.status == HardStatus.UNKNOWN for item in pack.hard_results)


class TestDifyChannel:
    @staticmethod
    def _disable_llm(monkeypatch) -> None:
        """屏蔽 LLM 通道（环境无关化）。

        原因：本地已接 Ollama（LLM_API 不再为空），Dify 失败后降级链会真实
        走到 llm_fallback；要验证「规则兜底」这个分支，必须显式让 LLM 不可用。
        """
        async def _llm_down(self_, pack):  # noqa: ANN001 - 测试替身
            raise RuntimeError("模拟 LLM 不可用")

        monkeypatch.setattr(ExplanationService, "_via_llm", _llm_down)

    async def test_dify_timeout_falls_back(self, monkeypatch):
        self._disable_llm(monkeypatch)
        fake = _FakeDify(error=DifyTimeoutError("模拟超时"))
        pack = make_pack()

        outcome = await ExplanationService(dify=fake).generate(pack)

        assert outcome.channel == "rule_fallback"
        assert outcome.explanation.source == "rule_fallback"
        assert any("Dify" in note for note in outcome.errors)

    async def test_fabricated_evidence_id_is_rejected(self):
        pack = make_pack()
        valid_id = next(
            item.evidence_id
            for item in pack.hard_results
            if item.status == HardStatus.PASS and item.evidence_id
        )
        fake = _FakeDify(
            payload={
                "summary": "本次匹配基于岗位必备条件与档案事实进行说明。",
                "highlights": [
                    {"text": "学历满足岗位要求", "evidence_ids": [valid_id], "claim_type": "fact"},
                    {"text": "具备高并发系统经验", "evidence_ids": ["EVID-1-2-999"], "claim_type": "fact"},
                ],
                "gaps": [],
                "risks": [],
                "interview_suggestions": ["请核实项目经历"],
            }
        )

        outcome = await ExplanationService(dify=fake).generate(pack)

        assert outcome.channel == "dify"
        texts = [claim.text for claim in outcome.explanation.highlights]
        assert "学历满足岗位要求" in texts
        assert "具备高并发系统经验" not in texts
        assert any(item.reason == "evidence_not_found" for item in outcome.result.rejected)

    async def test_invalid_dify_structure_falls_back(self, monkeypatch):
        """Dify 返回非对象 / 缺 summary：按失败处理，降级规则解释，不能 500。"""
        self._disable_llm(monkeypatch)
        fake = _FakeDify(payload={"foo": "bar"})
        pack = make_pack()

        outcome = await ExplanationService(dify=fake).generate(pack)

        assert outcome.channel == "rule_fallback"
        assert outcome.explanation.source == "rule_fallback"
        assert any("summary" in note for note in outcome.errors)

    async def test_missing_evidence_cannot_prove_capability(self):
        talent = {"id": 2, "name": "王五", "education": None, "work_years": 5, "skill_tags": ["Python", "MySQL"]}
        pack = make_pack(talent)
        missing_school = next(
            item for item in pack.evidences if item.evidence_type == "missing" and item.source_field == "education"
        )
        fake = _FakeDify(
            payload={
                "summary": "候选人基本符合岗位要求。",
                "highlights": [
                    {"text": "候选人具备本科及以上学历", "evidence_ids": [missing_school.evidence_id], "claim_type": "fact"}
                ],
            }
        )

        outcome = await ExplanationService(dify=fake).generate(pack)

        assert outcome.explanation.highlights == []
        assert any(item.reason == "missing_evidence_misuse" for item in outcome.result.rejected)
        detail = next(
            item.detail for item in outcome.result.rejected if item.reason == "missing_evidence_misuse"
        )
        assert "缺失类证据" in detail
