"""
模块：app/infrastructure/database/postgres.py
职责：PgSQL（分析 / 时序库）异步引擎的创建与连通性检测。

职责边界（四库协同，禁止混用）：
    MySQL   业务主数据（用户/组织/岗位/人才/测评等），由 session.py 提供写事务
    PgSQL   海量履历、测评历史、行为时序、分析宽表 —— 主要供统计分析与 Vanna NL2SQL 查询
    Milvus  向量检索
    Redis   缓存 / 会话 / 限流

实现思路：
1. 使用 postgresql+asyncpg 创建独立的 AsyncEngine（与分析库分离的连接池）；
2. PGSQL_ENABLED=False 时 get_pg_engine() 返回 None，上层统计功能降级为「只统计 MySQL」；
3. 分析库以「读多写少」为特征，连接池可小于 MySQL；查询统一走只读会话（见 get_pg_session）；
4. Vanna NL2SQL 生成的 SQL 必须在本库执行（禁止打 MySQL 主库），并经过 adapters/nl2sql/sql_validator 校验。

使用方式（分析类查询）：
    async with pg_session() as pg:
        rows = (await pg.execute(text(sql))).mappings().all()
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings

logger = logging.getLogger(__name__)

_pg_engine: AsyncEngine | None = None
_pg_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_pg_engine() -> AsyncEngine | None:
    """创建 PgSQL 异步引擎；未启用时返回 None。"""
    if not settings.PGSQL_ENABLED:
        logger.info("PgSQL 未启用（PGSQL_ENABLED=False），分析类查询降级为仅使用 MySQL")
        return None

    engine = create_async_engine(
        settings.PGSQL_URL,
        echo=settings.PGSQL_ECHO,
        future=True,
        pool_size=settings.PGSQL_POOL_SIZE,
        max_overflow=settings.PGSQL_MAX_OVERFLOW,
        pool_recycle=settings.PGSQL_POOL_RECYCLE,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": settings.PGSQL_SCHEMA}},
    )
    logger.info(
        "PgSQL 引擎已创建 %s:%s/%s（schema=%s）",
        settings.PGSQL_HOST,
        settings.PGSQL_PORT,
        settings.PGSQL_DB,
        settings.PGSQL_SCHEMA,
    )
    return engine


def get_pg_engine() -> AsyncEngine | None:
    """获取 PgSQL 引擎单例（惰性创建）。"""
    global _pg_engine
    if _pg_engine is None:
        _pg_engine = create_pg_engine()
    return _pg_engine


def get_pg_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """获取 PgSQL Session 工厂。"""
    global _pg_session_factory
    engine = get_pg_engine()
    if engine is None:
        return None
    if _pg_session_factory is None:
        _pg_session_factory = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    return _pg_session_factory


@asynccontextmanager
async def pg_session() -> AsyncGenerator[AsyncSession | None, None]:
    """分析库会话上下文（只读语义：不 commit，异常回滚）。

    未启用 PgSQL 时 yield None，调用方需判空并降级。
    """
    factory = get_pg_session_factory()
    if factory is None:
        yield None
        return
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping_pgsql() -> bool:
    """执行 SELECT 1，验证 PgSQL 连通性（供 /health/ready 使用）。"""
    engine = get_pg_engine()
    if engine is None:
        return False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "PgSQL 连通性检测失败：%s:%s/%s",
            settings.PGSQL_HOST,
            settings.PGSQL_PORT,
            settings.PGSQL_DB,
        )
        return False


async def dispose_pg_engine() -> None:
    """释放 PgSQL 连接池。"""
    global _pg_engine, _pg_session_factory
    if _pg_engine is not None:
        await _pg_engine.dispose()
        logger.info("PgSQL 连接池已释放")
    _pg_engine = None
    _pg_session_factory = None
