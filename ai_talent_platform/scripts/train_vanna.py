"""准备 / 导入 Vanna 训练数据（架构组 + Vanna 组共同验收）。

标签：[架构组]+[Vanna组] [P1]（DDL + 术语 + 示例问答训练语料）

训练语料三类：
1. DDL：PgSQL 分析库 6 张表的建表语句（直接读 ORM 元数据，保证与代码一致）；
2. 文档：表/字段中文说明（来自 database_design.md 的设计与本文档维护的术语表）；
3. 示例问答：question -> sql（Vanna 的核心 Few-shot 来源）。

用法：
    python scripts/train_vanna.py                       # 生成语料并尝试训练
    python scripts/train_vanna.py --output data/vanna/corpus.json   # 只导出语料
    python scripts/train_vanna.py --dry-run             # 只打印统计，不写文件、不训练

未安装 vanna 时仍然会导出语料（Vanna 组拿到语料后可自行导入），并提示安装命令。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.settings import settings  # noqa: E402
from app.infrastructure.database.analytics_schema import render_pgsql_ddl  # noqa: E402
from app.models.analytics import ANALYTICS_TABLE_NAMES  # noqa: E402

DEFAULT_OUTPUT = BASE_DIR / "data" / "vanna" / "corpus.json"

# 业务术语（让模型把「人头数」「学历分布」等口语映射到真实字段）
BUSINESS_TERMS: list[str] = [
    "人才 = talents / dim_talent_wide 中的一条人才记录",
    "部门 = organizations 表，dim_organization 是其分析维表（org_id 对应 organizations.id）",
    "在招岗位 = positions.status = 'open'",
    "匹配分 = matching_records.score，取值 0-100",
    "人才画像 = talent_portraits.dimensions（skill/experience/quality/potential）",
    "月度人才快照 = fact_talent_snapshot，按月统计各部门人才数量与平均工龄",
]

# 示例问答（Few-shot，SQL 必须只查分析库 6 张表）
EXAMPLE_QUERIES: list[dict[str, str]] = [
    {
        "question": "上个月各部门的人才数量是多少？",
        "sql": (
            "SELECT o.name AS department, sum(f.talent_count) AS talent_count "
            "FROM fact_talent_snapshot f JOIN dim_organization o ON o.org_id = f.organization_id "
            "WHERE f.snapshot_month = date_trunc('month', current_date - interval '1 month') "
            "GROUP BY o.name ORDER BY talent_count DESC LIMIT 200"
        ),
    },
    {
        "question": "各学历层次的人才分布？",
        "sql": (
            "SELECT education, count(*) AS talent_count FROM dim_talent_wide "
            "GROUP BY education ORDER BY talent_count DESC LIMIT 200"
        ),
    },
    {
        "question": "最近半年测评平均分的变化趋势？",
        "sql": (
            "SELECT date_trunc('month', scored_at) AS month, avg(total_score) AS avg_score "
            "FROM fact_assessment_history WHERE scored_at >= current_date - interval '6 months' "
            "GROUP BY 1 ORDER BY 1 LIMIT 200"
        ),
    },
    {
        "question": "学习时长最多的前 10 位人才？",
        "sql": (
            "SELECT t.name, sum(f.study_minutes) AS total_minutes FROM fact_training_track f "
            "JOIN dim_talent_wide t ON t.talent_id = f.talent_id "
            "GROUP BY t.name ORDER BY total_minutes DESC LIMIT 10"
        ),
    },
    {
        "question": "最近 7 天各类行为事件的次数？",
        "sql": (
            "SELECT event_type, count(*) AS event_count FROM fact_behavior_event "
            "WHERE event_at >= current_date - interval '7 days' "
            "GROUP BY event_type ORDER BY event_count DESC LIMIT 200"
        ),
    },
]


def build_corpus() -> dict[str, Any]:
    """构造训练语料（DDL + 文档 + 示例问答）。"""
    ddl = render_pgsql_ddl()
    return {
        "database": settings.PGSQL_DB,
        "schema": settings.PGSQL_SCHEMA,
        "tables": list(ANALYTICS_TABLE_NAMES),
        "ddl": ddl,
        "documentation": [
            f"分析库 {settings.PGSQL_DB}（schema {settings.PGSQL_SCHEMA}）只做只读分析，禁止写业务事务",
            "事实表（fact_*）按时间 RANGE 分区，查询请带时间范围条件",
            "dim_talent_wide 是人才宽表，供 NL2SQL 直接查询，避免复杂 JOIN",
            "所有统计查询必须带 LIMIT，默认不超过 200 行",
            *BUSINESS_TERMS,
        ],
        "examples": EXAMPLE_QUERIES,
    }


def train(corpus: dict[str, Any]) -> int:
    """导入 Vanna（未安装时返回 1，由调用方提示安装）。"""
    try:
        import vanna  # noqa: F401
    except ImportError:
        print("未安装 vanna，跳过训练（语料已导出，可手动导入）。安装：pip install vanna>=0.7")
        return 1

    try:
        from app.adapters.nl2sql.vanna_adapter import VannaAdapter

        adapter = VannaAdapter()
        method = getattr(adapter, "train", None)
        if method is None:
            print("VannaAdapter.train 未实现（Vanna 组负责），语料已导出待接入")
            return 1
        result = method(corpus)
        print(f"Vanna 训练完成：{result}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Vanna 训练失败：{exc}")
        return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="准备 / 导入 Vanna 训练数据")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="语料导出路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计")
    args = parser.parse_args()

    corpus = build_corpus()
    stats = {
        "tables": len(corpus["tables"]),
        "ddl_chars": len(corpus["ddl"]),
        "documentation": len(corpus["documentation"]),
        "examples": len(corpus["examples"]),
    }
    print(f"训练语料统计：{stats}")

    if args.dry_run:
        return

    output = Path(args.output)
    if not output.is_absolute():
        output = BASE_DIR / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"语料已导出：{output}")

    raise SystemExit(train(corpus))


if __name__ == "__main__":
    main()
