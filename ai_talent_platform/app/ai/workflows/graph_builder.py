"""
模块：app/ai/workflows/graph_builder.py
职责：工作流状态图构建器（LangGraph 封装 + 无 langgraph 时的内置轻量执行器）。

标签：[Agent组] [P0]

============================ 实现思路与代码要点 ============================
为什么需要：
- LangGraph 提供声明式图编排（节点 / 边 / 条件路由 / checkpointer），
  但业务代码不应直接依赖 langgraph 的 API 细节，也不应在缺依赖时整体不可用；
- 离线单测、精简部署环境里可能没有 langgraph，状态图必须能降级执行。

设计：
    WorkflowState(payload/data/errors/retries/status)  # 工作流状态契约
    build_graph(nodes, edges, entry_point, conditional_edges, use_checkpoint)
        → langgraph StateGraph.compile(...)；langgraph 缺失或 force_fallback=True
          时返回 _FallbackGraph（同接口 ainvoke，线性边 + 条件边，逐步执行）
    run_workflow(graph, initial_state, thread_id=None, resume=False)
        → 异常统一写入 state["errors"] 并置 status="failed"（不裸抛）；
          每步（含失败步）自动 state_manager.save(thread_id, state) 留断点，
          运行结束再整体保存一次，支持断点续跑（resume=True）
    route_by_status(state) -> str   # 按 status 分流：done / failed / retry / need_human

要点：
1. 节点只做「业务调用 + 返回状态增量」，异常由 _wrap_node 兜底捕获写入 errors，
   是否重试由条件边（路由函数）决定，不在此处吞掉业务错误语义；
2. 断点保存通过 ContextVar 传递 thread_id（节点签名保持 (state) -> dict 不变）；
3. 死循环保护：langgraph 路径用 recursion_limit（由 AGENT_MAX_STEPS 推导），
   内置执行器用 AGENT_MAX_STEPS 硬步数上限；
4. 节点名不得与状态键同名（langgraph 会拒绝，如状态键 route → 节点名 decide）。
========================================================================
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypedDict

from app.ai.memory.state_manager import state_manager
from app.config.settings import settings

logger = logging.getLogger(__name__)

try:  # langgraph 可选依赖：缺失时自动降级到内置执行器
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - 精简环境
    START = "__start__"
    END = "__end__"
    MemorySaver = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False

__all__ = [
    "END",
    "LANGGRAPH_AVAILABLE",
    "START",
    "WorkflowState",
    "build_graph",
    "route_by_status",
    "run_workflow",
]

# 当前 run 的 thread_id（run_workflow 注入，节点包装器读取做断点保存）
_RUN_THREAD: ContextVar[str | None] = ContextVar("graph_run_thread", default=None)


class WorkflowState(TypedDict, total=False):
    """工作流状态契约（LangGraph StateGraph 的 schema）。"""

    payload: dict[str, Any]        # 输入负载
    data: dict[str, Any]           # 结构化结果
    errors: list[str]              # 异常描述（节点内部捕获，非裸抛）
    retries: int                   # 重试计数
    status: str                    # running / done / failed / retry / need_human


# ---------------------------------------------------------------- 基础工具
def _with_defaults(state: dict[str, Any] | None) -> dict[str, Any]:
    """补齐状态基线字段，保证路由函数总是能读到 keys。"""
    result = dict(state or {})
    result.setdefault("payload", {})
    result.setdefault("data", {})
    result.setdefault("errors", [])
    result.setdefault("retries", 0)
    result.setdefault("status", "running")
    return result


async def _save_breakpoint(thread_id: str | None, state: dict[str, Any]) -> None:
    """断点保存：失败也只告警，不影响工作流执行。"""
    if not thread_id:
        return
    try:
        await state_manager.save(thread_id, state)
    except Exception as exc:  # noqa: BLE001 - 持久化失败不能打断业务
        logger.warning("工作流断点保存失败（thread_id=%s）：%s", thread_id, exc)


def _wrap_node(fn: Callable[..., Any]) -> Callable[..., Any]:
    """节点包装：统一异常兜底 + 每步断点保存（async / sync 节点都支持）。"""
    if getattr(fn, "_graph_wrapped", False):
        return fn
    name = getattr(fn, "__name__", "node")

    async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        try:
            delta = fn(state)
            if inspect.isawaitable(delta):
                delta = await delta
        except Exception as exc:  # noqa: BLE001 - 写入 errors，由条件边决定重试/结束
            logger.exception("状态图节点执行失败：%s", name)
            delta = {
                "errors": [*(state.get("errors") or []), f"{name}: {exc}"],
                "status": "failed",
            }
        if delta is None:
            delta = {}
        if not isinstance(delta, dict):
            logger.warning("状态图节点 %s 返回非 dict（已忽略）：%r", name, type(delta))
            delta = {}
        merged = {**state, **delta}
        await _save_breakpoint(_RUN_THREAD.get(), merged)
        return delta

    wrapped._graph_wrapped = True  # type: ignore[attr-defined]
    return wrapped


async def _call_router(router: Callable[..., Any], state: dict[str, Any]) -> str:
    """同步 / 异步路由函数统一调用。"""
    key = router(state)
    if inspect.isawaitable(key):
        key = await key
    return str(key)


# ---------------------------------------------------------------- 内置降级执行器
class _FallbackGraph:
    """langgraph 缺失时的轻量状态图执行器（线性边 + 条件边，接口兼容 ainvoke）。"""

    def __init__(
        self,
        nodes: dict[str, Callable[..., Any]],
        edges: list[tuple[str, str]],
        entry_point: str,
        conditional_edges: list[tuple[str, Callable[..., str], dict[str, str]]],
        max_steps: int,
        use_checkpoint: bool = False,
    ) -> None:
        self._nodes = {name: _wrap_node(fn) for name, fn in nodes.items()}
        self._edges = list(edges)
        self._entry = entry_point
        self._conditional = [(src, router, dict(mapping or {})) for src, router, mapping in conditional_edges]
        self._max_steps = max(1, int(max_steps))
        self.use_checkpoint = use_checkpoint
        self.checkpointer = None  # 与 langgraph CompiledGraph 对齐（降级路径无 checkpointer）

    async def ainvoke(
        self,
        state: dict[str, Any] | None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        if state is None:  # 断点续跑：读取上次保存的状态直接返回
            if thread_id:
                loaded = await state_manager.load(thread_id)
                return dict(loaded or {})
            state = {}
        current_state = _with_defaults(state)
        await _save_breakpoint(thread_id, current_state)

        current: str | None = self._entry
        steps = 0
        while current and current != END and steps < self._max_steps:
            steps += 1
            node = self._nodes.get(current)
            if node is None:
                raise ValueError(f"状态图包含未知节点：{current}")
            delta = await node(current_state)
            if delta:
                current_state.update(delta)

            if current in {src for src, _, _ in self._conditional}:
                router, mapping = next(
                    (r, m) for src, r, m in self._conditional if src == current
                )
                key = await _call_router(router, current_state)
                if key not in mapping:
                    raise ValueError(f"条件路由返回未知目标：{key!r}（可用：{sorted(mapping)}）")
                current = mapping[key]
            else:
                current = next((dst for src, dst in self._edges if src == current), None)

        if current not in (END, None):  # 触发步数上限 → 判定死循环保护
            current_state["errors"] = [
                *(current_state.get("errors") or []),
                f"超过最大步数 AGENT_MAX_STEPS={self._max_steps}",
            ]
            current_state["status"] = "failed"
            await _save_breakpoint(thread_id, current_state)
        return current_state


def _build_fallback(
    nodes: dict[str, Callable[..., Any]],
    edges: list[tuple[str, str]],
    entry_point: str,
    conditional_edges: list[tuple[str, Callable[..., str], dict[str, str]]],
    use_checkpoint: bool,
) -> _FallbackGraph:
    return _FallbackGraph(
        nodes=nodes,
        edges=edges,
        entry_point=entry_point,
        conditional_edges=conditional_edges,
        max_steps=settings.AGENT_MAX_STEPS,
        use_checkpoint=use_checkpoint,
    )


# ---------------------------------------------------------------- 对外 API
def build_graph(
    nodes: dict[str, Callable[..., Any]],
    edges: list[tuple[str, str]],
    entry_point: str,
    conditional_edges: list[tuple[str, Callable[..., str], dict[str, str]]] = (),
    *,
    use_checkpoint: bool = False,
    state_schema: type[TypedDict] | None = None,
    force_fallback: bool = False,
) -> Any:
    """把「节点 + 边」编译成可执行状态图。

    :param nodes: 节点名 -> async 函数 (state) -> 状态增量 dict
    :param edges: 线性边 (源节点, 目标节点)
    :param entry_point: 入口节点名（自动接 START）
    :param conditional_edges: 条件边 (源节点, 路由函数, {路由值: 目标节点})
    :param use_checkpoint: 是否装配 langgraph checkpointer（Memory → Redis → MySQL
                           降级由 state_manager 断点保存承担）
    :param state_schema: 状态 TypedDict（默认 WorkflowState；Supervisor 传 SupervisorState）
    :param force_fallback: 强制使用内置执行器（测试降级路径用）
    :return: langgraph CompiledGraph 或 _FallbackGraph（接口均为 ainvoke）
    """
    if not nodes:
        raise ValueError("nodes 不能为空")
    if entry_point not in nodes:
        raise ValueError(f"entry_point 不在 nodes 中：{entry_point}")

    if force_fallback or not LANGGRAPH_AVAILABLE:
        if force_fallback and LANGGRAPH_AVAILABLE:
            logger.debug("状态图强制使用内置降级执行器")
        return _build_fallback(nodes, edges, entry_point, conditional_edges, use_checkpoint)

    schema = state_schema or WorkflowState
    graph: Any = StateGraph(schema)
    for name, fn in nodes.items():
        if name in (START, END):
            raise ValueError(f"节点名不能使用保留名：{name}")
        graph.add_node(name, _wrap_node(fn))
    graph.add_edge(START, entry_point)
    for src, dst in edges:
        graph.add_edge(src, dst)
    for src, router, mapping in conditional_edges:
        graph.add_conditional_edges(src, router, dict(mapping or {}))

    checkpointer = MemorySaver() if use_checkpoint else None
    return graph.compile(checkpointer=checkpointer)


async def run_workflow(
    graph: Any,
    initial_state: dict[str, Any],
    *,
    thread_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """执行状态图；异常统一折叠进 state（不裸抛），并保存断点。

    :param graph: build_graph 返回的可执行图
    :param initial_state: 初始状态
    :param thread_id: 断点线程号（每步 state_manager.save；缺省时自动生成并用于收尾保存）
    :param resume: True 时忽略 initial_state，从 checkpointer / 断点续跑
    :return: 最终状态（失败时含 errors + status="failed"）
    """
    state = _with_defaults(initial_state)
    has_checkpointer = getattr(graph, "checkpointer", None) is not None
    tid = thread_id or (uuid.uuid4().hex if has_checkpointer else None)

    token = _RUN_THREAD.set(tid)
    try:
        config: dict[str, Any] = {
            # 步数上限与 AGENT_MAX_STEPS 挂钩（每个业务步最多若干超步）
            "recursion_limit": max(int(settings.AGENT_MAX_STEPS), 4) * 4,
        }
        if tid:
            config["configurable"] = {"thread_id": tid}

        if not resume:
            await _save_breakpoint(tid, state)

        try:
            if resume and has_checkpointer:
                final = await graph.ainvoke(None, config)
            elif resume and tid:
                loaded = await state_manager.load(tid)
                final = dict(loaded or state)
            else:
                final = await graph.ainvoke(state, config)
        except Exception as exc:  # noqa: BLE001 - 图执行异常折叠为失败状态
            logger.exception("状态图执行失败（thread_id=%s）", tid)
            final = {
                **state,
                "errors": [*(state.get("errors") or []), str(exc)],
                "status": "failed",
            }

        result = dict(final or {})
        if "status" not in result:
            result["status"] = "failed" if result.get("errors") else "done"
        await _save_breakpoint(tid, result)
        return result
    finally:
        _RUN_THREAD.reset(token)


def route_by_status(state: dict[str, Any]) -> str:
    """按状态分流（条件边路由函数）：done / failed / retry / need_human / running。"""
    return str(state.get("status") or "running")
