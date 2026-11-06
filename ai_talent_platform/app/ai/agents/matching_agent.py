"""
模块：app/ai/agents/matching_agent.py
职责：人岗匹配 Agent（跑批 + 自然语言总结）。

标签：[Agent组] [P1]  依赖：[架构组] BaseAgent + matching_score 技能（人岗匹配解释）

============================ 实现思路与代码要点 ============================
执行流程（与设计稿的分工差异，为什么这样分）：

    接收 position_id + 候选人才列表
      -> MatchingService.run()（确定性链路：硬性条件四态判定 -> 加权打分 ->
         证据构建 -> 解释 -> 落库，权重可配）
      -> 本 Agent 只负责「表达」：把结构化结果交给 LLM 生成 HR 可读的总结
      -> 解析 LLM 输出为 {"summary": ...}，随跑批结果一起返回

分工红线（与 MATCH-005/007 一致）：
- 硬性判定、分数、排序全部由确定性引擎产出，LLM 不得重算或改写；
- LLM 只允许引用给定事实，禁止编造（prompt 红线 + 上游证据校验双保险）；
- 跑批落库由 MatchingService 完成（persist_result=False，Agent 不重复写库）。

注意事项：
- 输入经 MatchRunRequest 校验（schemas/matching.py）；
- 提示词版本化：prompt_config.PROMPT_VERSION，模板 "matching_run_summary"；
- 数据访问只通过 MatchingService，不直连数据库。
实现说明：*kwq* 实现（原 TODO 骨架）。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.llm.base_llm import ChatMessage
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.ai.utils.output_parser import parse_json
from app.schemas.matching import MatchRunRequest
from app.services.matching_service import MatchingService

logger = logging.getLogger(__name__)


@register_agent("matching")
class MatchingAgent(BaseAgent):
    """人岗匹配 Agent：确定性跑批 + LLM 自然语言总结。"""

    name = "matching"
    purpose = "matching"
    prompt_name = "matching_run_summary"
    temperature = 0.2
    max_tokens = 800
    # 跑批本身已由 MatchingService 落库，Agent 只做总结，不重复写库
    persist_result = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # build_prompt 与 parse_output 之间传递跑批结果（同一次 run() 调用内）
        self._run_payload: dict[str, Any] = {}

    # ---------------------------------------------------------------- 数据与提示
    async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]:
        """跑一次真实匹配（确定性引擎），把结构化事实渲染进提示词。"""
        from app.ai.prompts.matching_prompt import render_run_summary

        run_req = MatchRunRequest.model_validate(
            {
                "position_id": payload.get("position_id"),
                "talent_ids": payload.get("talent_ids"),
                "top_k": payload.get("top_k", 10),
                "min_score": payload.get("min_score", 0.0),
                "weights": payload.get("weights"),
                # Agent 场景默认走规则解释：总结阶段另有 LLM 调用，避免双重外部调用；
                # 调用方显式传 with_explanation=True 时保留外部解释通道。
                "with_explanation": bool(payload.get("with_explanation", False)),
            }
        )

        service = MatchingService(self.db)
        run_response = await service.run(run_req, current_user_id=self.context.user_id)
        self._run_payload = run_response.model_dump(mode="json")

        # 只给 LLM 必要字段（控制上下文长度；证据明细不进提示词）
        results = [
            {
                "rank": item["rank"],
                "talent_id": item["talent_id"],
                "talent_name": item.get("talent_name"),
                "decision": item.get("decision"),
                "score": item.get("score"),
                "dimension_scores": item.get("dimension_scores"),
                "matched_skills": item.get("matched_skills"),
                "highlights": item.get("highlights"),
                "gaps": item.get("gaps"),
                "explanation_source": item.get("explanation_source"),
            }
            for item in self._run_payload.get("items", [])
        ]
        user_text = render_run_summary(
            requirement_version=self._run_payload.get("requirement_version", 1),
            stats=self._run_payload.get("stats", {}),
            results=results,
        )
        return [ChatMessage.user(user_text)]

    # ---------------------------------------------------------------- 解析
    async def parse_output(self, raw: str) -> dict[str, Any]:
        """解析 LLM 输出：优先 JSON {"summary": ...}，否则把全文当作 summary。"""
        parsed = parse_json(raw)
        summary = ""
        if isinstance(parsed, dict):
            summary = str(parsed.get("summary") or "").strip()
        if not summary:
            summary = (raw or "").strip()
        if not summary:
            raise ValueError("模型未返回任何总结内容")

        return {
            "summary": summary,
            "batch_id": self._run_payload.get("batch_id"),
            "position_id": self._run_payload.get("position_id"),
            "requirement_version": self._run_payload.get("requirement_version"),
            "recall_mode": self._run_payload.get("recall_mode"),
            "stats": self._run_payload.get("stats"),
            "weights_used": self._run_payload.get("weights_used"),
            "items": self._run_payload.get("items"),
            "excluded": self._run_payload.get("excluded"),
        }


__all__ = ["MatchingAgent"]
