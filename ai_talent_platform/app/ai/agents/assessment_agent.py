"""
模块：app/ai/agents/assessment_agent.py
职责：人才测评分析 Agent（LangGraph 多节点有状态调度）。

============================ 架构定位 ============================
隶属 AI Agent 多智能体系统（LangGraph 核心落地），是 6 大业务 Agent 之一。
执行管线（与架构师设计的节点表一致）：

    assign  ->  dispatch  ->  monitor  ->  stat  ->  score  ->  analyze  ->  report
    组卷       下发+推送     答题监控       规则统计    语义打分     短板研判     报告
    (Dify)    本地           本地           本地         (Dify)      (Dify)      本地LLM

【依赖说明】
- assign / score / analyze：调用 Dify 组同事的工作流（尚未交付），
  当前走 assessment_dify_client 里的测试数据，交付后改一个开关即可切真实接口；
- report 的自然语言小结：用平台统一 llm_manager，默认走硅基流动线上大模型；
- 落库：persist() 写 assessment_results（best-effort，无 DB 时仅返回结果字典）。

【与 LangGraph 的衔接】
本模块把每个节点拆成纯函数（见 assessment_nodes.NODE_REGISTRY + EDGES_FULL）。
待 AG-A 的 graph_builder 就绪后，一行即可升级为真正的 StateGraph：
    graph = build_graph(NODE_REGISTRY, EDGES_FULL, entry_point="assign")
    final = await run_workflow(graph, initial_state)
当前 run() 用顺序执行跑通同一套节点，逻辑等价、可单测。
=========================================================================
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.adapters.llm.base_llm import ChatMessage
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.ai.agents.assessment_nodes import (
    EDGES_FULL,
    NODE_REGISTRY,
    AssessmentState,
)
from app.ai.utils.output_parser import parse_json
from app.config.settings import settings
from app.core.exceptions import BusinessException

logger = logging.getLogger(__name__)


@register_agent("assessment")
class AssessmentAgent(BaseAgent):
    """人才测评分析 Agent。"""

    name = "assessment"
    purpose = "assessment"          # model_selector 按此选模型（硅基流动）
    prompt_name = "assessment"
    temperature = 0.2                # 结构化研判，低温
    max_tokens = 1200
    persist_result = True

    system_prompt = (
        "你是资深人才能力测评分析师。请基于给定的测评数据，输出一段不超过 300 字的"
        "自然语言评估结论：包括总体表现、核心优势、主要短板、岗位适配判断与发展建议。"
        "只输出结论正文，不要分点、不要 JSON。"
    )

    # ------------------------------------------------------------------ 主流程编排
    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """覆盖基类 run：测评是 7 节点管线，而非单次 LLM 调用。"""
        payload = payload or {}
        started = time.perf_counter()

        # 1) 初始化 LangGraph 状态
        state: AssessmentState = {
            "assessment_id": int(payload.get("assessment_id", 0)),
            "talent_id": int(payload.get("talent_id", 0)),
            "position_id": payload.get("position_id"),
            "job_level": payload.get("job_level", "中级"),
            "profession": payload.get("profession", "数字化"),
            "grade": payload.get("grade", "中级"),
            "stage": payload.get("stage", "full"),
            "answers": payload.get("answers", []),
            "errors": [],
            "retries": 0,
            "status": "init",
            "started_at": "",
            "finished_at": "",
        }

        try:
            # 2) 选择执行路径：assign 阶段只组卷+下发；full 阶段跑完整 7 节点
            if state["stage"] == "assign":
                executed = ["assign", "dispatch"]
            else:
                executed = [src for src, _ in EDGES_FULL] + ["report"]
                executed = list(dict.fromkeys(executed))  # 去重保序

            # 3) 顺序执行节点（等价于 LangGraph 线性图；接入 graph_builder 后替换为 run_workflow）
            for node_name in executed:
                node_fn = NODE_REGISTRY[node_name]
                logger.info("[assessment] 执行节点 %s", node_name)
                delta = await node_fn(state)
                state.update(delta)

            # 4) report 节点后，用本地 LLM（硅基流动）生成自然语言小结
            await self._enrich_summary_with_llm(state)

            # 5) 落库（best-effort）
            persisted = None
            if self.persist_result:
                persisted = await self.persist(dict(state), payload)

            return {
                "ok": True,
                "agent": self.name,
                "result": {
                    "assessment_id": state["assessment_id"],
                    "paper_id": state.get("paper_id"),
                    "status": state.get("status"),
                    "total_score": state.get("total_score"),
                    "level": state.get("level"),
                    "dimension_scores": state.get("dimension_scores"),
                    "llm_scores": state.get("llm_scores"),
                    "weakness": state.get("weakness"),
                    "suggestions": state.get("suggestions"),
                    "report": state.get("report"),
                },
                "persisted": persisted,
                "nodes_executed": executed,
                "model": state.get("model_name") or settings.LLM_MODEL,
                "prompt_version": self.prompt_version,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "attempts": 1,
            }
        except BusinessException as exc:
            return self._error_result(exc.message, code=exc.code, elapsed_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            logger.exception("测评 Agent 执行失败")
            return self._error_result(
                f"测评分析失败：{exc}", code=5002, elapsed_ms=(time.perf_counter() - started) * 1000
            )

    # ------------------------------------------------------------------ 本地 LLM 小结
    async def _enrich_summary_with_llm(self, state: AssessmentState) -> None:
        """report 节点后：用硅基流动线上模型把结构化结果润色成自然语言小结。"""
        from app.ai.core.llm_manager import llm_manager

        report = state.get("report") or {}
        if report.get("summary"):
            return
        try:
            messages = await self.build_prompt(dict(state))
            messages = self._with_system_prompt(messages)
            result = await llm_manager.chat(
                messages,
                purpose=self.purpose,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                user_id=self.context.user_id,
                trace_id=self.context.trace_id,
            )
            report["summary"] = (result.content or "").strip()
            state["model_name"] = result.model
            state["prompt_version"] = self.prompt_version
            state["report"] = report
        except Exception as exc:  # noqa: BLE001 - LLM 小结失败不阻塞主流程，用结构化兜底
            logger.warning("LLM 自然语言小结失败，使用结构化兜底：%s", exc)
            report["summary"] = (
                f"综合得分 {state.get('total_score', 0)}，等级 {state.get('level', '中级')}；"
                f"主要短板：{('、'.join(w.get('dimension','') for w in (state.get('weakness') or [])) or '无显著短板')}。"
            )
            state["report"] = report

    # ------------------------------------------------------------------ BaseAgent 契约
    async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]:
        """取数据 + 组装提示词（这里把各节点产物序列化成上下文）。"""
        report = payload.get("report") or {}
        context = {
            "岗位层级": payload.get("job_level"),
            "专业方向": payload.get("profession"),
            "规则总分": payload.get("rule_total_score"),
            "综合/专业/适配分": payload.get("llm_scores"),
            "能力结构": payload.get("dimension_rule_scores"),
            "短板": [w.get("dimension") for w in (payload.get("weakness") or [])],
            "短板研判依据": payload.get("score_rationale"),
        }
        return [ChatMessage.user(f"测评数据：{context}\n请输出个性化能力评估结论与提升建议。")]

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """解析模型输出（自然语言小结场景直接取文本；结构化场景用 parse_json 容错）。"""
        try:
            return parse_json(raw)
        except Exception:  # noqa: BLE001 - 自然语言输出，按纯文本处理
            return {"summary": raw.strip()}

    async def persist(self, result: dict[str, Any], payload: dict[str, Any]) -> Any:
        """写回 assessment_results（1:1，best-effort）。

        - 注入了 db（生产/接口调用）：走 AssessmentService -> Repository.save_result() 真落库，
          并把测评状态推进到 scored、回填总分；
        - 无 db（离线 demo）：仅返回结果字典，不报错。
        """
        report = result.get("report") or {}
        if self.db is None:
            return {
                "assessment_id": result.get("assessment_id"),
                "total_score": result.get("total_score"),
                "level": result.get("level"),
                "persisted": False,
            }

        try:
            from app.services.assessment_service import AssessmentService

            service = AssessmentService(self.db)
            saved = await service.repo.save_result(
                self.db,
                assessment_id=result["assessment_id"],
                total_score=result.get("total_score"),
                dimension_scores=result.get("dimension_scores"),
                level=result.get("level"),
                report=report,
                suggestions=result.get("suggestions"),
                model_name=result.get("model_name"),
                prompt_version=self.prompt_version,
            )
            # 测评状态推进到 scored + 回填总分
            await service.repo.update_status(
                self.db, result["assessment_id"], "scored",
                total_score=result.get("total_score"),
            )
            return {"assessment_id": saved.assessment_id, "persisted": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("测评结果落库失败（不影响返回）：%s", exc)
            return {"persisted": False, "error": str(exc)}


__all__ = ["AssessmentAgent"]
