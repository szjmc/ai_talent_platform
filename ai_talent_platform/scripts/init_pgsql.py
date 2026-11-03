"""独立初始化 PgSQL 分析库。

用法：
    python scripts/init_pgsql.py              # 建库 + schema + 6 张分析表 + 默认分区（幂等）
    python scripts/init_pgsql.py --check      # 只读检查表/字段/默认分区是否齐全
    python scripts/init_pgsql.py --print-ddl  # 离线打印 DDL，不连接数据库

依赖 .env 中 PGSQL_* 配置；账号需要 CREATE DATABASE / CREATE SCHEMA 权限。
本脚本不删除已有表，不修改已有字段，不写入业务数据。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.infrastructure.database.analytics_schema import (  # noqa: E402
    initialize_pgsql,
    inspect_pgsql_schema,
    render_pgsql_ddl,
)


async def _run(check: bool) -> int:
    if not settings.PGSQL_ENABLED:
        print("PgSQL 未启用，请在 .env 设置 PGSQL_ENABLED=true")
        return 1

    print(f"PgSQL 目标：{settings.PGSQL_HOST}:{settings.PGSQL_PORT}/{settings.PGSQL_DB}（schema={settings.PGSQL_SCHEMA}）")
    if check:
        report = await inspect_pgsql_schema()
        if report["ok"]:
            print(f"检查通过：{len(report['tables'])} 张分析表及默认分区齐全")
            for name in report["tables"]:
                print(f"  - {settings.PGSQL_SCHEMA}.{name}")
            return 0
        print("检查未通过：")
        for key in ("missing_tables", "missing_columns", "missing_default_partitions"):
            if report[key]:
                print(f"  {key}: {report[key]}")
        print("可执行 python scripts/init_pgsql.py 创建缺失对象；已有字段变更需单独迁移。")
        return 1

    report = await initialize_pgsql()
    print(f"数据库：{report['database']}（{'新建' if report['database_created'] else '已存在'}）")
    print(f"Schema：{report['schema']}")
    print(f"分析表共 {len(report['tables'])} 张，本次新建 {len(report['created_tables'])} 张：")
    for name in report["tables"]:
        mark = "新建" if name in report["created_tables"] else "已存在"
        print(f"  - {report['schema']}.{name}（{mark}）")
    print("默认分区已就绪。注意：这里只初始化结构，不会自动同步 MySQL 数据。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 / 检查 PgSQL 分析库")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只读检查结构，不创建对象")
    group.add_argument("--print-ddl", action="store_true", help="离线打印 PostgreSQL DDL")
    args = parser.parse_args()
    setup_logging()
    if args.print_ddl:
        print(render_pgsql_ddl())
        return
    raise SystemExit(asyncio.run(_run(check=args.check)))


if __name__ == "__main__":
    main()
