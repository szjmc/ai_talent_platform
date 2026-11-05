"""
模块：app/infrastructure/database/__init__.py
职责：数据库基础设施统一出口。

标签：[架构组] [P0]（四库协同统一出口）

    mysql.py      MySQL 业务主库引擎（异步 / 连接池）
    postgres.py   PgSQL 分析库引擎（分析宽表、Vanna NL2SQL 数据源）
    analytics_schema.py  PgSQL 分析库建库建表、结构检查、DDL 渲染
    health.py     四库连通性检查（SELECT 1 唯一实现）
    session.py    MySQL 请求级 Session、session_scope、建表、连接总览、连接归还
    transaction.py 显式事务（SAVEPOINT）与死锁重试
"""

from app.infrastructure.database.analytics_schema import (
    ensure_pgsql_database,
    initialize_pgsql,
    inspect_pgsql_schema,
    render_pgsql_ddl,
)
from app.infrastructure.database.health import check_databases, check_mysql, check_pgsql
from app.infrastructure.database.mysql import (
    create_async_engine_from_settings,
    dispose_engine,
)
from app.infrastructure.database.postgres import (
    dispose_pg_engine,
    get_pg_engine,
    get_pg_session_factory,
    pg_session,
    reset_pg_engine,
)
from app.infrastructure.database.session import (
    check_all_connections,
    close_db,
    get_db,
    get_engine,
    get_session_factory,
    init_db,
    release_connection,
    reset_engine,
    session_scope,
)
from app.infrastructure.database.transaction import retry_on_deadlock, transaction

__all__ = [
    "create_async_engine_from_settings",
    "dispose_engine",
    "get_db",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "release_connection",
    "init_db",
    "check_all_connections",
    "close_db",
    "reset_engine",
    "transaction",
    "retry_on_deadlock",
    "get_pg_engine",
    "get_pg_session_factory",
    "pg_session",
    "reset_pg_engine",
    "dispose_pg_engine",
    "check_databases",
    "check_mysql",
    "check_pgsql",
    "ensure_pgsql_database",
    "initialize_pgsql",
    "inspect_pgsql_schema",
    "render_pgsql_ddl",
]
