"""
模块：app/infrastructure/database/mysql.py
职责：MySQL（业务主库）异步引擎的创建与连通性检测。

标签：[架构组] [P0]（业务主库引擎，硬依赖）

实现思路：
1. 按 settings.DATABASE_URL（mysql+aiomysql）创建 AsyncEngine；
2. 连接池：pool_size / max_overflow / pool_recycle / pool_pre_ping / pool_timeout，
   pool_pre_ping=True 保证从池中取出的连接可用（自动重连），pool_recycle 避免 MySQL 8h 断连；
3. 统一 connect_args：charset=utf8mb4、autocommit=False（事务由 SQLAlchemy 控制）；
4. 提供 create_async_engine_from_settings()：引擎工厂，由 session.py 的 LazyHolder 调用；
5. 提供 dispose_engine()：应用关闭 / 重置时释放连接池；
   连通性检查（SELECT 1）统一收敛在 infrastructure/database/health.py，本模块不重复实现。

注意：本文件只负责「连接」，Session 生命周期在 session.py，事务控制在 transaction.py。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings

logger = logging.getLogger(__name__)


def create_async_engine_from_settings() -> AsyncEngine:
    """按配置创建 MySQL 异步引擎。

    测试环境（APP_ENV=test）使用 NullPool，避免连接跨 pytest-asyncio 的
    函数级事件循环被复用（复用会撞上已关闭的 transport，Windows 下表现为
    `AttributeError: 'NoneType' object has no attribute 'send'`）。
    """
    if settings.is_test:
        engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            future=True,
            poolclass=NullPool,
            connect_args={"charset": settings.DB_CHARSET, "autocommit": False},
        )
        logger.info("MySQL 引擎已创建（测试环境，NullPool）%s/%s", settings.DB_HOST, settings.DB_NAME)
        return engine

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


async def dispose_engine(engine: AsyncEngine) -> None:
    """释放连接池（应用关闭 / 引擎重置时调用）。"""
    await engine.dispose()
    logger.info("MySQL 连接池已释放")
