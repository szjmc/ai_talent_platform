"""
模块：app/patterns/singleton.py
职责：惰性单例持有器（引擎 / 连接池 / 客户端等「创建一次、统一释放」的资源）。

标签：[架构组] [P1]（抽取三处重复的 lazy init + dispose 样板）

实现思路：
1. LazyHolder.get()：首次调用执行 factory，之后复用结果；
   factory 返回 None（如 PGSQL_ENABLED=false）同样只计算一次，
   避免「未启用」分支每次调用都重复走创建逻辑 / 重复打日志；
2. LazyHolder.aclose()：调用 closer 释放资源并清空，下次 get 重新创建；
   更适用于应用关闭与测试的引擎重置（MySQL / PgSQL / Redis 共用此语义）；
3. LazyHolder.reset()：只清空引用不释放（少用，供特殊热切换场景）；
4. 纯同步 API（asyncio 单线程内无竞态），closer 可为协程函数。

使用示例：
    _engine_holder: LazyHolder[AsyncEngine] = LazyHolder("mysql-engine", create_engine, dispose_engine)
    def get_engine(): return _engine_holder.get()
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")

Closer = Callable[[T], Awaitable[None]]
Factory = Callable[[], "T | None"]


class LazyHolder(Generic[T]):
    """惰性资源持有器：创建（含 None 结果）只发生一次，释放路径统一。"""

    def __init__(self, name: str, factory: Factory[T], closer: Closer[T] | None = None) -> None:
        self._name = name
        self._factory = factory
        self._closer = closer
        self._value: T | None = None
        self._created = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def created(self) -> bool:
        """factory 是否已执行过（即便返回 None 也为 True）。"""
        return self._created

    def get(self) -> T | None:
        """获取资源；首次调用惰性创建，之后复用（None 也缓存）。"""
        if not self._created:
            self._value = self._factory()
            self._created = True
        return self._value

    def peek(self) -> T | None:
        """只读当前值，不触发创建。"""
        return self._value

    def reset(self) -> None:
        """丢弃引用（不调用 closer）；下次 get 重新创建。"""
        self._value = None
        self._created = False

    async def aclose(self) -> None:
        """释放资源（closer 存在时调用）并清空状态，下次 get 重新创建。"""
        value, self._value, self._created = self._value, None, False
        if value is not None and self._closer is not None:
            await self._closer(value)


__all__ = ["LazyHolder"]
