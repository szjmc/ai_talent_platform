"""准备 / 导入 Vanna 训练数据（架构组 + Vanna 组共同验收）。

标签：[架构组]+[Vanna组] [P1]（DDL + 术语 + 示例问答训练语料）

训练语料三类：
1. DDL：PgSQL 分析库 6 张表的建表语句（直接读 ORM 元数据，保证与代码一致）；
2. 文档：表/字段中文说明（来自 database_design.md 的设计与本文档维护的术语表）；
3. 示例问答：question -> sql（Vanna 的核心 Few-shot 来源）。

用法：
    python scripts/train_vanna.py                       # 生成语料并尝试训练
    python scripts/train_vanna.py --output data/vanna/corpus.json --export-only  # 只导出语料
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
    "人才指 dim_talent_wide 的记录，只有六张分析表可查询，不可查询 MySQL 主表。",
    "部门为 dim_organization，org_id 是部门 ID；path 是不含自身 ID 的祖级路径。",
    "部门快照人数：organization_id > 0、education为空、age_group为空的部门总计行；不要与细分行重复求和。",
    "学历/年龄快照：只固定一个维度，其他维度为空；同一月份按明细部门汇总，排除 organization_id=0。",
    "人才技能分布来自 skill_tags；一个人才可能有多个技能，人数与技能出现次数不同。",
    "平均匹配分 match_avg_score 为0—100分，不是岗位匹配率；没有分母和阈值时拒绝计算匹配率。",
    "学习轨迹可能有同一人才/课程的多次记录；条数不是培训完成率，累计分钟需确认源字段语义。",
    "骨干、继任者、流失率、达标率没有已确认业务定义，不可自行编造阈值或离职事件。",
    "fact_talent_snapshot 月份字段是 snapshot_month，测评时间为 scored_at，其余事实事件为 event_at。",
    "所有用户只能看授权数据；SQL范围由系统执行前注入，不可依赖提示词获得额外权限。"
]

# 至少40条可由现有字段回答的训练示例；新增技能展开与组合条件示例。
EXAMPLE_QUERIES: list[dict[str, str]] = [
    {
        "question": "人才总数是多少？",
        "sql": "SELECT count(*) AS talent_count FROM dim_talent_wide"
    },
    {
        "question": "各学历层次的人才分布？",
        "sql": "SELECT education, count(*) AS talent_count FROM dim_talent_wide GROUP BY education ORDER BY talent_count DESC"
    },
    {
        "question": "各潜力等级的人才数量？",
        "sql": "SELECT potential_level, count(*) AS talent_count FROM dim_talent_wide GROUP BY potential_level"
    },
    {
        "question": "各部门路径的人才数量？",
        "sql": "SELECT org_path, count(*) AS talent_count FROM dim_talent_wide GROUP BY org_path"
    },
    {
        "question": "人才平均工作年限是多少？",
        "sql": "SELECT avg(work_years) AS avg_work_years FROM dim_talent_wide"
    },
    {
        "question": "工作年限最多的十位人才？",
        "sql": "SELECT name, work_years FROM dim_talent_wide ORDER BY work_years DESC NULLS LAST LIMIT 10"
    },
    {
        "question": "各学历人才的平均工作年限？",
        "sql": "SELECT education, avg(work_years) AS avg_work_years FROM dim_talent_wide GROUP BY education"
    },
    {
        "question": "平均匹配分最高的十位人才？",
        "sql": "SELECT name, match_avg_score FROM dim_talent_wide ORDER BY match_avg_score DESC NULLS LAST LIMIT 10"
    },
    {
        "question": "各潜力等级的平均匹配分？",
        "sql": "SELECT potential_level, avg(match_avg_score) AS avg_match_score FROM dim_talent_wide GROUP BY potential_level"
    },
    {
        "question": "工作五年以上有多少人才？",
        "sql": "SELECT count(*) AS talent_count FROM dim_talent_wide WHERE work_years >= 5"
    },
    {
        "question": "各月份各部门的人才总量？",
        "sql": "SELECT snapshot_month, organization_id, talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' ORDER BY snapshot_month"
    },
    {
        "question": "最新月份各部门的人才总量？",
        "sql": "SELECT organization_id, talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) ORDER BY talent_count DESC"
    },
    {
        "question": "每月人才总量趋势？",
        "sql": "SELECT snapshot_month, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' GROUP BY snapshot_month ORDER BY snapshot_month"
    },
    {
        "question": "每月各部门平均工龄？",
        "sql": "SELECT snapshot_month, organization_id, avg_work_years FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' ORDER BY snapshot_month"
    },
    {
        "question": "最近三个月部门人才总计？",
        "sql": "SELECT organization_id, sum(talent_count) AS talent_months FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' AND snapshot_month >= date_trunc('month', current_date) - interval '2 months' GROUP BY organization_id"
    },
    {
        "question": "最新月份学历分布？",
        "sql": "SELECT education, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education <> '' AND age_group = '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) GROUP BY education"
    },
    {
        "question": "最新月份年龄段分布？",
        "sql": "SELECT age_group, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group <> '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) GROUP BY age_group"
    },
    {
        "question": "上个月各部门的人才数量是多少？",
        "sql": "SELECT o.name AS department, sum(f.talent_count) AS talent_count FROM fact_talent_snapshot f JOIN dim_organization o ON o.org_id = f.organization_id WHERE f.snapshot_month = date_trunc('month', current_date - interval '1 month') AND f.organization_id > 0 AND f.education = '' AND f.age_group = '' GROUP BY o.name ORDER BY talent_count DESC"
    },
    {
        "question": "测评平均分是多少？",
        "sql": "SELECT avg(total_score) AS avg_score FROM fact_assessment_history"
    },
    {
        "question": "最近半年测评平均分趋势？",
        "sql": "SELECT date_trunc('month', scored_at) AS month, avg(total_score) AS avg_score FROM fact_assessment_history WHERE scored_at >= current_date - interval '6 months' GROUP BY 1 ORDER BY 1"
    },
    {
        "question": "各能力等级测评次数？",
        "sql": "SELECT level, count(*) AS assessment_count FROM fact_assessment_history GROUP BY level"
    },
    {
        "question": "各岗位的测评平均分？",
        "sql": "SELECT position_id, avg(total_score) AS avg_score FROM fact_assessment_history GROUP BY position_id"
    },
    {
        "question": "测评最高分前十条？",
        "sql": "SELECT talent_id, total_score FROM fact_assessment_history ORDER BY total_score DESC NULLS LAST LIMIT 10"
    },
    {
        "question": "每月测评次数趋势？",
        "sql": "SELECT date_trunc('month', scored_at) AS month, count(*) AS assessment_count FROM fact_assessment_history GROUP BY 1 ORDER BY 1"
    },
    {
        "question": "测评分低于60的次数？",
        "sql": "SELECT count(*) AS assessment_count FROM fact_assessment_history WHERE total_score < 60"
    },
    {
        "question": "参与测评的人才数？",
        "sql": "SELECT count(DISTINCT talent_id) AS talent_count FROM fact_assessment_history"
    },
    {
        "question": "培训轨迹记录数？",
        "sql": "SELECT count(*) AS track_count FROM fact_training_track"
    },
    {
        "question": "各课程累计记录的学习分钟数？",
        "sql": "SELECT course_id, sum(study_minutes) AS recorded_minutes FROM fact_training_track GROUP BY course_id"
    },
    {
        "question": "各学习状态的轨迹条数？",
        "sql": "SELECT status, count(*) AS track_count FROM fact_training_track GROUP BY status"
    },
    {
        "question": "每月培训轨迹次数？",
        "sql": "SELECT date_trunc('month', event_at) AS month, count(*) AS track_count FROM fact_training_track GROUP BY 1 ORDER BY 1"
    },
    {
        "question": "参与学习的人才数？",
        "sql": "SELECT count(DISTINCT talent_id) AS talent_count FROM fact_training_track"
    },
    {
        "question": "各培训计划的参与人才数？",
        "sql": "SELECT plan_id, count(DISTINCT talent_id) AS talent_count FROM fact_training_track GROUP BY plan_id"
    },
    {
        "question": "各人才最新学习事件时间？",
        "sql": "SELECT talent_id, max(event_at) AS last_event_at FROM fact_training_track GROUP BY talent_id"
    },
    {
        "question": "最近七天各行为类型次数？",
        "sql": "SELECT event_type, count(*) AS event_count FROM fact_behavior_event WHERE event_at >= current_date - interval '7 days' GROUP BY event_type"
    },
    {
        "question": "每日行为事件趋势？",
        "sql": "SELECT date_trunc('day', event_at) AS day, count(*) AS event_count FROM fact_behavior_event GROUP BY 1 ORDER BY 1"
    },
    {
        "question": "各页面访问事件数？",
        "sql": "SELECT page, count(*) AS event_count FROM fact_behavior_event GROUP BY page ORDER BY event_count DESC"
    },
    {
        "question": "各事件类型的平均持续时间？",
        "sql": "SELECT event_type, avg(duration_ms) AS avg_duration_ms FROM fact_behavior_event GROUP BY event_type"
    },
    {
        "question": "部门列表及层级？",
        "sql": "SELECT org_id, name, level, path FROM dim_organization ORDER BY org_id"
    },
    {
        "question": "各层级的部门数量？",
        "sql": "SELECT level, count(*) AS department_count FROM dim_organization GROUP BY level"
    },
    {
        "question": "顶层部门有哪些？",
        "sql": "SELECT org_id, name FROM dim_organization WHERE parent_id IS NULL ORDER BY org_id"
    },
    {
        "question": "各项技能分别有多少人才，重复技能不要重复计人？",
        "sql": "SELECT skill, count(*) AS talent_count FROM (SELECT DISTINCT talent_id, trim(unnest(skill_tags)) AS skill FROM dim_talent_wide) s WHERE skill IS NOT NULL AND skill <> '' GROUP BY skill ORDER BY talent_count DESC"
    },
    {
        "question": "本科且工作三年以上、具备Python技能的人才有多少？",
        "sql": "SELECT count(*) AS talent_count FROM dim_talent_wide WHERE education = '本科' AND work_years >= 3 AND talent_id IN (SELECT talent_id FROM (SELECT talent_id, unnest(skill_tags) AS skill FROM dim_talent_wide) s WHERE trim(skill) = 'Python')"
    },
    {
        "question": "各测评等级的有效记录数、平均分、最低分和最高分？",
        "sql": "SELECT level, count(total_score) AS scored_count, avg(total_score) AS average_score, min(total_score) AS minimum_score, max(total_score) AS maximum_score FROM fact_assessment_history GROUP BY level"
    }
]


def build_corpus() -> dict[str, Any]:
    """构造默认检索语料：六表ORM DDL、指标/术语说明、至少40条问题-SQL示例。

    render_pgsql_ddl 只从模型离线生成DDL，不连接数据库；这里不会查询或
    同步业务数据，也不会微调大模型。返回值可导出JSON或交给Adapter.train。
    """
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
            "问数默认返回200行，执行前强制添加服务端LIMIT，硬上限1000行",
            *BUSINESS_TERMS,
        ],
        "examples": EXAMPLE_QUERIES,
    }


def train(corpus: dict[str, Any], *, reset: bool = False) -> int:
    """命令行训练包装：调用 VannaAdapter.train 把语料写入本地记忆。

    reset 决定是否显式清空旧集合；返回0成功、1依赖/入口未就绪、2训练失败。
    是否安装/变更项目依赖须按团队审批流程执行，脚本本身不会自动安装。
    """
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
        result = method(corpus, reset=reset)
        print(f"Vanna 训练完成：{result}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Vanna 训练失败：{exc}")
        return 2


def main() -> None:
    """训练命令入口：组装语料 → 校验 → 统计 → 可选导出/导入。

    --dry-run：只校验并输出统计，不写文件或训练记忆。
    --export-only：导出JSON，不导入记忆；--reset：实际导入时重建三个集合。
    --ddl/--sql覆盖默认DDL/示例，--docs/--terms补充口径和术语。
    """
    parser = argparse.ArgumentParser(description="准备 / 导入 Vanna 训练数据")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="语料导出路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计")
    parser.add_argument("--export-only", action="store_true", help="只导出语料，不写 Vanna 记忆库")
    parser.add_argument("--reset", action="store_true", help="显式重建当前 Vanna 目录中的训练集合")
    parser.add_argument("--ddl", type=Path, help="覆盖默认 ORM DDL 的 UTF-8 文件")
    parser.add_argument("--docs", type=Path, help="补充指标口径 Markdown")
    parser.add_argument("--sql", type=Path, help="示例文件，每段以 -- question: 问题 开头")
    parser.add_argument("--terms", type=Path, help="补充业务术语 YAML 列表")
    args = parser.parse_args()

    # 先加载内置语料，再按显式命令参数覆盖或补充；不从数据库读取训练样本。
    corpus = build_corpus()
    if args.ddl:
        corpus["ddl"] = args.ddl.read_text(encoding="utf-8")
    if args.docs:
        corpus["documentation"].append(args.docs.read_text(encoding="utf-8"))
    if args.terms:
        import yaml
        # safe_load 仅解析普通YAML数据；术语必须是字符串列表。
        terms = yaml.safe_load(args.terms.read_text(encoding="utf-8"))
        if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
            parser.error("--terms 必须为字符串列表 YAML")
        corpus["documentation"].extend(terms)
    if args.sql:
        import re
        # 每个 -- question: 标记开始一组问答，后面的文本作为这一组SQL。
        parts = re.split(r"(?m)^-- question: *", args.sql.read_text(encoding="utf-8"))
        if parts[0].strip() or len(parts) < 2:
            parser.error("--sql 每段必须以 -- question: 问题 开头")
        corpus["examples"] = []
        for part in parts[1:]:
            question, separator, sql = part.partition("\n")
            if not separator or not question.strip() or not sql.strip():
                parser.error("示例缺少问题或 SQL")
            corpus["examples"].append({"question": question.strip(), "sql": sql.strip()})
    from app.adapters.nl2sql.sql_validator import validate_select_sql
    # 全部SQL先过同一套白名单校验；语料不足40条时也不允许进入实际导入。
    for example in corpus["examples"]:
        validate_select_sql(example["sql"])
    if len(corpus["examples"]) < 40:
        parser.error("训练示例必须至少 40 条")
    stats = {
        "tables": len(corpus["tables"]),
        "ddl_chars": len(corpus["ddl"]),
        "documentation": len(corpus["documentation"]),
        "examples": len(corpus["examples"]),
    }
    print(f"训练语料统计：{stats}")

    # dry-run 到此结束：不会创建输出文件，也不会初始化/重建 Chroma。
    if args.dry_run:
        return

    # 导出一份可检查的语料快照；相对输出路径统一相对项目根目录解析。
    output = Path(args.output)
    if not output.is_absolute():
        output = BASE_DIR / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"语料已导出：{output}")

    # export-only 仅保存JSON；普通模式才导入本地训练记忆。
    if not args.export_only:
        raise SystemExit(train(corpus, reset=args.reset))


if __name__ == "__main__":
    main()
