"""
模块：app/patterns/strategy.py
职责：策略模式基础设施（用于「同类任务的多种算法」）。

典型场景：
- 人岗匹配策略：硬性过滤 / 向量软匹配 / 混合加权
- 文档切分策略：按长度 / 按语义 / 按标题层级
- 简历解析策略：规则解析 / LLM 解析 / 混合

使用示例：
    class HardFilterStrategy(MatchStrategy): ...
    class HybridStrategy(MatchStrategy): ...

    strategy = MatchStrategyFactory.create("hybrid", weights={"skill": 0.4})
    result = strategy.match(position, candidates)

待实现清单：
    - [ ] MatchStrategy 抽象基类与三个实现（业务模块负责）
    - [ ] 策略工厂（可复用本文件的 select_strategy）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

T = TypeVar("T")


class Strategy(ABC):
    """策略抽象基类：所有具体策略实现 execute。"""

    name: str = "base"

    @abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """执行策略。"""
        raise NotImplementedError


def select_strategy(
    strategies: dict[str, T], name: str | None, default: str
) -> T:
    """从策略字典中选择策略，未命中回落默认策略。"""
    key = name if name and name in strategies else default
    if key not in strategies:
        raise KeyError(f"策略不存在：{key}，可用：{sorted(strategies)}")
    return strategies[key]
