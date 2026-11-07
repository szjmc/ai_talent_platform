"""
脚本：scripts/check_mysql_schema.py
职责：MySQL 建表自检（离线 DDL 校验 + 在线表结构比对），专治「自动建表静默中断」。

标签：[架构组] [P2]（建表自检：离线校验 ORM 元数据，在线比对真实库缺表/缺列）

为什么需要它：
    历史上启动日志只打印一句「自动建表失败（不影响服务启动）」，堆栈里是
        (1101, "BLOB, TEXT, GEOMETRY or JSON column 'question_ids' can't have a default value")
    create_all 是「全有或全无」：第一张失败的表之后，剩下的表**一张都不会建**。
    于是服务能启动、日志看着只有一条 ERROR，但 exam_sessions 之后的表全缺，
    相关接口一用就 500。本脚本把这件事变成一眼可见的检查结果。

两种模式：
    1) 离线（默认）：只用 SQLAlchemy 元数据 + MySQL 方言编译 DDL，不需要连库；
       校验「JSON/TEXT/BLOB 列是否声明了非法 server_default」等建表硬错误。
    2) 在线（--online）：连真实 MySQL，逐表比对「表是否存在 / ORM 字段是否缺列」，
       输出缺失清单并以非 0 退出，可直接接进部署前检查。

用法：
    python scripts/check_mysql_schema.py                 # 离线校验
    python scripts/check_mysql_schema.py --online        # 离线 + 在线比对
    python scripts/check_mysql_schema.py --online --fix  # 在线比对后补齐缺失的表（create_all 逐表）
    python scripts/check_mysql_schema.py --print-ddl     # 打印 exam_sessions 的 DDL（排障用）

退出码：0 通过；1 发现建表硬错误或在线缺表/缺列。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import inspect as sa_inspect  # noqa: E402
from sqlalchemy.dialects import mysql  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.models import Base  # noqa: E402

# MySQL 不允许带默认值的大对象 / 半结构化列类型
FORBIDDEN_DEFAULT_TYPES = {
    "JSON", "TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT",
    "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB",
    "GEOMETRY", "POINT", "LINESTRING", "POLYGON",
}


def offline_check() -> list[str]:
    """离线校验：返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.server_default is None:
                continue
            if {cls.__name__.upper() for cls in type(column.type).__mro__} & FORBIDDEN_DEFAULT_TYPES:
                problems.append(
                    f"{table.name}.{column.name}（{column.type}）声明了 MySQL 禁止的服务端默认值："
                    f"{column.server_default.arg} → 建表会报 ERR 1101"
                )
        try:
            str(CreateTable(table).compile(dialect=dialect))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{table.name} 的 DDL 无法用 MySQL 方言编译：{exc}")
    return problems


async def online_check(fix: bool = False) -> list[str]:
    """在线校验：比对真实库的表 / 列；fix=True 时逐表补齐缺失的表。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.infrastructure.database.session import create_tables_individually

    problems: list[str] = []
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            existing = set(await conn.run_sync(lambda sync_conn: sa_inspect(sync_conn).get_table_names()))
        missing_tables = sorted(set(Base.metadata.tables) - existing)
        if missing_tables:
            problems.append(f"缺少 {len(missing_tables)} 张表：{', '.join(missing_tables)}")
            if fix:
                async with engine.begin() as conn:
                    failures = await conn.run_sync(create_tables_individually)
                if failures:
                    problems.append(
                        "补齐失败：" + "；".join(f"{name} -> {reason}" for name, reason in failures)
                    )
                else:
                    problems = [p for p in problems if not p.startswith("缺少")]
                    print(f"      已补齐 {len(missing_tables)} 张表：{', '.join(missing_tables)}")

        # 已存在的表：比对 ORM 字段是否缺列（只提示，不影响退出码的硬判定）
        async with engine.connect() as conn:
            for name in sorted(existing & set(Base.metadata.tables)):
                columns = set(
                    await conn.run_sync(
                        lambda sync_conn, n=name: {c["name"] for c in sa_inspect(sync_conn).get_columns(n)}
                    )
                )
                missing_columns = sorted({c.name for c in Base.metadata.tables[name].columns} - columns)
                if missing_columns:
                    print(f"      [提示] {name} 缺少字段：{', '.join(missing_columns)}（需手工 ALTER 或迁移）")
    finally:
        await engine.dispose()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="MySQL 建表自检")
    parser.add_argument("--online", action="store_true", help="连真实 MySQL 比对表 / 字段")
    parser.add_argument("--fix", action="store_true", help="在线模式下逐表补齐缺失的表")
    parser.add_argument("--print-ddl", action="store_true", help="打印 exam_sessions 的建表 DDL")
    args = parser.parse_args()

    if args.print_ddl:
        table = Base.metadata.tables["exam_sessions"]
        print(str(CreateTable(table).compile(dialect=mysql.dialect())))
        return 0

    print("=" * 68)
    print(f" MySQL 建表自检（{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}）")
    print("=" * 68)
    print(f"[1/2] 离线校验 {len(Base.metadata.tables)} 张表的 DDL……")
    problems = offline_check()
    if problems:
        for item in problems:
            print(f"      ✗ {item}")
        print("      结论：存在建表硬错误，自动建表会在这些表处中断。")
        return 1
    print("      ✓ 通过（无非法默认值 / DDL 均可编译）")

    if not args.online:
        print("[2/2] 在线比对已跳过（加 --online 连接真实库校验缺表 / 缺列）")
        return 0

    print("[2/2] 在线比对真实库表结构……")
    online_problems = asyncio.run(online_check(fix=args.fix))
    if online_problems:
        for item in online_problems:
            print(f"      ✗ {item}")
        return 1
    print("      ✓ 通过（表齐全）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
