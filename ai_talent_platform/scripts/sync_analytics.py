"""独立执行 MySQL -> PgSQL 分析库全量同步（问数 / 看板的数据底座）。

标签：[Vanna组] [P0]（6 张分析表数据来源；契约见 docs/database_design.md「数据分析」

用法：
    python scripts/sync_analytics.py           # 增量同步（默认近 12 个月窗口）
    python scripts/sync_analytics.py --full    # 全量（清空事实表后重灌）

幂等策略：
    - 快照 / 维度表：ON CONFLICT 幂等 upsert（快照唯一键 snapshot_month+org+edu+age）；
    - 事实表（测评 / 培训）：按同步窗口 DELETE + INSERT（无自然唯一键）；
    - 行为事件 fact_behavior_event：无 MySQL 来源，跳过并计数 0；
    - PGSQL_ENABLED=false 时直接失败退出（不静默）。

表与来源：
    fact_talent_snapshot   talents（按月快照：存量 + 学历/年龄段/部门明细，org0=全公司）
    fact_assessment_history assessments JOIN assessment_results（近 12 个月）
    fact_training_track    learning_records（近 12 个月，course_id 非空）
    fact_behavior_event    无来源（跳过）
    dim_organization       organizations（全量）
    dim_talent_wide        talents（在职+储备）+ 部门链路 + 平均匹配分
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.infrastructure.database.postgres import pg_session  # noqa: E402

logger = logging.getLogger(__name__)

SNAPSHOT_MONTHS = 12

_UPSERT_SNAPSHOT = """
INSERT INTO fact_talent_snapshot
    (snapshot_month, organization_id, education, age_group, talent_count, work_years_avg)
VALUES (:snapshot_month, :organization_id, :education, :age_group, :talent_count, :work_years_avg)
ON CONFLICT (snapshot_month, organization_id, education, age_group) DO UPDATE SET
    talent_count = EXCLUDED.talent_count,
    work_years_avg = EXCLUDED.work_years_avg
"""

_UPSERT_DIM_WIDE = """
INSERT INTO dim_talent_wide
    (talent_id, talent_no, name, org_id, org_path, education, age_group, skill_tags,
     work_years, potential_level, match_score, snapshot_month)
VALUES (:talent_id, :talent_no, :name, :org_id, :org_path, :education, :age_group, :skill_tags,
     :work_years, :potential_level, :match_score, :snapshot_month)
ON CONFLICT (talent_id) DO UPDATE SET
    talent_no = EXCLUDED.talent_no,
    name = EXCLUDED.name,
    org_id = EXCLUDED.org_id,
    org_path = EXCLUDED.org_path,
    education = EXCLUDED.education,
    age_group = EXCLUDED.age_group,
    skill_tags = EXCLUDED.skill_tags,
    work_years = EXCLUDED.work_years,
    potential_level = EXCLUDED.potential_level,
    match_score = EXCLUDED.match_score,
    snapshot_month = EXCLUDED.snapshot_month
"""

_UPSERT_ORG = """
INSERT INTO dim_organization
    (org_id, org_code, org_name, org_level, org_path, parent_org_id)
VALUES (:org_id, :org_code, :org_name, :org_level, :org_path, :parent_org_id)
ON CONFLICT (org_id) DO UPDATE SET
    org_code = EXCLUDED.org_code,
    org_name = EXCLUDED.org_name,
    org_level = EXCLUDED.org_level,
    org_path = EXCLUDED.org_path,
    parent_org_id = EXCLUDED.parent_org_id
"""

_INSERT_ASSESSMENT = """
INSERT INTO fact_assessment_history
    (talent_id, submitted_at, assessment_type, total_score, level, dimension_scores)
VALUES (:talent_id, :submitted_at, :assessment_type, :total_score, :level, :dimension_scores)
"""

_INSERT_TRAINING = """
INSERT INTO fact_training_track
    (talent_id, course_id, learn_date, study_minutes, completion_status)
