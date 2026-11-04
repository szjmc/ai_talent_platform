"""PgSQL 分析库建库、建表、结构检查。

标签：[Vanna组] [P1]（架构组已交付骨架：6 张分析表 + 默认分区，幂等建表 / 检查）

只负责 DDL 初始化，不写入业务数据，也不执行 MySQL -> PgSQL 同步。
脚本入口：scripts/init_pgsql.py；完整初始化 scripts/init_db.py 也会调用这里。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config.settings import settings
from app.models.analytics import (
    ANALYTICS_TABLE_NAMES,
    DEFAULT_PARTITIONS,
    AnalyticsBase,
)

logger = logging.getLogger(__name__)


def _quote(conn: Any, identifier: str) -> str:
    """使用当前连接方言安全引用标识符。"""
    return conn.dialect.identifier_preparer.quote(identifier)


def _qualified(conn: Any, name: str) -> str:
    return f"{_quote(conn, settings.PGSQL_SCHEMA)}.{_quote(conn, name)}"


async def ensure_pgsql_database() -> bool:
    """确保分析数据库存在；返回是否新建。"""
    if not settings.PGSQL_ENABLED:
        raise RuntimeError("PgSQL 未启用（PGSQL_ENABLED=False），无法初始化分析库")

    engine = create_async_engine(settings.PGSQL_SERVER_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": settings.PGSQL_DB},
            )
            if exists:
                return False
            await conn.execute(text(f"CREATE DATABASE {_quote(conn, settings.PGSQL_DB)}"))
            logger.info("PgSQL 分析库 %s 创建成功", settings.PGSQL_DB)
            return True
    finally:
        await engine.dispose()


async def _create_default_partitions(conn: Any) -> None:
    """为 4 张分区父表创建 DEFAULT 分区（幂等）。"""
    for parent, partition in DEFAULT_PARTITIONS.items():
        await conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_qualified(conn, partition)} "
                f"PARTITION OF {_qualified(conn, parent)} DEFAULT"
            )
        )


async def initialize_pgsql_schema(engine: AsyncEngine) -> dict[str, Any]:
    """在已存在的分析库中创建 schema、6 张分析表和默认分区。"""
    schema = settings.PGSQL_SCHEMA
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote(conn, schema)}"))
        before = set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names(schema=schema)))
        await conn.run_sync(AnalyticsBase.metadata.create_all)
        await _create_default_partitions(conn)
        after = set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names(schema=schema)))

    created = sorted(after - before)
    return {
        "database": settings.PGSQL_DB,
        "schema": schema,
        "tables": list(ANALYTICS_TABLE_NAMES),
        "created_tables": [name for name in created if name in ANALYTICS_TABLE_NAMES],
        "created_partitions": [name for name in created if name in DEFAULT_PARTITIONS.values()],
    }


async def initialize_pgsql() -> dict[str, Any]:
    """独立初始化入口：建库（如缺失）-> 建 schema -> 建表 -> 默认分区。"""
    database_created = await ensure_pgsql_database()
    engine = create_async_engine(settings.PGSQL_URL, pool_pre_ping=True)
    try:
        result = await initialize_pgsql_schema(engine)
        result["database_created"] = database_created
        return result
    finally:
        await engine.dispose()


async def inspect_pgsql_schema() -> dict[str, Any]:
    """只读检查分析表、字段和默认分区是否齐全，不创建任何对象。"""
    engine = create_async_engine(settings.PGSQL_URL, pool_pre_ping=True)
    schema = settings.PGSQL_SCHEMA
    try:
        async with engine.connect() as conn:
            existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names(schema=schema)))
            missing_tables = [name for name in ANALYTICS_TABLE_NAMES if name not in existing]
            missing_columns: dict[str, list[str]] = {}
            for table in AnalyticsBase.metadata.sorted_tables:
                if table.name not in existing:
                    continue
                columns = await conn.run_sync(
                    lambda c, name=table.name: {col["name"] for col in inspect(c).get_columns(name, schema=schema)}
                )
                missing = sorted({col.name for col in table.columns} - columns)
                if missing:
                    missing_columns[table.name] = missing
            missing_partitions = [name for name in DEFAULT_PARTITIONS.values() if name not in existing]
        return {
            "database": settings.PGSQL_DB,
            "schema": schema,
            "ok": not missing_tables and not missing_columns and not missing_partitions,
            "tables": list(ANALYTICS_TABLE_NAMES),
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "missing_default_partitions": missing_partitions,
        }
    finally:
        await engine.dispose()


def render_pgsql_ddl() -> str:
    """离线渲染 PostgreSQL DDL，供 --print-ddl 与测试使用，不连接数据库。"""
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    statements: list[str] = []
    schema = settings.PGSQL_SCHEMA
    quote = dialect.identifier_preparer.quote
    statements.append(f"CREATE SCHEMA IF NOT EXISTS {quote(schema)};")
    for table in AnalyticsBase.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    for parent, partition in DEFAULT_PARTITIONS.items():
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {quote(schema)}.{quote(partition)} "
            f"PARTITION OF {quote(schema)}.{quote(parent)} DEFAULT;"
        )
    return "\n\n".join(statements) + "\n"


__all__ = [
    "ensure_pgsql_database",
    "initialize_pgsql",
    "initialize_pgsql_schema",
    "inspect_pgsql_schema",
    "render_pgsql_ddl",
]
