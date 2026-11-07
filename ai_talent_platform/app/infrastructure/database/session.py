"""
模块：app/infrastructure/database/session.py
职责：MySQL AsyncSession 生命周期管理（业务写操作唯一入口）。

标签：[架构组] [P0]（AsyncSession / 事务边界，被全部 Repository 依赖）

实现思路：
1. 引擎与 Session 工厂通过 patterns.singleton.LazyHolder 惰性单例管理，
   与 PgSQL（postgres.py）、Redis（cache/redis.py）共用同一套持有 / 释放语义；
2. get_db()：FastAPI 依赖，按请求创建 Session，请求结束自动提交 / 回滚 / 关闭；
   session_scope()：脚本 / 后台任务使用的异步上下文管理器；
   两者共用同一份事务边界实现（session_scope 是唯一定义，get_db 委托它）：
       请求/上下文正常结束 → commit；抛出异常 → rollback 后继续向上抛；
3. release_connection()：长耗时外部调用（如 LLM 请求）前主动归还连接，
   避免一次 AI 调用占住池连接几十秒导致连接池耗尽；
4. init_db()：开发期便捷建表（create_all，带 MySQL 命名锁串行化多实例启动），
   生产环境使用 Alembic 迁移（lifespan 按 settings.db_auto_create_tables 把关）；
5. check_all_connections()：MySQL + PgSQL 一起检查，供就绪探针展示四库状态
   （单库连通性统一走 infrastructure/database/health.py，本模块不重复实现）。

使用（Router / Service / Repository 都不要自己 new Session）：
    async def endpoint(db: AsyncSession = Depends(get_db)): ...
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.infrastructure.database.mysql import (
    create_async_engine_from_settings,
    dispose_engine,
)
from app.infrastructure.database.postgres import dispose_pg_engine
from app.patterns.singleton import LazyHolder

logger = logging.getLogger(__name__)


def _build_engine() -> AsyncEngine:
    return create_async_engine_from_settings()


def _build_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),  # type: ignore[arg-type]
        class_=AsyncSession,
        expire_on_commit=False,  # commit 后对象仍可访问属性，避免懒加载报错
        autoflush=False,
    )


_engine_holder: LazyHolder[AsyncEngine] = LazyHolder("mysql-engine", _build_engine, dispose_engine)
_session_holder: LazyHolder[async_sessionmaker[AsyncSession]] = LazyHolder(
    "mysql-session-factory", _build_session_factory
)


def get_engine() -> AsyncEngine:
    """获取 MySQL 引擎（惰性创建）。"""
    return _engine_holder.get()  # type: ignore[return-value]


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取 MySQL Session 工厂（惰性创建）。"""
    return _session_holder.get()  # type: ignore[return-value]


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """请求 / 脚本 / 后台任务共用的唯一事务边界。

    正常结束 → commit；发生异常 → rollback，然后重新抛出（交给全局异常处理器）。
    get_db() 与本上下文共用该实现，避免两份事务块漂移。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def warm_up_pool(count: int | None = None) -> int:
    """启动阶段连接池预热：并发建立常驻连接并归还池中。

    背景：远程 MySQL 的 TCP+鉴权握手实测可达 5~19 秒（RTT 仅 50ms，慢在服务器端）。
    不预热时，服务重启后的首批请求要现场建连，用户视角就是「列表加载失败 / 请求失败」。
    预热后请求从池中直接取连接，只剩单次查询耗时。
    """
    from sqlalchemy import text

    n = count or min(settings.DB_POOL_SIZE, 5)

    async def _one() -> bool:
        try:
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - 单条预热失败不拖垮其余连接
            logger.warning("连接池预热单连接失败", exc_info=True)
            return False

    results = await asyncio.gather(*(_one() for _ in range(n)))
    ok = sum(1 for r in results if r)
    logger.info("MySQL 连接池预热完成：%s/%s 条常驻连接", ok, n)
    return ok


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级 MySQL 会话（复用 session_scope 的事务策略）。

    事务策略：一个请求一个事务（Unit of Work）。
        正常结束 → commit
        发生异常 → rollback，然后重新抛出，交给全局异常处理器
    """
    async with session_scope() as session:
        yield session