VALUES (:talent_id, :course_id, :learn_date, :study_minutes, :completion_status)
"""


# ================================================================ 纯函数（可单测）
def age_group_label(birth_date: date | datetime | None, ref: date | None = None) -> str:
    """年龄段标签：'<25' / '25-30' / '30-35' / '35-40' / '40+'（空值 -> ''）。"""
    if not birth_date:
        return ""
    ref = ref or date.today()
    born = birth_date.date() if isinstance(birth_date, datetime) else birth_date
    age = ref.year - born.year - ((ref.month, ref.day) < (born.month, born.day))
    if age < 25:
        return "<25"
    if age < 30:
        return "25-30"
    if age < 35:
        return "30-35"
    if age < 40:
        return "35-40"
    return "40+"


def month_iter(start: date, end: date) -> Iterable[date]:
    """逐月产出（含 start、含 end 所在月的 1 号）。"""
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        yield cur
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)


def _month_end(month: date) -> date:
    return date(month.year + (month.month // 12), (month.month % 12) + 1, 1)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _parse_skill_tags(raw: Any) -> list[str]:
    """skill_tags 可能是 JSON 字符串 / Python 字面量列表，尽力解析。"""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw or not isinstance(raw, str):
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (ValueError, SyntaxError, TypeError):
            continue
    return []


def build_snapshot_rows(
    talents: list[dict[str, Any]],
    months: list[date],
) -> list[dict[str, Any]]:
    """按月构建快照明细（每组织 存量+学历+年龄段 三类行，另加 organization_id=0 汇总）。

    存量口径：created_at <= 月末 且（非离职 或 离职月 >= 快照月）；
    离职月 = updated_at 所在月（talents 无 left_at，与趋势口径一致）。
    """
    rows: list[dict[str, Any]] = []
    for month in months:
        month_end = _month_end(month)
        members: list[dict[str, Any]] = []
        for talent in talents:
            created = _as_date(talent.get("created_at"))
            if created is None or created >= month_end:
                continue
            if talent.get("status") == "left":
                left_month = _as_date(talent.get("updated_at"))
                left_floor = (
                    date(left_month.year, left_month.month, 1) if left_month else None
                )
                if left_floor is None or left_floor < month:
                    continue
            members.append(talent)

        # 每成员贡献 4 类明细行：组织×学历、组织合计、全公司×学历、全公司合计
        grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
        for talent in members:
            org_id = int(talent.get("organization_id") or 0)
            education = str(talent.get("education") or "")
            age_group = age_group_label(talent.get("birth_date"), ref=month_end)
            for key in (
                (org_id, education, age_group),
                (org_id, "", ""),
                (0, education, age_group),
                (0, "", ""),
            ):
                grouped.setdefault(key, []).append(talent)

        for key in sorted(grouped):
            org_id, education, age_group = key
            members_of = grouped.get(key, [])
            work_years = [
                float(t["work_years"])
                for t in members_of
                if t.get("work_years") is not None
            ]
            rows.append(
                {
                    "snapshot_month": month,
                    "organization_id": org_id,
                    "education": education,
                    "age_group": age_group,
                    "talent_count": len(members_of),
                    "work_years_avg": (
                        round(sum(work_years) / len(work_years), 2)
                        if work_years
                        else None
                    ),
                }
            )
    return rows


def build_dim_talent_wide_rows(
    talents: list[dict[str, Any]],
    org_paths: dict[int, str],
    match_scores: dict[int, float],
    snapshot_month: date | None = None,
) -> list[dict[str, Any]]:
    """构建 dim_talent_wide 行（在职+储备；部门链路 + 平均匹配分）。"""
    snapshot_month = snapshot_month or date.today()
    rows: list[dict[str, Any]] = []
    for talent in talents:
        if talent.get("status") == "left":
            continue
        org_id = int(talent.get("organization_id") or 0)
        rows.append(
            {
                "talent_id": int(talent["id"]),
                "talent_no": talent.get("talent_no"),
                "name": talent.get("name"),
                "org_id": org_id,
                "org_path": org_paths.get(org_id, f"/{org_id}/" if org_id else ""),
                "education": str(talent.get("education") or ""),
                "age_group": age_group_label(talent.get("birth_date")),
                "skill_tags": _parse_skill_tags(talent.get("skill_tags")),
                "work_years": (
                    float(talent["work_years"])
                    if talent.get("work_years") is not None
                    else None
                ),
                "potential_level": talent.get("potential_level"),
                "match_score": match_scores.get(int(talent["id"])),
                "snapshot_month": snapshot_month,
            }
        )
    return rows


def build_org_rows(organizations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建 dim_organization 行（path 缺失时按 id 自构 /{id}/）。"""
    rows: list[dict[str, Any]] = []
    for org in organizations:
        org_id = int(org["id"])
        path = org.get("path") or (f"/{org_id}/" if org_id else "")
        rows.append(
            {
                "org_id": org_id,
                "org_code": org.get("code"),
                "org_name": org.get("name"),
                "org_level": int(org.get("level") or 0),
                "org_path": path,
                "parent_org_id": org.get("parent_id"),
            }
        )
    return rows


