"""
模块：app/patterns/factory.py
职责：工厂模式基础设施。

标签：[架构组] [P2]（基于 Registry 的通用工厂，可选）

典型场景：
- LLM 工厂：根据 settings.LLM_PROVIDER 创建 qwen / openai / deepseek / ollama 适配器
- Agent 工厂：根据 agent_type 创建对应 Agent
- 存储工厂：根据 storage_type 创建本地 / MinIO / 阿里云 OSS / S3 适配器

使用示例：
    llm = AdapterFactory.create("llm", settings.LLM_PROVIDER, api_key=..., model=...)

待实现清单：
    - [ ] 各业务模块的 Factory 子类（例如 ai/factory.py 的 LLMFactory）
"""

from __future__ import annotations

from typing import Any, Callable

from app.patterns.registry import Registry


class AdapterFactory:
    """基于 Registry 的通用工厂：注册的是「构造函数/工厂函数」。"""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def create(self, key: str, **kwargs: Any) -> Any:
        factory: Callable[..., Any] = self.registry.get(key)
        return factory(**kwargs)

    def create_or_default(self, key: str | None, default: str, **kwargs: Any) -> Any:
        return self.create(key if key in self.registry else default, **kwargs)
