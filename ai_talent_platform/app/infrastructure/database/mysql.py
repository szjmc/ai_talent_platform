"""
模块：app/infrastructure/database/mysql.py
职责：MySQL（业务主库）异步引擎的创建与连通性检测。

实现思路：
1. 按 settings.DATABASE_URL（mysql+aiomysql）创建 AsyncEngine；
2. 连接池：pool_size / max_overflow / pool_recycle / pool_pre_ping / pool_timeout，
   pool_pre_ping=True 保证从池中取出的连接可用（自动重连），pool_recycle 避免 MySQL 8h 断连；
3. 统一 connect_args：charset=utf8mb4、autocommit=False（事务由 SQLAlchemy 控制）；
4. 提供 create_async_engine_from_settings()：引擎工厂，供 session.py 调用；
5. 提供 ping_database()：执行 SELECT 1 验证「MySQL 连接成功」，供 /health 与启动日志使用；
6. 提供 dispose_engine()：应用关闭时释放连接池。

注意：本文件只负责「连接」，Session 生命周期在 session.py，事务控制在 transaction.py。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import settings

logger = logging.getLogger(__name__)


def create_async_engine_from_settings() -> AsyncEngine:
    """按配置创建 MySQL 异步引擎。"""
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        future=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_pre_ping=True,
        connect_args={"charset": settings.DB_CHARSET, "autocommit": False},
    )
    logger.info(
        "MySQL 引擎已创建 %s:%s/%s（pool_size=%s, max_overflow=%s）",
        settings.DB_HOST,
        settings.DB_PORT,
        settings.DB_NAME,
        settings.DB_POOL_SIZE,
        settings.DB_MAX_OVERFLOW,
    )
    return engine


async def ping_database(engine: AsyncEngine) -> bool:
    """执行 SELECT 1，验证 MySQL 连通性。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - 任何异常都视为连接失败
        logger.exception(
            "MySQL 连通性检测失败：%s:%s/%s", settings.DB_HOST, settings.DB_PORT, settings.DB_NAME
        )
        return False


async def dispose_engine(engine: AsyncEngine) -> None:
    """释放连接池（应用关闭时调用）。"""
    await engine.dispose()
    logger.info("MySQL 连接池已释放")
