"""
模块：app/ai/agent.py
职责：AI Agent 统一调度入口（对外唯一门面）。

============================ 实现思路与代码要点 ============================
定位：
    routers/agent_router.py  -> AgentService  -> 本文件（调度器）  -> 具体 Agent
    ai/agents/*_agent.py 各自实现 build_prompt / parse_output / persist。

调度逻辑：
    1. 读取 agent_type（assistant / resume / talent / matching / assessment / training）
    2. 从 patterns.registry.agent_registry 取出对应 Agent 类
    3. 构造 Agent(db=..., context=ai.context.get_context())
    4. await agent.run(payload) -> dict
    5. 统一埋点：token 统计、耗时、审计日志

    async def dispatch(agent_type: str, payload: dict, db: AsyncSession) -> dict
    async def dispatch_stream(agent_type: str, payload: dict) -> AsyncIterator[str]   # SSE

要点：
- 未注册的 agent_type 抛 NotFoundError 并列出可用 Agent；
- 单个 Agent 失败不影响其它任务；超时统一控制（settings.LLM_TIMEOUT）；
- 这里是「编排」，不要写具体的提示词与业务逻辑。
待实现清单：
    - [ ] AGENT_TYPES 常量与校验
    - [ ] dispatch / dispatch_stream
    - [ ] token 与耗时埋点
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