async def release_connection(session: AsyncSession) -> bool:
    """提交当前事务、把连接归还连接池（长耗时外部调用前调用，如 LLM 请求）。

    背景：一个请求一个事务意味着长外部调用期间会一直占用池连接，
    并发稍高即打满连接池。在「读取上下文 → 长调用 → 继续写入」的流程里，
    应在长调用前调用本函数：

        上下文已读取 -> await release_connection(db) -> 60s LLM 调用 -> 后续写入

    返回 True 表示提交了事务。expire_on_commit=False 保证 session 与已加载
    对象仍可继续使用；此后的写入随请求结束由 get_db 再次提交。
    注意：此函数会提交「当前已 flush 的全部写操作」，调用点应处于只读阶段。
    """
    if not session.in_transaction():
        return False
    await session.commit()
    return True


def create_tables_individually(sync_conn: Any) -> list[tuple[str, str]]:
    """逐个建表（在同步连接上执行），返回失败清单 [(表名, 错误摘要)]。

    为什么不用一次性 `metadata.create_all()`：
        它是「全有或全无」的语义 —— 第一张建表失败（例如 JSON 列写了非法默认值，
        MySQL 直接报 ERR 1101）就中断，**后面所有表都不会被创建**，而 lifespan
        只打印一条「自动建表失败（不影响服务启动）」，服务带病启动、接口一用就 500。
        逐表执行可以把故障隔离到单张表，失败表名与原因一次性列出来，排障成本从
        「翻一堆堆栈」降到「看一行日志」，且其余表照常可用。

    幂等：checkfirst=True 已存在的表直接跳过，重复启动不会报错。
    """
    from app.models import Base  # 延迟导入，避免循环依赖
    import app.models  # noqa: F401 - 触发所有模型模块的注册

    failures: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        try:
            Base.metadata.create_all(sync_conn, tables=[table], checkfirst=True)
        except Exception as exc:  # noqa: BLE001 - 单表失败不阻断其余表
            reason = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:200]
            failures.append((table.name, reason))
            logger.error("建表失败：%s（%s）—— 其余表继续创建", table.name, reason)
    return failures


async def init_db() -> None:
    """建表（开发期快捷方式；生产请使用 Alembic）。

    导入 app.models 是为了让所有模型类注册到 Base.metadata；
    通过 MySQL 命名锁 GET_LOCK 串行化多 worker / 多实例同时启动的建表；
    逐表创建（见 create_tables_individually），单表 DDL 错误不会拖垮其余表。
    SQLite 回退模式没有 GET_LOCK，单文件库本身由文件锁串行化，直接建表即可。
    """
    from sqlalchemy import text

    from app.models import Base  # 延迟导入，避免循环依赖
    import app.models  # noqa: F401 - 触发所有模型模块的注册

    engine = get_engine()
    if settings.is_sqlite:
        async with engine.begin() as conn:
            failures = await conn.run_sync(create_tables_individually)
    else:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT GET_LOCK('ai_talent_platform:create_all', 30)"))
            try:
                failures = await conn.run_sync(create_tables_individually)
            finally:
                await conn.execute(text("SELECT RELEASE_LOCK('ai_talent_platform:create_all')"))

    total = len(Base.metadata.tables)
    if failures:
        detail = "；".join(f"{name}: {reason}" for name, reason in failures)
        raise RuntimeError(
            f"MySQL 建表部分失败（成功 {total - len(failures)}/{total} 张），失败表：{detail}"
        )
    logger.info("MySQL 表结构初始化完成，共 %s 张表", total)


async def check_all_connections() -> dict:
    """四库连通性总览（详细版，含失败原因），供 /health/ready 与启动日志使用。"""
    from app.infrastructure.database.health import check_databases

    return await check_databases()


async def close_db() -> None:
    """应用关闭：释放 MySQL 与 PgSQL 连接池并重置引擎单例。"""
    await reset_engine()


async def reset_engine() -> None:
    """重置引擎单例（测试 / 配置热切换时使用）。

    与 close_db 同源：MySQL 与 PgSQL 的引擎 + Session 工厂一并释放清空，
    保证下一次使用按当前 settings 重新创建（历史上只重置 MySQL、
    PgSQL 旧引擎残留的不对称行为已修复）。
    """
    await _session_holder.aclose()
    await _engine_holder.aclose()
    await dispose_pg_engine()  # 同时释放并清空 PgSQL 单例
