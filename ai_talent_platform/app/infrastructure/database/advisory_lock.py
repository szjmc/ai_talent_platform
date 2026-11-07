"""
模块：app/infrastructure/database/advisory_lock.py
职责：跨方言的咨询锁（命名锁）封装。

标签：[架构组] [P1]

背景：
    多个模块（RAG 入库任务、题库索引同步、崩溃任务回收、启动建表）使用
    MySQL 的 `GET_LOCK / RELEASE_LOCK` 做跨 worker 串行化。SQLite 回退模式
    （DB_DRIVER=sqlite，本地开发/演示）没有这两个函数，直接执行会报
    `no such function: GET_LOCK`，把「可选的并发保护」变成「功能不可用」。

约定：
    - MySQL：真实 GET_LOCK(key, timeout) / RELEASE_LOCK(key)；
    - SQLite：单文件 + 单进程场景下文件锁已保证串行，恒视为获锁成功（返回 1），
      释放为空操作。该模式仅用于本地开发/演示，不用于生产，故此降级安全。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config.settings import settings

logger = logging.getLogger(__name__)


async def acquire_advisory_lock(conn: AsyncConnection, key: str, timeout: int = 0) -> int:
    """获取命名锁；返回 1 表示成功（与 MySQL GET_LOCK 语义一致）。"""
    if settings.is_sqlite:
        return 1
    return int(await conn.scalar(text(f"SELECT GET_LOCK(:k, :t)"), {"k": key, "t": timeout}) or 0)


async def release_advisory_lock(conn: AsyncConnection, key: str) -> None:
    """释放命名锁（SQLite 回退模式下为空操作）。"""
    if settings.is_sqlite:
        return
    await conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": key})
