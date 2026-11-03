"""
模块：app/infrastructure/database/mysql.py
职责：MySQL（以及开发期 SQLite）异步引擎的创建与连通性检测。

实现思路：
1. 根据 settings.DATABASE_URL（mysql+aiomysql / sqlite+aiosqlite）创建 AsyncEngine；
2. MySQL 场景配置连接池：pool_size、max_overflow、pool_recycle、pool_pre_ping；
   - pool_pre_ping=True 保证从池中取出的连接可用（自动重连）；
3. 提供 create_async_engine_from_settings()：引擎工厂，供 session.py 调用；
4. 提供 ping_database()：执行 SELECT 1 验证「MySQL 连接成功」，供 /health 接口与启动日志使用；
5. 提供 dispose_engine()：应用关闭时释放连接池。

注意：本文件只负责「连接」，Session 生命周期在 session.py，事务控制在 transaction.py。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import settings

logger = logging.getLogger(__name__)


def create_async_engine_from_settings() -> AsyncEngine:
    """按配置创建异步引擎。"""
    url = settings.DATABASE_URL
    kwargs: dict = {
        "echo": settings.DB_ECHO,
        "future": True,
    }
    if settings.DB_DRIVER == "mysql":
        # MySQL：连接池 + 自动重连
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
            pool_pre_ping=True,
            pool_timeout=30,
            connect_args={"charset": settings.DB_CHARSET, "autocommit": False},
        )
    else:
        # SQLite：单文件、无连接池概念，关闭 pre_ping 即可
        kwargs.update(connect_args={"check_same_thread": False})

    engine = create_async_engine(url, **kwargs)
    logger.info(
        "数据库引擎已创建 driver=%s database=%s",
        settings.DB_DRIVER,
        settings.DB_NAME if settings.DB_DRIVER != "sqlite" else settings.SQLITE_PATH,
    )
    return engine


async def ping_database(engine: AsyncEngine) -> bool:
    """执行 SELECT 1，验证数据库连通性。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - 任何异常都视为连接失败
        logger.exception("数据库连通性检测失败")
        return False


async def dispose_engine(engine: AsyncEngine) -> None:
    """释放连接池（应用关闭时调用）。"""
    await engine.dispose()
    logger.info("数据库连接池已释放")
