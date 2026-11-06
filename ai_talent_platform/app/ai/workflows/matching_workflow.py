"""
功能：连接匹配流水线六个节点。
输入：MatchingPipeline和MatchingState。
输出：执行后的完整State。
流程：加载 → 召回 → 判断评分 → 证据 → 解释 → 组装。
边界：不实现评分或直接保存；LangGraph缺失时顺序执行相同节点。

标签：[Agent组] [P1]（匹配编排工作流）
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.matching_pipeline import MatchingPipeline, MatchingState

logger = logging.getLogger(__name__)

# *kwq* 新增：节点执行顺序（也是降级顺序执行的顺序）。
NODE_ORDER: tuple[str, ...] = (
    "load_context",
    "recall",
    "filter_score",
    "build_evidence",
    "explain",
    "assemble",
)


# *kwq* 新增：构建并编译 LangGraph 状态图。
# 返回编译后的图对象；langgraph 未安装时抛 ImportError，由 run_matching 决定降级。
def build_matching_graph(pipeline: MatchingPipeline):
    """按 NODE_ORDER 构建线性状态图。"""
    from langgraph.graph import END, START, StateGraph

    graph: Any = StateGraph(MatchingState)
    for name in NODE_ORDER:
        graph.add_node(name, getattr(pipeline, name))
    graph.add_edge(START, NODE_ORDER[0])
    for left, right in zip(NODE_ORDER, NODE_ORDER[1:]):
        graph.add_edge(left, right)
    graph.add_edge(NODE_ORDER[-1], END)
    return graph.compile()


# *kwq* 新增：执行匹配工作流（LangGraph 优先，顺序执行兜底）。
# 返回值：最终 state（含 items / stats / excluded / errors）。
async def run_matching(pipeline: MatchingPipeline, state: MatchingState) -> MatchingState:
    """执行匹配流程，返回终态。"""
    try:
        compiled = build_matching_graph(pipeline)
    except ImportError:  # pragma: no cover - 仅在未安装 langgraph 的环境触发
        logger.warning("langgraph 未安装，匹配工作流降级为顺序执行（节点逻辑不变）")
        result: dict[str, Any] = dict(state)
        for name in NODE_ORDER:
            delta = await getattr(pipeline, name)(result)  # type: ignore[arg-type]
            result.update(delta or {})
        return result  # type: ignore[return-value]

    logger.info(
        "matching.workflow.start position_id=%s talent_ids=%s top_k=%s",
        state.get("position_id"),
        len(state.get("talent_ids") or []) if state.get("talent_ids") else "ALL",
        state.get("top_k"),
    )
    final = await compiled.ainvoke(state)
    logger.info(
        "matching.workflow.done position_id=%s output=%s excluded=%s",
        final.get("position_id"),
        len(final.get("items") or []),
        len(final.get("excluded") or []),
    )
    return final  # type: ignore[return-value]


__all__ = ["NODE_ORDER", "build_matching_graph", "run_matching"]
