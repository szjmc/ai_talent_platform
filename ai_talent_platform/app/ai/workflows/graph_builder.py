"""
模块：app/ai/workflows/graph_builder.py
职责：LangGraph 工作流构建工具（状态定义 + 节点编排 + 检查点）。

标签：[Agent组] [P0]（被全部 workflow 依赖：图构建 / 节点注册 / checkpoint）

============================ 实现思路与代码要点 ============================
1. 状态：用 TypedDict 定义 WorkflowState（如 ResumeState：file_path, text, parsed,
   errors, retries, status），节点返回「增量 dict」由 LangGraph 合并；
2. 节点：每个节点是一个 async 函数，内部只调用 Service / Skill / LLM，不写 SQL；
3. 边：支持条件路由（route_by_status）、循环（重试节点）、人工介入（interrupt）；
4. 检查点：使用 LangGraph 的 checkpointer（内存 / Redis / MySQL）实现断点续跑；
5. 失败处理：节点内部捕获异常写入 state["errors"]，由路由决定重试或结束。

对外 API：
    build_graph(nodes: dict[str, Callable], edges, entry_point, conditional_edges) -> CompiledGraph
    run_workflow(graph, initial_state) -> final_state

待实现清单：
    - [ ] StateGraph 构建封装
    - [ ] 条件边 / 循环 / 重试节点工具
    - [ ] checkpointer 装配（持久化到 Redis 或 MySQL）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
