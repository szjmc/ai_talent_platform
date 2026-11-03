"""
模块：app/ai/context.py
职责：AI 调用上下文（把「当前用户 / 权限 / trace_id」透传给 Agent 与 Skill）。

============================ 实现思路与代码要点 ============================
为什么需要：
- Agent / Skill 在执行过程中需要知道「是谁在问、能看哪些数据」，否则会出现越权；
- 但把 Request / User 对象一路塞进每个函数参数很脏，因此用 contextvars 做请求级上下文。

设计：
    @dataclass
    class AIContext:
        user_id: int | None
        roles: list[str]
        organization_id: int | None
        trace_id: str
        conversation_id: str | None
        extra: dict

    current_ai_context: ContextVar[AIContext]

    def set_context(user: User, **extra) -> AIContext      # 在 Router / Service 入口调用
    def get_context() -> AIContext                          # Skill 内部读取（权限过滤用）
    def clear_context()

要点：
- Skill 内部做数据过滤时，必须使用 get_context() 中的角色，禁止信任 LLM 传入的参数；
- 上下文不跨进程，后台任务需要显式重建（从 user_id 查库）。
待实现清单：
    - [ ] AIContext 数据类与 ContextVar
    - [ ] set_context / get_context / clear_context
    - [ ] 在 agent_service 的对话入口统一 set_context
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