def _window_bounds() -> tuple[date, date]:
    today = date.today()
    start = date(today.year, today.month, 1) - timedelta(days=SNAPSHOT_MONTHS * 31)
    start = date(start.year, start.month, 1)
    end = _month_end(date(today.year, today.month, 1))
    return start, end


# ================================================================ MySQL -> 载荷
async def _load_mysql_payload(db: AsyncSession) -> dict[str, Any]:
    talents = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT id, talent_no, name, organization_id, education, birth_date, "
                    "work_years, skill_tags, potential_level, status, created_at, updated_at "
                    "FROM talents WHERE is_deleted = 0"
                )
            )
        ).all()
    ]
    organizations = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT id, code, name, level, path, parent_id "
                    "FROM organizations WHERE is_deleted = 0"
                )
            )
        ).all()
    ]
    assessments = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT a.talent_id, a.submitted_at, a.title, a.total_score, "
                    "r.level, r.dimension_scores "
                    "FROM assessments a LEFT JOIN assessment_results r "
                    "ON r.assessment_id = a.id "
                    "WHERE a.is_deleted = 0 AND a.status = 'submitted' "
                    "AND a.submitted_at IS NOT NULL"
                )
            )
        ).all()
    ]
    training = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT talent_id, course_id, last_study_at, created_at, "
                    "study_minutes, status FROM learning_records "
                    "WHERE is_deleted = 0 AND course_id IS NOT NULL"
                )
            )
        ).all()
    ]
    scores = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT talent_id, AVG(score) AS avg_score "
                    "FROM matching_records GROUP BY talent_id"
                )
            )
        ).all()
    ]
    return {
        "talents": talents,
        "organizations": organizations,
        "assessments": assessments,
        "training": training,
        "match_scores": {
            int(row["talent_id"]): round(float(row["avg_score"] or 0), 2)
            for row in scores
            if row.get("talent_id") is not None
        },
    }


