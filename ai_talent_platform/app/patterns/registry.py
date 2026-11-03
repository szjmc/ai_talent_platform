"""
模块：app/patterns/registry.py
职责：通用注册表（插件式注册机制）。

实现思路：
- 用一个 dict 维护「名称 -> 实现」的映射，配合装饰器实现自动注册；
- 典型用途：
    * AI Agent 注册：@agent_registry.register("resume") 注册简历解析 Agent
    * Skill 注册：@skill_registry.register("matching") 注册技能组件
    * LLM 适配器注册：@llm_registry.register("qwen")
- 好处：新增实现不需要修改调度中心的 if/elif。

使用示例：
    registry = Registry[type[BaseAgent]]("agent")

    @registry.register("resume")
    class ResumeAgent(BaseAgent): ...

    agent_cls = registry.get("resume")
"""

from __future__ import annotations

from typing import Callable, Generic, Iterator, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """名称 -> 实现 的注册表。"""

    def __init__(self, name: str = "registry") -> None:
        self.name = name
        self._items: dict[str, T] = {}

    def register(self, key: str) -> Callable[[T], T]:
        """装饰器注册。"""

        def decorator(item: T) -> T:
            self._items[key] = item
            return item

        return decorator

    def add(self, key: str, item: T) -> None:
        self._items[key] = item

    def get(self, key: str) -> T:
        if key not in self._items:
            raise KeyError(f"{self.name} 未注册：{key}，可用：{sorted(self._items)}")
        return self._items[key]

    def try_get(self, key: str) -> T | None:
        return self._items.get(key)

    def keys(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[tuple[str, T]]:
        return iter(self._items.items())

    def __len__(self) -> int:
        return len(self._items)


# 全平台共享的几个注册表（AI 模块实现后直接往这里 register）
agent_registry: Registry = Registry("agent")
skill_registry: Registry = Registry("skill")
llm_registry: Registry = Registry("llm")
