"""
模块：app/ai/workflows/supervisor_workflow.py
职责：Agent 总路由（Supervisor）状态图 —— 把一次请求路由到正确的 Agent 并聚合结果。

标签：[Agent组] [P0]

============================ 实现思路与代码要点 ============================
为什么需要：
- 平台有 6 类 Agent（assistant/resume/talent/matching/assessment/training），
  用户消息到底由谁处理，必须有一个统一的「总路由 + 调度」入口（D14）；
- 路由决策要可观测（route_reason）、可降级（Agent 未注册 / LLM 失败 → 通用应答）、
  可重试（瞬时超时），这些控制流用 LangGraph 的条件边表达最自然。

设计（LangGraph 状态图，节点只调 Service/Skill/LLM，无裸 SQL）：
    START → decide(路由决策) ─条件边→ run_<agent> ×6 / fallback
                                        └→ collect(结果聚合) ─条件边→ decide（队列未尽/重试）
                                                                      └→ respond(终态组装) → END

路由策略 decide_route（同步纯规则，可单测）：
    1. 显式指定非 assistant/auto 的 agent_type → 强制该 Agent（reason=explicit）；
    2. 关键词命中（简历→resume、匹配/候选人→matching、测评→assessment、
       培训→training、人才/画像→talent）→ reason=keyword:*；
    3. settings.AGENT_ROUTE_LLM=true 且规则未命中 → LLM 意图分类（reason=llm，
       失败静默回退默认）；
    4. 默认 assistant（reason=default）。前端固定发 agent_type="assistant"，
       因此 assistant 视为「自动路由」而非强制。

降级与重试：
    - dispatch 抛 NotFoundError（Agent 未注册）：带 message 的对话请求降级
      通用 LLM 应答（result.degraded=True），结构化任务返回 ok=False(error_code=3001)；
    - dispatch 抛 AIError（如超时）：retries < 1 时回到 decide 重试一次，否则失败；
    - 其余 BusinessException / 未知异常 → status=failed + errors（不裸抛）。

要点：
1. 消息行落库由 AgentService 统一负责：dispatch 前在 AI 上下文打
   suppress_conversation_persist 标记，BaseAgent 内的 ConversationMemory
   持久化会跳过（见 conversation_repository.append_message），避免双写；
2. 多智能体协作：payload.pipeline=[...] 会在当前 Agent 完成后按序继续调度；
3. 图本身经 graph_builder 构建（无 langgraph 自动降级），每步断点由
   run_workflow 写入 state_manager，thread_id 建议用 chat:<uuid> / task:<id>。
========================================================================
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TypedDict

from app.ai.agent import AGENT_TYPES, dispatch
from app.ai.context import get_context, update_context
from app.ai.core.llm_manager import llm_manager
from app.adapters.llm.base_llm import ChatMessage
from app.ai.workflows.graph_builder import END, build_graph, run_workflow
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessException, NotFoundError

logger = logging.getLogger(__name__)

__all__ = [
    "ROUTE_KEYWORDS",
    "SupervisorState",
    "build_supervisor_graph",
    "decide_route",
    "run_supervisor",
]

# 瞬时类失败（超时）在总调度层最多再派发一次（Agent 内部重试由 BaseAgent 负责）
_SUPERVISOR_MAX_RETRIES = 1

# 关键词路由表：顺序即优先级（更具体的 Agent 在前）
ROUTE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("resume", ("简历", "履历", "resume", "解析简历")),
    ("matching", ("匹配", "人岗", "推荐候选", "候选人", "竞聘", "岗位要求", "matching")),
    ("assessment", ("测评", "考试", "评分", "评估", "assessment")),
    ("training", ("培训", "课程", "学习计划", "能力提升", "training")),
    ("talent", ("人才", "画像", "盘点", "档案", "talent")),
)

# prompt: supervisor-degraded-v1.0.0（降级通用应答；正式知识问答走 assistant Agent + 技能）
_DEGRADED_SYSTEM = (
    "你是企业人才数字化平台的智能助手。基于用户问题给出简洁、准确的中文回答；"
    "不确定的信息明确说明，不要编造具体数据（人名、分数、薪酬等）。"
)


class SupervisorState(TypedDict, total=False):
    """总路由状态（SupervisorState ⊃ WorkflowState 基线字段）。"""

    payload: dict[str, Any]        # 原始请求负载（message/history/conversation_id/...）
    agent: str                     # 当前目标 Agent
    queue: list[str]               # 多智能体协作：待执行 Agent 队列
    route_reason: str              # 路由依据：explicit / keyword:* / llm / default / ...
    result: dict[str, Any]         # 当前 Agent 的 dispatch 结果
    answers: list[dict[str, Any]]  # 各次执行结果（含重试尝试）
    answer: str                    # respond 节点组装的最终回答
    citations: list[dict[str, Any]]
    usage: dict[str, Any]
    degraded: bool                 # 是否降级通用应答
    errors: list[str]
    retries: int
    status: str                    # running / done / failed / retry
    started_at: float
    elapsed_ms: float


# ---------------------------------------------------------------- 路由决策
def decide_route(payload: dict[str, Any]) -> tuple[str, str]:
    """同步规则路由：返回 (agent_type, reason)。

    显式非 assistant/auto 的 agent_type 强制生效；assistant/auto 走
    关键词 → （LLM 由节点异步补充）→ 默认 assistant。
    """
    explicit = str(payload.get("agent_type") or "").strip().lower()
    if explicit in AGENT_TYPES and explicit not in ("assistant", "auto"):
        return explicit, "explicit"

    message = str(payload.get("message") or payload.get("query") or "").strip().lower()
    if message:
        for agent_type, keywords in ROUTE_KEYWORDS:
            for keyword in keywords:
                if keyword.lower() in message:
                    return agent_type, f"keyword:{keyword}"

    if explicit in AGENT_TYPES:
        return explicit, "default"
    return "assistant", "default"


async def _llm_route(message: str) -> str | None:
    """LLM 意图分类（AGENT_ROUTE_LLM=true 时启用；任何失败静默回退规则路由）。"""
    try:
        from app.adapters.llm.provider_config import provider_configured

        if not provider_configured():
            return None
        names = ", ".join(sorted(AGENT_TYPES))
        result = await llm_manager.chat(
            [
                ChatMessage(
                    role="system",
                    content=f"你是路由分类器。根据用户需求选择最合适的智能体，只输出名称之一：{names}。",
                ),
                ChatMessage(role="user", content=message),
            ],
            purpose="chat",
            user_id=get_context().user_id,
            trace_id=get_context().trace_id,
        )
        picked = (result.content or "").strip().strip("。.,，\"'").lower()
        return picked if picked in AGENT_TYPES else None
    except Exception as exc:  # noqa: BLE001 - 路由增强失败不影响主流程
        logger.warning("LLM 意图路由失败，回退规则路由：%s", exc)
        return None


# ---------------------------------------------------------------- 节点
async def node_decide(state: SupervisorState) -> dict[str, Any]:
    """路由决策：首次进入按规则/LLM 选 Agent；重入处理协作队列与重试。"""
    payload = dict(state.get("payload") or {})

    if "queue" in state:  # 重入（协作队列推进 / 失败重试）
        queue = list(state.get("queue") or [])
        if queue:
            agent_type = queue.pop(0)
            return {"agent": agent_type, "queue": queue, "status": "running"}
        return {}  # 队列空的重入 = 重试同一 Agent，保持原 agent/status

    agent_type, reason = decide_route(payload)
    message = str(payload.get("message") or payload.get("query") or "").strip()
    if reason == "default" and settings.AGENT_ROUTE_LLM and message:
        picked = await _llm_route(message)
        if picked:
            agent_type, reason = picked, "llm"

    pipeline = [a for a in (payload.get("pipeline") or []) if a in AGENT_TYPES and a != agent_type]
    return {
        "agent": agent_type,
        "queue": pipeline,
        "route_reason": reason,
        "status": "running",
        "started_at": time.perf_counter(),
    }


def route_to_agent(state: SupervisorState) -> str:
    """decide 的条件边：按 agent 选择执行节点，未知值降级 fallback。"""
    agent_type = str(state.get("agent") or "")
    return agent_type if agent_type in AGENT_TYPES else "fallback"


def _extract_answer(result: dict[str, Any]) -> str:
    """从 dispatch 结果中提取可读回答（兼容各 Agent 的 result 结构）。"""
    if not result.get("ok"):
        return ""
    inner = result.get("result")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, dict):
        for key in ("answer", "content", "text", "summary"):
            value = inner.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(inner, ensure_ascii=False, default=str)
    answer = result.get("answer")
    return str(answer) if answer else ""


def _extract_usage(result: dict[str, Any]) -> dict[str, Any]:
    """归一 token 用量（兼容 prompt/completion 与 prompt_tokens/completion_tokens 两种键）。"""
    tokens: dict[str, Any] = dict(result.get("tokens") or {})
    inner = result.get("result")
    if isinstance(inner, dict) and isinstance(inner.get("tokens"), dict):
        tokens = {**inner["tokens"], **tokens}
    return {
        "prompt_tokens": int(tokens.get("prompt", tokens.get("prompt_tokens", 0)) or 0),
        "completion_tokens": int(tokens.get("completion", tokens.get("completion_tokens", 0)) or 0),
        "model": str(tokens.get("model") or result.get("model") or ""),
    }


async def _fallback_answer(state: SupervisorState, *, note: str) -> dict[str, Any]:
    """降级通用应答：Agent 不可用时用基础 LLM 回答对话类请求。"""
    payload = dict(state.get("payload") or {})
    message = str(payload.get("message") or payload.get("query") or "").strip()
    if not message:
        return {
            "status": "failed",
            "errors": [*(state.get("errors") or []), f"{note}：无可用 Agent 且缺少 message"],
            "result": {"ok": False, "error": "无可用 Agent 且缺少 message", "error_code": 1001},
        }
    try:
        ctx = get_context()
        history = list(payload.get("history") or [])
        messages: list[ChatMessage] = [ChatMessage(role="system", content=_DEGRADED_SYSTEM)]
        messages.extend(ChatMessage.from_any(item) for item in history)
        if not any(m.role == "user" and m.content == message for m in messages[-3:]):
            messages.append(ChatMessage(role="user", content=message))
        result = await llm_manager.chat(
            messages,
            purpose="chat",
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            trace_id=ctx.trace_id,
        )
    except Exception as exc:  # noqa: BLE001 - LLM 不可用 → 结构化业务错误
        logger.warning("降级通用应答失败（%s）：%s", note, exc)
        return {
            "status": "failed",
            "errors": [*(state.get("errors") or []), str(exc)],
            "result": {"ok": False, "error": str(exc), "error_code": 5002},
        }
    return {
        "status": "done",
        "degraded": True,
        "result": {
            "ok": True,
            "degraded": True,
            "answer": result.content,
            "tokens": {
                "prompt": result.prompt_tokens,
                "completion": result.completion_tokens,
                "model": result.model,
            },
        },
    }


def _make_run_node(agent_type: str, db: Any, user: Any) -> Any:
    """构造 run_<agent> 节点：唯一调度入口 dispatch + 异常分类（重试/降级/失败）。"""

    async def node(state: SupervisorState) -> dict[str, Any]:
        payload = dict(state.get("payload") or {})
        payload["agent"] = agent_type
        retries = int(state.get("retries") or 0)

        # 消息行由 AgentService 统一落库：dispatch 期间抑制 BaseAgent 的 ConversationMemory 持久化
        prev_extra = dict(get_context().extra or {})
        had_key = "suppress_conversation_persist" in prev_extra
        update_context(extra={**prev_extra, "suppress_conversation_persist": True})
        try:
            try:
                result = await dispatch(agent_type, payload, db=db, user=user, context=None)
            except NotFoundError as exc:
                # Agent 未注册：对话请求降级通用应答，结构化任务返回业务错误
                if str(payload.get("message") or "").strip():
                    return await _fallback_answer(state, note=f"agent_not_registered:{agent_type}")
                return {
                    "status": "failed",
                    "errors": [*(state.get("errors") or []), str(exc.message)],
                    "result": {"ok": False, "error": exc.message, "error_code": exc.code},
                }
            except AIError as exc:  # 超时等瞬时错误：调度层最多重试一次
                errors = [*(state.get("errors") or []), str(exc.message)]
                if retries < _SUPERVISOR_MAX_RETRIES:
                    return {"retries": retries + 1, "status": "retry", "errors": errors}
                return {
                    "status": "failed",
                    "errors": errors,
                    "result": {"ok": False, "error": exc.message, "error_code": exc.code},
                }
            except BusinessException as exc:
                return {
                    "status": "failed",
                    "errors": [*(state.get("errors") or []), str(exc.message)],
                    "result": {"ok": False, "error": exc.message, "error_code": exc.code},
                }
            except Exception as exc:  # noqa: BLE001 - 未知异常折叠为业务失败
                logger.exception("Agent %s 执行出现未预期异常", agent_type)
                return {
                    "status": "failed",
                    "errors": [*(state.get("errors") or []), str(exc)],
                    "result": {"ok": False, "error": str(exc), "error_code": 5000},
                }
        finally:
            now_extra = dict(get_context().extra or {})
            if had_key:
                now_extra["suppress_conversation_persist"] = prev_extra["suppress_conversation_persist"]
            else:
                now_extra.pop("suppress_conversation_persist", None)
            update_context(extra=now_extra)

        if result.get("ok"):
            return {"result": result, "status": "done"}
        return {
            "status": "failed",
            "errors": [*(state.get("errors") or []), str(result.get("error") or "Agent 执行失败")],
            "result": result,
        }

    return node


async def node_fallback(state: SupervisorState) -> dict[str, Any]:
    """条件边兜底节点：路由到未知 Agent 时降级通用应答。"""
    return await _fallback_answer(state, note="route_fallback")


async def node_collect(state: SupervisorState) -> dict[str, Any]:
    """聚合当前执行结果（保留重试尝试记录），决定继续/失败。"""
    result = dict(state.get("result") or {})
    errors = list(state.get("errors") or [])
    answers = list(state.get("answers") or [])
    answers.append({"agent": state.get("agent"), "ok": bool(result.get("ok")), "result": result})
    error_text = str(result.get("error") or "")
    if not result.get("ok") and error_text and error_text not in errors:
        errors.append(error_text)

    status = state.get("status")
    if status in ("retry", "failed"):
        return {"answers": answers, "errors": errors, "status": status}
    return {"answers": answers, "errors": errors, "status": "running"}


def route_next(state: SupervisorState) -> str:
    """collect 的条件边：重试/队列未尽 → 回 decide；否则 → respond。"""
    if state.get("status") == "retry":
        return "route"
    if state.get("status") == "failed":
        return "respond"
    if state.get("queue"):
        return "route"
    return "respond"


async def node_respond(state: SupervisorState) -> dict[str, Any]:
    """终态组装：从最后一次执行结果提取回答 / 引用 / 用量，得出最终 status。"""
    answers = list(state.get("answers") or [])
    result = dict((answers[-1].get("result") if answers else {}) or {})
    errors = list(state.get("errors") or [])
    any_ok = any(bool(item.get("ok")) for item in answers)
    status = "done" if any_ok else "failed"
    if status == "failed" and not errors:
        errors = ["Agent 执行失败"]
    started_at = state.get("started_at")
    elapsed = (time.perf_counter() - started_at) * 1000 if started_at else 0.0
    inner = result.get("result")
    citations = list(
        result.get("citations")
        or (inner.get("citations") if isinstance(inner, dict) else None)
        or []
    )
    return {
        "status": status,
        "answer": _extract_answer(result),
        "citations": citations,
        "usage": _extract_usage(result),
        "degraded": bool(result.get("degraded")),
        "errors": errors,
        "elapsed_ms": round(elapsed, 2),
    }


# ---------------------------------------------------------------- 图装配
def build_supervisor_graph(
    *,
    db: Any = None,
    user: Any = None,
    use_checkpoint: bool = False,
    mode: str = "full",
) -> Any:
    """装配 Supervisor 状态图（经 graph_builder，langgraph 缺失自动降级）。

    :param mode: "full" = decide → agent → collect → respond（完整执行）；
                 "route" = 只跑 decide（流式入口先拿路由结果，再走 dispatch_stream）
    :return: 可执行图（ainvoke 接口）
    """
    if mode not in ("full", "route"):
        raise ValueError(f"未知 mode：{mode}（可选 full/route）")

    nodes: dict[str, Any] = {
        "decide": node_decide,
        "fallback": node_fallback,
        "collect": node_collect,
        "respond": node_respond,
    }
    for agent_type in AGENT_TYPES:
        nodes[f"run_{agent_type}"] = _make_run_node(agent_type, db, user)

    agent_map = {agent_type: f"run_{agent_type}" for agent_type in AGENT_TYPES}
    if mode == "route":
        decision_map = {**{a: END for a in AGENT_TYPES}, "fallback": END}
        conditional_edges: list[tuple[str, Any, dict[str, str]]] = [
            ("decide", route_to_agent, decision_map)
        ]
        edges: list[tuple[str, str]] = []
    else:
        conditional_edges = [
            ("decide", route_to_agent, {**agent_map, "fallback": "fallback"}),
            ("collect", route_next, {"route": "decide", "respond": "respond"}),
        ]
        edges = [(f"run_{a}", "collect") for a in AGENT_TYPES] + [("respond", END)]

    return build_graph(
        nodes=nodes,
        edges=edges,
        entry_point="decide",
        conditional_edges=conditional_edges,
        use_checkpoint=use_checkpoint,
        state_schema=SupervisorState,
    )


async def run_supervisor(
    payload: dict[str, Any],
    *,
    db: Any = None,
    user: Any = None,
    thread_id: str | None = None,
    mode: str = "full",
    use_checkpoint: bool = False,
) -> dict[str, Any]:
    """执行一次总路由：异常折叠进返回状态（status=failed + errors），不裸抛。"""
    graph = build_supervisor_graph(db=db, user=user, use_checkpoint=use_checkpoint, mode=mode)
    initial: dict[str, Any] = {
        "payload": dict(payload or {}),
        "errors": [],
        "retries": 0,
        "status": "running",
    }
    return await run_workflow(graph, initial, thread_id=thread_id)