def _assessment_rows(
    assessments: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assessments:
        submitted = _as_date(item.get("submitted_at"))
        if submitted is None or not (start <= submitted < end):
            continue
        rows.append(
            {
                "talent_id": int(item["talent_id"]),
                "submitted_at": item["submitted_at"],
                "assessment_type": item.get("title") or "综合测评",
                "total_score": float(item["total_score"] or 0),
                "level": item.get("level"),
                "dimension_scores": (
                    item.get("dimension_scores")
                    if isinstance(item.get("dimension_scores"), str)
                    else json.dumps(item.get("dimension_scores") or {}, ensure_ascii=False)
                ),
            }
        )
    return rows


def _training_rows(
    records: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in records:
        learn_day = _as_date(item.get("last_study_at")) or _as_date(item.get("created_at"))
        if learn_day is None:
            continue
        if not (start <= learn_day < end):
            continue
        rows.append(
            {
                "talent_id": int(item["talent_id"]),
                "course_id": int(item["course_id"]),
                "learn_date": learn_day,
                "study_minutes": int(item.get("study_minutes") or 0),
                "completion_status": str(item.get("status") or "in_progress"),
            }
        )
    return rows


# ================================================================ PgSQL 写入
async def _exec_many(pg: Any, sql: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    await pg.execute(text(sql), rows)
    return len(rows)


async def _replace_window(
    pg: Any,
    table: str,
    insert_sql: str,
    start: date,
    end: date,
    rows: list[dict[str, Any]],
    time_column: str,
    full_refresh: bool,
) -> int:
    """事实表窗口替换：DELETE（窗口或全量）+ INSERT。"""
    if full_refresh:
        await pg.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - 表名为内部常量
    else:
        await pg.execute(
            text(
                f"DELETE FROM {table} WHERE {time_column} >= :start "  # noqa: S608
                f"AND {time_column} < :end"
            ),
            {"start": datetime.combine(start, datetime.min.time()),
             "end": datetime.combine(end, datetime.min.time())},
        )
    return await _exec_many(pg, insert_sql, rows)


# ================================================================ 主流程
async def sync_all(*, full_refresh: bool = False) -> dict[str, int]:
    """MySQL -> PgSQL 全量/增量同步（幂等），返回各表写入行数。

    契约：sync_all(*, full_refresh: bool = False) -> dict[str, int]（database_design）。
    PGSQL_ENABLED=false 时抛 RuntimeError（不静默）。
    """
    if not settings.PGSQL_ENABLED:
        raise RuntimeError("PgSQL 未启用（PGSQL_ENABLED=false），无法执行分析库同步")

    start, end = _window_bounds()
    months = [m for m in month_iter(start, end)]

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as db:
            payload = await _load_mysql_payload(db)
    finally:
        await engine.dispose()

    talents: list[dict[str, Any]] = payload["talents"]
    organizations: list[dict[str, Any]] = payload["organizations"]
    org_paths = {
        int(org["id"]): str(org.get("path") or f"/{org['id']}/") for org in organizations
    }

    counts: dict[str, int] = {
        "fact_talent_snapshot": 0,
        "fact_assessment_history": 0,
        "fact_training_track": 0,
        "fact_behavior_event": 0,
        "dim_organization": 0,
        "dim_talent_wide": 0,
    }

    async with pg_session() as pg:
        if pg is None:
            raise RuntimeError("PgSQL 会话不可用，请检查 PGSQL_* 配置")

        counts["fact_talent_snapshot"] = await _exec_many(
            pg,
            _UPSERT_SNAPSHOT,
            build_snapshot_rows(talents, months),
        )
        counts["dim_organization"] = await _exec_many(
            pg, _UPSERT_ORG, build_org_rows(organizations)
        )
        counts["dim_talent_wide"] = await _exec_many(
            pg,
            _UPSERT_DIM_WIDE,
            build_dim_talent_wide_rows(talents, org_paths, payload["match_scores"]),
        )
        counts["fact_assessment_history"] = await _replace_window(
            pg,
            "fact_assessment_history",
            _INSERT_ASSESSMENT,
            start,
            end,
            _assessment_rows(payload["assessments"], start, end),
            "submitted_at",
            full_refresh,
        )
        counts["fact_training_track"] = await _replace_window(
            pg,
            "fact_training_track",
            _INSERT_TRAINING,
            start,
            end,
            _training_rows(payload["training"], start, end),
            "learn_date",
            full_refresh,
        )
        # fact_behavior_event：无 MySQL 来源，保持 0（见模块 docstring）

    if counts["fact_behavior_event"] == 0:
        logger.info("fact_behavior_event 无数据来源，跳过同步（计数 0）")
    logger.info(
        "分析库同步完成（window=%s ~ %s, full_refresh=%s）：%s",
        start,
        end,
        full_refresh,
        counts,
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL -> PgSQL 分析库同步（幂等）")
    parser.add_argument("--full", action="store_true", help="全量：先清空事实表再重灌")
    args = parser.parse_args()
    setup_logging()
    try:
        counts = asyncio.run(sync_all(full_refresh=args.full))
    except Exception as exc:  # noqa: BLE001 - CLI 统一退出码
        print(f"同步失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
