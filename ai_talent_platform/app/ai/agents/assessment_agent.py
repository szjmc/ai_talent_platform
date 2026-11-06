"""
标签：[Agent组] [P0]
模块：app/ai/agents/assessment_agent.py
职责：人才测评分析 Agent（LangGraph 多节点有状态调度）。

============================ 架构定位 ============================
隶属 AI Agent 多智能体系统（LangGraph 核心落地），是 6 大业务 Agent 之一。
执行管线（与架构师设计的节点表一致）：

    assign  ->  dispatch  ->  monitor  ->  stat  ->  score  ->  analyze  ->  report
    组卷       下发+推送     答题监控       规则统计    语义打分     短板研判     报告
    (Dify)    本地           本地           本地         (Dify)      (Dify)      本地LLM

【依赖说明】
- assign 读取数据库试卷；score / analyze 调用已配置的 Dify，失败明确报错；
- report 的自然语言小结：用平台统一 llm_manager，默认走硅基流动线上大模型；
- 落库：persist() 写 assessment_results；保存失败返回结构化错误。

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
    NODE_REGISTRY,
    AssessmentState,
)
from app.ai.utils.output_parser import parse_json
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessException, ParamError
from app.services.talent_access import actor_context

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
        actor_context(self.user, self.context).require_permission("assessment:manage")
        if payload.get("stage", "full") not in ("assign", "full"):
            raise ParamError("stage 仅支持 assign 或 full")
        assessment_id = int(payload.get("assessment_id") or 0)
        if assessment_id <= 0 or self.db is None:
            raise ParamError("测评分析需要有效的 assessment_id 和数据库会话")

        # 1) 初始化状态；业务事实只能由 Service 查库补充
        state: AssessmentState = {
            "assessment_id": assessment_id,
            "talent_id": int(payload.get("talent_id") or 0),
            "position_id": payload.get("position_id"),
            "job_level": payload.get("job_level", "中级"),
            "profession": payload.get("profession", "数字化"),
            "grade": payload.get("grade", "中级"),
            "stage": payload.get("stage", "full"),
            "answers": [],  # 评分事实只从数据库答题快照装载
            "errors": [],
            "retries": 0,
            "status": "init",
            "started_at": "",
            "finished_at": "",
        }

        try:
            from app.services.assessment_service import AssessmentService

            service = AssessmentService(self.db)
            # 2) assign 只下发已有试卷；full 从已提交答案评分，绝不重新组卷或重复发信。
            if state["stage"] == "assign":
                paper = await service.start_assessment(assessment_id)
                row = await service.repo.get_by_id_or_404(self.db, assessment_id)
                if payload.get("talent_id") is not None and int(payload["talent_id"]) != row.talent_id:
                    raise ParamError("talent_id 与数据库测评记录不一致")
                state.update({"talent_id": row.talent_id, "position_id": row.position_id,
                              "questions": paper["questions"], "email": row.talent.email or "",
                              "candidate_name": row.talent.name})
                executed = ["assign", "dispatch"]
            else:
                state.update(await service.load_analysis_state(assessment_id))
                if payload.get("talent_id") is not None and int(payload["talent_id"]) != state["talent_id"]:
                    raise ParamError("talent_id 与数据库测评记录不一致")
                executed = ["stat", "score", "analyze", "report"]

            # 3) 顺序执行节点（等价于 LangGraph 线性图；接入 graph_builder 后替换为 run_workflow）
            for node_name in executed:
                node_fn = NODE_REGISTRY[node_name]
                logger.info("[assessment] 执行节点 %s", node_name)
                delta = await node_fn(state)
                state.update(delta)

            # 4) 小结只陈述已计算的数据；外部润色不可用时使用确定性说明。
            if state["stage"] != "assign":
                await self._enrich_summary_with_llm(state)
                state.setdefault("model_name", "dify")
                state["prompt_version"] = self.prompt_version

            # 5) 评分结果必须成功落库；保存失败不能返回成功报告。
            persisted = None
            if self.persist_result and state["stage"] != "assign":
                persisted = await self.persist(dict(state), payload)

            dispatch_failed = state.get("status") == "assign_failed"
            return {
                "ok": not dispatch_failed,
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
                    "email_result": state.get("email_result"),
                },
                "persisted": persisted,
                "nodes_executed": executed,
                "model": state.get("model_name") or settings.LLM_MODEL,
                "prompt_version": self.prompt_version,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "attempts": 1,
                **({"error": "试卷已就绪，但邮件未发送", "error_code": 5001} if dispatch_failed else {}),
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
        """写回 assessment_results（1:1，失败向上抛出）。

        - 走 AssessmentService 真落库，并把测评状态推进到 scored、回填总分。
        """
        if self.db is None:
            raise AIError("缺少数据库会话，测评结果未保存")
        from app.services.assessment_service import AssessmentService

        await AssessmentService(self.db).save_ai_evaluation(result["assessment_id"], result)
        return {"assessment_id": result["assessment_id"], "persisted": True}


__all__ = ["AssessmentAgent"]
