"""
功能：生成推荐理由与差距说明，并统一执行证据校验。
输入：MatchEvidencePack、是否允许外部模型调用。
输出：ExplanationOutcome，含真实通道和降级原因。
流程：Dify → LLM → 规则兜底 → 证据校验。
边界：不决定分数和排序；关闭外部调用仍会生成规则解释。

标签：[Agent组] [P1]（匹配解释服务：Dify 优先，规则兜底）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.adapters.dify.workflow_adapter import DifyError, DifyWorkflowAdapter
from app.config.settings import settings
from app.schemas.matching import (
    ExplanationClaim,
    HardStatus,
    MatchEvidencePack,
    MatchExplanation,
)
from app.services.evidence_verifier import VerificationResult, verify_explanation

logger = logging.getLogger(__name__)

# *kwq* 新增：LLM 通道系统提示词。
# 约束：只允许使用输入事实、必须引用 evidence_id、禁止能力推断。
LLM_SYSTEM_PROMPT = (
    "你是招聘匹配结果的解释助手。你只能使用输入 JSON 中的事实，"
    "不得补充任何未出现的信息，不得给出录用概率或主观评价。"
    "禁止新增阈值、及格线、录用概率；缺失信息只能描述为待核实，不能推断不具备。"
    "输出必须是 JSON，字段：summary(字符串)、highlights/gaps/risks(数组，"
    "每项 {text, evidence_ids, claim_type}，evidence_ids 必须来自输入 evidences 中的 evidence_id)、"
    "interview_suggestions(字符串数组)。"
)

# *kwq* 新增：JSON 代码块提取（模型常把 JSON 包在 ```json 里）。
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ExplanationChannel(str):
    """通道名（仅用于类型提示，取值为三个固定字符串）。"""


@dataclass
class ExplanationOutcome:
    """解释编排结果（含真实通道与降级原因）。"""

    result: VerificationResult
    channel: Literal["dify", "llm_fallback", "rule_fallback"]
    errors: list[str] = field(default_factory=list)

    @property
    def explanation(self) -> MatchExplanation:
        return self.result.explanation


# ------------------------------------------------------------------ 解析工具


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象（失败返回 None，不抛异常）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    match = _FENCE_RE.search(raw)
    if match:
        candidates.insert(0, match.group(1))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            continue
    return None


# *kwq* 新增：把模型输出的结论条目规范化。
# 关键：没有 evidence_ids 的条目不能进入 ExplanationClaim（Schema 要求至少 1 条），
#      直接丢弃并记录原因 —— 这是「无证据结论不得展示」的第一道闸。
def _coerce_claims(value: Any, section: str) -> tuple[list[ExplanationClaim], list[str]]:
    """把 highlights/gaps/risks 转成 ExplanationClaim 列表，返回 (有效条目, 丢弃原因)。"""
    claims: list[ExplanationClaim] = []
    dropped: list[str] = []
    if value is None:
        return claims, dropped
    if isinstance(value, (str, dict)):
        value = [value]
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                dropped.append(f"{section} 条目未引用证据，已忽略：{text[:60]}")
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("claim") or "").strip()
        ids = item.get("evidence_ids") or item.get("evidence_id") or []
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(eid).strip() for eid in ids if str(eid).strip()]
        if not text or not ids:
            if text:
                dropped.append(f"{section} 条目缺少证据引用，已忽略：{text[:60]}")
            continue
        claim_type = item.get("claim_type")
        claims.append(
            ExplanationClaim(
                text=text[:500],
                evidence_ids=ids,
                claim_type=claim_type if claim_type in {"fact", "inference"} else "inference",
            )
        )
    return claims, dropped


def _to_explanation(payload: dict[str, Any], *, source: str, prompt_version: str) -> tuple[MatchExplanation | None, list[str]]:
    """把模型输出 dict 转成 MatchExplanation（结构不合法返回 None）。"""
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        return None, ["解释缺少 summary"]

    highlights, d1 = _coerce_claims(payload.get("highlights"), "highlights")
    gaps, d2 = _coerce_claims(payload.get("gaps"), "gaps")
    risks, d3 = _coerce_claims(payload.get("risks"), "risks")

    suggestions = payload.get("interview_suggestions") or []
    if isinstance(suggestions, str):
        suggestions = [suggestions]
    suggestions = [str(item).strip() for item in suggestions if str(item).strip()][:10]

    pending = payload.get("pending_verification") or []
    if isinstance(pending, str):
        pending = [pending]

    explanation = MatchExplanation(
        summary=summary[:1000],
        highlights=highlights,
        gaps=gaps,
        risks=risks,
        interview_suggestions=suggestions,
        pending_verification=[str(item)[:200] for item in pending][:20] + d1 + d2 + d3,
        source=source,  # type: ignore[arg-type]
        prompt_version=prompt_version[:20],
    )
    return explanation, d1 + d2 + d3


# ------------------------------------------------------------------ 规则兜底


# *kwq* 新增：规则兜底解释（零模型、零推测）。
# 设计原因：外部通道全部不可用时，至少要给 HR 一份「事实清单」，
#          而不是空白或 500；所有内容都直接来自四态判定与证据，逐条挂 evidence_id。
def build_rule_explanation(pack: MatchEvidencePack) -> MatchExplanation:
    """用证据包拼装事实型解释（不含任何推断性能力描述）。"""
    passed = [item for item in pack.hard_results if item.status == HardStatus.PASS]
    failed = [item for item in pack.hard_results if item.status == HardStatus.FAIL]
    unknown = [item for item in pack.hard_results if item.status == HardStatus.UNKNOWN]
    review = [item for item in pack.hard_results if item.status == HardStatus.NEEDS_REVIEW]

    def _claim(text: str, evidence_id: str | None) -> list[ExplanationClaim]:
        if not evidence_id:
            return []
        return [ExplanationClaim(text=text[:500], evidence_ids=[evidence_id], claim_type="fact")]

    highlights: list[ExplanationClaim] = []
    for item in passed:
        highlights.extend(_claim(f"{item.item}：{item.reason}", item.evidence_id))

    gaps: list[ExplanationClaim] = []
    for item in failed + unknown:
        gaps.extend(_claim(f"{item.item}：{item.reason}", item.evidence_id))

    risks: list[ExplanationClaim] = []
    for item in review:
        risks.extend(_claim(f"{item.item}：{item.reason}", item.evidence_id))

    summary = (
        f"本次判定共 {len(pack.hard_results)} 项硬性条件："
        f"满足 {len(passed)} 项、不满足 {len(failed)} 项、"
        f"档案缺失 {len(unknown)} 项、需人工复核 {len(review)} 项；"
        f"档案完整度 {pack.data_completeness:.0%}。"
    )

    pending = [f"{item.item}：{item.reason}" for item in unknown + review]
    missing_fields = sorted(
        {item.source_field for item in pack.evidences if item.evidence_type == "missing"}
    )
    if missing_fields:
        pending.append("档案缺少字段：" + "、".join(missing_fields[:10]))

    suggestions = [
        f"请核实「{item.item}」：{item.reason}" for item in unknown + review
    ][:5]

    return MatchExplanation(
        summary=summary,
        highlights=highlights,
        gaps=gaps,
        risks=risks,
        interview_suggestions=suggestions,
        pending_verification=pending[:20],
        source="rule_fallback",
        prompt_version="rule-v1",
    )


# ------------------------------------------------------------------ 编排


class ExplanationService:
    """匹配解释编排器。"""

    def __init__(self, dify: DifyWorkflowAdapter | None = None) -> None:
        self._dify = dify

    # ---------------------------------------------------------------- 通道
    def _dify_configured(self) -> bool:
        if self._dify is None:
            # 步骤1：默认服务读取解释应用配置，确保正式入口真正启用适配器。
            return bool(settings.DIFY_BASE_URL and (settings.DIFY_EXPLANATION_API_KEY or settings.DIFY_API_KEY))
        return bool(getattr(self._dify, "configured", False))

    @staticmethod
    def _llm_configured() -> bool:
        """LLM 通道是否具备可用配置（Ollama 本地模型不需要 Key）。"""
        provider = str(getattr(settings, "LLM_PROVIDER", "") or "").lower()
        if provider == "ollama":
            return True
        return bool(getattr(settings, "LLM_API_KEY", ""))

    async def _via_dify(self, pack: MatchEvidencePack) -> tuple[MatchExplanation | None, list[str]]:
        """Dify 通道：送证据包，取结构化解释。"""
        # 步骤2：仅发送结构化证据；自建客户端按次关闭，注入对象归调用方管理。
        if self._dify is None:
            async with DifyWorkflowAdapter(workflow_key="match_explanation") as client:
                result = await client.run_match_explanation(pack)
        else:
            result = await self._dify.run_match_explanation(pack)
        payload = result.get("parsed") or result.get("outputs") or {}
        if isinstance(payload, str):
            payload = _extract_json(payload) or {}
        if not isinstance(payload, dict):
            return None, ["Dify 返回结构不是对象"]
        return _to_explanation(
            payload,
            source="dify",
            prompt_version=str(result.get("prompt_version") or "dify-v1"),
        )

    async def _via_llm(self, pack: MatchEvidencePack) -> tuple[MatchExplanation | None, list[str]]:
        """LLM 通道：把证据包交给平台 LLMManager（延迟导入，避免无 AI 依赖时启动失败）。"""
        from app.ai.core.llm_manager import LLMManager

        payload = pack.model_dump(mode="json")
        prompt = (
            "以下是岗位与候选人的结构化匹配证据包（JSON）。"
            "请仅依据其中的事实写解释，每条结论必须列出 evidence_ids。\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        result = await LLMManager().generate(
            prompt, system=LLM_SYSTEM_PROMPT, purpose="match_explanation"
        )
        content = getattr(result, "content", None) or getattr(result, "text", None) or ""
        data = _extract_json(content)
        if data is None:
            return None, ["LLM 输出无法解析为 JSON"]
        return _to_explanation(
            data,
            source="llm_fallback",
            prompt_version=str(getattr(result, "model", None) or "llm-v1")[:20] or "llm-v1",
        )

    # ---------------------------------------------------------------- 主入口
    # *kwq* 新增：生成 + 校验（唯一对外入口）。
    # 返回值中的 channel 是「真实使用的通道」，与 explanation.source 一致；
    # errors 记录降级原因，供日志与交付报告核对。
    async def generate(
        self, pack: MatchEvidencePack, *, use_external: bool = True
    ) -> ExplanationOutcome:
        """
        功能：选择解释通道并进行证据校验。
        输入：证据包、use_external开关。
        输出：ExplanationOutcome，含真实来源和降级信息。
        步骤：尝试Dify → 尝试LLM → 规则兜底；输出统一经过校验。
        """
        errors: list[str] = []

        if use_external and self._dify_configured():
            try:
                explanation, notes = await self._via_dify(pack)
                errors.extend(notes)
                if explanation is not None:
                    return ExplanationOutcome(
                        result=verify_explanation(explanation, pack),
                        channel="dify",
                        errors=errors,
                    )
            except DifyError as exc:
                errors.append(f"Dify 不可用：{exc.message}")
                logger.warning("explanation.dify_failed talent_id=%s err=%s", pack.talent_id, exc.message)

        if use_external and self._llm_configured():
            try:
                explanation, notes = await self._via_llm(pack)
                errors.extend(notes)
                if explanation is not None:
                    return ExplanationOutcome(
                        result=verify_explanation(explanation, pack),
                        channel="llm_fallback",
                        errors=errors,
                    )
            except Exception as exc:  # noqa: BLE001 - 任何模型异常都降级，不能让解释把主流程打挂
                errors.append(f"LLM 通道失败：{type(exc).__name__}: {exc}")
                logger.warning("explanation.llm_failed talent_id=%s err=%s", pack.talent_id, exc)

        explanation = build_rule_explanation(pack)
        return ExplanationOutcome(
            result=verify_explanation(explanation, pack),
            channel="rule_fallback",
            errors=errors,
        )


__all__ = [
    "ExplanationChannel",
    "ExplanationOutcome",
    "ExplanationService",
    "build_rule_explanation",
]
