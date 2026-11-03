"""
模块：app/ai/agents/base_agent.py
职责：Agent 基类（统一「输入 -> 工具调用 -> 输出 -> 落库」骨架）。

============================ 实现思路与代码要点 ============================
基类骨架：
    class BaseAgent(ABC):
        name: str
        system_prompt: str

        def __init__(self, db: AsyncSession, user: User | None = None)
        @abstractmethod
        async def build_prompt(self, payload: dict) -> list[ChatMessage]   # 取数据 + 组装提示
        @abstractmethod
        async def parse_output(self, raw: str) -> dict                     # 结构化解析 + 校验
        @abstractmethod
        async def persist(self, result: dict, payload: dict) -> Any        # 写回业务表

        async def run(self, payload: dict) -> dict:
            # 1) build_prompt -> 2) llm_manager.chat -> 3) parse_output
            # 4) 失败重试 / 兜底 -> 5) persist -> 6) 记录 token 与耗时 -> 返回
实现要点：
- Agent 通过 Services 取数据（不要直接写 SQL），保证权限一致；
- 输出解析用 ai/utils/output_parser（JSON 容错 + Pydantic 校验）；
- 失败重试最多 N 次（settings.LLM_MAX_RETRIES），仍失败则返回明确的错误结构；
- 每个 Agent 注册到 patterns.registry.agent_registry，便于调度与 Dify 可视化。
待实现清单：
    - [ ] BaseAgent 与 run() 主流程
    - [ ] 注册机制
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
