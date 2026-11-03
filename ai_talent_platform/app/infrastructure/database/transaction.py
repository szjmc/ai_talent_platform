"""
模块：app/infrastructure/database/transaction.py
职责：显式事务控制（跨多个 Repository 的原子操作）。

标签：[架构组] [P1]（事务上下文管理）

实现思路：
1. 默认策略：一个 HTTP 请求 = 一个事务，由 get_db() 统一提交/回滚，业务代码无需关心；
2. 需要「局部事务」或「嵌套事务」时使用本模块：
     async with transaction(db):
         ...
     - 外层无事务 → 新开事务；已有事务 → 使用 SAVEPOINT（begin_nested）；
3. retry_on_deadlock：MySQL 死锁自动重试装饰器（最多 3 次，指数退避）；
4. 事务边界属于 Service 层职责，Repository 内不要自行 commit。

示例：
    async def transfer_talent(db, from_dept, to_dept):
        async with transaction(db):
            await talent_repo.update(db, ...)
            await audit_repo.create(db, ...)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

T = TypeVar("T")

# MySQL 死锁 / 锁等待超时错误码
_RETRYABLE_MYSQL_CODES = {1205, 1213}


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（死锁、锁等待超时、连接闪断）。"""
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        code = getattr(exc.orig, "args", [None])[0]
        return code in _RETRYABLE_MYSQL_CODES
    return False


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """事务上下文：自动兼容外层已有事务（使用 SAVEPOINT）。"""
    if session.in_transaction():
        # 已在事务中：用嵌套事务（SAVEPOINT），保证局部失败可回滚到保存点
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            yield session


def retry_on_deadlock(
    max_attempts: int = 3,
    base_delay: float = 0.1,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """死锁自动重试装饰器（只用于幂等的数据库操作）。"""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    if not _is_retryable(exc) or attempt == max_attempts:
                        raise
                    last_exc = exc
                    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.05)
                    logger.warning(
                        "数据库操作可重试异常，第 %s/%s 次重试，等待 %.2fs：%s",
                        attempt, max_attempts, delay, exc,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # pragma: no cover - 理论上不会到达

        return wrapper

    return decorator
