"""分析 SQL 的受控执行资源：独立凭据、只读事务、硬超时和连接释放。

标签：[Vanna组] [P0]（NL2SQL 只读执行边界，被 vanna_adapter 调用）

仅执行已经通过 NL2SQL 校验与权限处理的 SELECT，不承载业务指标逻辑。
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.core.exceptions import AIError


def ensure_query_enabled() -> None:
    """检查问数执行前提，缺少配置时抛 AIError。

    这里只检查开关/账号配置，不验证数据库连通性或实际授权；后者需部署验收。
    关闭分析库或未配置独立账号时直接拒绝，不能回退到 MySQL 主库。
    """
    if not settings.PGSQL_ENABLED:
        raise AIError("分析库未启用，暂时无法问数")
    if not settings.NL2SQL_PGSQL_USER:
        raise AIError("NL2SQL 依赖未就绪：尚未配置专用 PgSQL 只读账号")


async def execute_readonly(sql: str, max_rows: int) -> tuple[list[str], list[list]]:
    """用独立 PgSQL 账号执行一条受控 SELECT，返回 (列名列表, 数据行列表)。

    sql：必须已由上层完成 SQL 白名单、权限范围和 LIMIT 处理；本方法不补权限。
    max_rows：最多取出的行数，与上层 SQL LIMIT 配合，不替代执行前限行。
    超时/数据库错误转为 AIError，连接串和驱动细节不直接返回用户。
    """
    ensure_query_enabled()
    # 查询凭据与 PGSQL_USER/PASSWORD 建库凭据分离；URL.create 正确编码特殊字符。
    url = URL.create(
        "postgresql+asyncpg", username=settings.NL2SQL_PGSQL_USER,
        password=settings.NL2SQL_PGSQL_PASSWORD, host=settings.PGSQL_HOST,
        port=settings.PGSQL_PORT, database=settings.PGSQL_DB,
    )
    # NullPool 不保留空闲连接；timeout 管连接，command_timeout 管驱动命令。
    engine = create_async_engine(url, poolclass=NullPool, connect_args={
        "timeout": settings.NL2SQL_TIMEOUT_SECONDS,
        "command_timeout": settings.NL2SQL_TIMEOUT_SECONDS,
    })
    try:
        # 应用层超时覆盖连接、事务和取数阶段；数据库侧再独立设置语句超时。
        async with asyncio.timeout(settings.NL2SQL_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                async with connection.begin():
                    # 显式开启只读事务；仍要求部署时为该账号授予最小只读权限。
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    # true 表示设置仅在当前事务内生效；PostgreSQL 使用毫秒单位。
                    await connection.execute(text(
                        "SELECT set_config('statement_timeout', :timeout, true)"
                    ), {"timeout": str(settings.NL2SQL_TIMEOUT_SECONDS * 1000)})
                    await connection.execute(text(
                        "SELECT set_config('search_path', :schema, true)"
                    ), {"schema": settings.NL2SQL_ALLOWED_SCHEMA})
                    # 只有完成全部前置校验的 SQL 才能到达此处。
                    result = await connection.execute(text(sql))
                    return list(result.keys()), [list(row) for row in result.fetchmany(max_rows)]
    except (TimeoutError, SQLAlchemyError) as exc:
        raise AIError("分析查询失败或超时，请缩小时间/数据范围后重试") from exc
    finally:
        # 无论成功、失败还是任务取消，都释放本次创建的引擎资源。
        await engine.dispose()


@asynccontextmanager
async def readonly_analytics_session():
    """为确定性ORM统计提供同一只读快照，仍用专用账号与硬超时，不回退MySQL。

    查询表达式由本模块所属Repository构造，接口不接受SQL。事务控制语句仅用于
    只读/超时设置，不承载业务查询；跨次聚合保持REPEATABLE READ避免对比口径漂移。
    """
    ensure_query_enabled()
    if (settings.NL2SQL_ALLOWED_SCHEMA != settings.PGSQL_SCHEMA
            or not re.fullmatch(r"[a-z_][a-z0-9_]*", settings.PGSQL_SCHEMA)):
        raise AIError("分析统计schema必须与合法的NL2SQL_ALLOWED_SCHEMA一致")
    url = URL.create(
        "postgresql+asyncpg", username=settings.NL2SQL_PGSQL_USER,
        password=settings.NL2SQL_PGSQL_PASSWORD, host=settings.PGSQL_HOST,
        port=settings.PGSQL_PORT, database=settings.PGSQL_DB,
    )
    engine = create_async_engine(url, poolclass=NullPool, connect_args={
        "timeout": settings.NL2SQL_TIMEOUT_SECONDS,
        "command_timeout": settings.NL2SQL_TIMEOUT_SECONDS,
    })
    try:
        async with asyncio.timeout(settings.NL2SQL_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    await connection.execute(text(
                        "SELECT set_config('statement_timeout', :timeout, true)"
                    ), {"timeout": str(settings.NL2SQL_TIMEOUT_SECONDS * 1000)})
                    async with AsyncSession(bind=connection, expire_on_commit=False, autoflush=False) as session:
                        yield session
    except (TimeoutError, SQLAlchemyError) as exc:
        raise AIError("分析统计失败或超时，请检查分析库或缩小筛选范围") from exc
    finally:
        await engine.dispose()
