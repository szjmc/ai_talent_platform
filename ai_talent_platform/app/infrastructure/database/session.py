"""
模块：app/infrastructure/database/session.py
职责：AsyncSession 生命周期管理（全平台唯一的会话入口）。

实现思路：
1. 引擎与 Session 工厂在模块内惰性单例，首次使用时创建；
2. get_db()：FastAPI 依赖，按请求创建 Session，请求结束自动提交 / 回滚 / 关闭；
   - 请求成功 → commit；抛出异常 → rollback 后继续向上抛（由全局异常处理器转响应）；
3. session_scope()：脚本 / 后台任务中使用的异步上下文管理器；
4. init_db()：开发期便捷建表（create_all），生产环境使用 Alembic 迁移；
5. reset_engine()：测试环境切换数据库后重建引擎。

使用（Router / Service / Repository 都不要自己 new Session）：
    async def endpoint(db: AsyncSession = Depends(get_db)): ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.infrastructure.database.mysql import (
    create_async_engine_from_settings,
    dispose_engine,
    ping_database,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """获取全局引擎（惰性创建）。"""
    global _engine
    if _engine is None:
        _engine = create_async_engine_from_settings()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取 Session 工厂（惰性创建）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,   # commit 后对象仍可访问属性，避免懒加载报错
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级数据库会话。

    事务策略：一个请求一个事务（Unit of Work）。
        正常结束 → commit
        发生异常 → rollback，然后重新抛出，交给全局异常处理器
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """非请求场景（脚本、定时任务、Agent 后台流程）使用的会话上下文。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """建表（开发期快捷方式；生产请使用 Alembic）。

    导入 models 包是为了让所有模型类注册到 Base.metadata。
    """
    from app.models import Base  # 延迟导入，避免循环依赖
    import app.models  # noqa: F401 - 触发所有模型模块的注册

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表结构初始化完成，共 %s 张表", len(Base.metadata.tables))


async def check_connection() -> bool:
    """对外暴露的连通性检查（供 /health 使用）。"""
    return await ping_database(get_engine())


async def close_db() -> None:
    """应用关闭：释放连接池。"""
    global _engine, _session_factory
    if _engine is not None:
        await dispose_engine(_engine)
    _engine = None
    _session_factory = None


def reset_engine() -> None:
    """重置引擎单例（测试环境指向临时库时使用）。"""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
