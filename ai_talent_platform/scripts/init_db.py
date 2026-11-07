"""
脚本：scripts/init_db.py
职责：数据库初始化脚本（MySQL 业务库建库建表 + PgSQL 分析库建库建表 + 内置角色）。

标签：[架构组] [P0]（MySQL + PgSQL 建库建表 + 内置角色，联调前置）

前置条件：
    MySQL 8 已启动（默认 127.0.0.1:3306），账号具备 CREATE DATABASE 权限
    PgSQL 15+ 已启动（默认 127.0.0.1:5432），账号具备 CREATE DATABASE 权限

使用方式：
    # 全流程：MySQL 建库 + 建表 + 角色初始化 + PgSQL 建库
    python scripts/init_db.py

    # 只建表（库已手工创建）
    python scripts/init_db.py --tables-only

    # 危险操作：删除 MySQL 所有表后重建（仅开发环境）
    python scripts/init_db.py --drop

执行流程：
1. 读取 app.config.settings（.env）；
2. MySQL：先连服务器（不指定 database）执行 CREATE DATABASE IF NOT EXISTS
   （字符集 utf8mb4 / 排序 utf8mb4_general_ci）；
3. MySQL：导入 app.models 注册全部模型 -> Base.metadata.create_all 建表；
4. MySQL：写入内置角色（super_admin / admin / hr / manager / employee）；
5. PgSQL（PGSQL_ENABLED=True 时）：创建分析库、schema、6 张分析表及默认分区。
注意：生产环境请使用 Alembic 迁移，不要用本脚本改表结构。
只初始化 PgSQL 可执行：python scripts/init_pgsql.py。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.core.permissions import ROLE_PERMISSIONS, RoleEnum  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.role import Role  # noqa: E402

ROLE_NAMES = {
    RoleEnum.SUPER_ADMIN: "超级管理员",
    RoleEnum.ADMIN: "系统管理员",
    RoleEnum.HR: "人力资源",
    RoleEnum.MANAGER: "部门管理者",
    RoleEnum.EMPLOYEE: "普通员工",
}


# ============================================================== MySQL
async def create_mysql_database() -> None:
    """MySQL：确保业务库存在（SQLite 回退模式下无需建库，文件自动创建）。"""
    if settings.is_sqlite:
        settings.ensure_dirs()
        print(f"[1/4] SQLite 回退模式：库文件 {settings.sqlite_path}（自动创建）")
        return
    engine = create_async_engine(settings.MYSQL_SERVER_URL, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                    f"DEFAULT CHARACTER SET {settings.DB_CHARSET} "
                    f"COLLATE {settings.DB_CHARSET}_general_ci"
                )
            )
        print(f"[1/4] MySQL 库 `{settings.DB_NAME}` 已就绪（{settings.DB_HOST}:{settings.DB_PORT}）")
    finally:
        await engine.dispose()


async def create_mysql_tables(drop: bool = False) -> None:
    """MySQL：建表（可选先删表）。

    与 app.infrastructure.database.session.init_db 共用 MySQL 命名锁
    （GET_LOCK），多 worker / 本脚本与应用同时启动时 DDL 串行化，避免竞争。
    同样逐表建表：单表 DDL 错误（例如历史上 JSON 列写了非法默认值）只跳过该表，
    其余表照常创建，并把失败表名汇总成一份清单，而不是第一张表失败就整体中断。
    """
    from app.infrastructure.database.advisory_lock import acquire_advisory_lock, release_advisory_lock
    from app.infrastructure.database.session import create_tables_individually

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await acquire_advisory_lock(conn, "ai_talent_platform:create_all", 30)
            try:
                if drop:
                    print("      [警告] 正在删除所有业务表……")
                    await conn.run_sync(Base.metadata.drop_all)
                failures = await conn.run_sync(create_tables_individually)
            finally:
                await release_advisory_lock(conn, "ai_talent_platform:create_all")
        tables = sorted(Base.metadata.tables)
        if failures:
            print(f"[2/4] MySQL 建表部分失败：成功 {len(tables) - len(failures)}/{len(tables)} 张")
            for name, reason in failures:
                print(f"      失败表 {name}：{reason}")
            raise RuntimeError(f"MySQL 建表失败 {len(failures)} 张：{[name for name, _ in failures]}")
        print(f"[2/4] MySQL 表结构创建完成，共 {len(tables)} 张表：{', '.join(tables)}")
    finally:
        await engine.dispose()


async def init_roles() -> None:
    """MySQL：写入内置角色（幂等）。"""
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            created = 0
            for role_enum in RoleEnum:
                code = role_enum.value
                exists = await db.scalar(
                    text("SELECT COUNT(1) FROM roles WHERE code = :code"), {"code": code}
                )
                if exists:
                    continue
                db.add(
                    Role(
                        code=code,
                        name=ROLE_NAMES.get(role_enum, code),
                        description=f"内置角色，权限 {len(ROLE_PERMISSIONS.get(role_enum, set()))} 项",
                        is_active=True,
                    )
                )
                created += 1
            await db.commit()
        print(f"[3/4] 角色初始化完成，新增 {created} 个角色")
    finally:
        await engine.dispose()


# ============================================================== PgSQL
async def create_pgsql_database() -> None:
    """PgSQL：确保分析库、schema、分析表及默认分区存在（兼容旧函数名）。"""
    if not settings.PGSQL_ENABLED:
        print("[4/4] PgSQL 未启用（PGSQL_ENABLED=False），跳过分析库初始化")
        return

    from app.infrastructure.database.analytics_schema import initialize_pgsql

    report = await initialize_pgsql()
    state = "创建成功" if report["database_created"] else "已存在"
    print(
        f"[4/4] PgSQL 分析库 `{report['database']}` {state}"
        f"（{settings.PGSQL_HOST}:{settings.PGSQL_PORT}，schema={report['schema']}）"
    )
    print(
        f"      分析表共 {len(report['tables'])} 张，本次新建 {len(report['created_tables'])} 张："
        f"{', '.join(report['tables'])}"
    )


async def main(drop: bool = False, tables_only: bool = False) -> None:
    print("=" * 68)
    print(" 数据库初始化开始（MySQL 业务库 + PgSQL 分析库）")
    print("=" * 68)
    settings.ensure_dirs()

    if not tables_only:
        await create_mysql_database()
    await create_mysql_tables(drop=drop)
    if not tables_only:
        await init_roles()
        await create_pgsql_database()

    print("=" * 68)
    print(" 完成。下一步：python scripts/create_admin.py 创建管理员账号")
    print("=" * 68)


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="初始化 MySQL / PgSQL 数据库")
    parser.add_argument("--drop", action="store_true", help="先删除所有表（危险，仅开发环境）")
    parser.add_argument("--tables-only", action="store_true", help="只建表，不建库、不初始化角色、不建分析库")
    args = parser.parse_args()
    asyncio.run(main(drop=args.drop, tables_only=args.tables_only))
