"""独立执行 MySQL -> PgSQL 分析库全量同步（问数 / 看板的数据底座）。

标签：[Vanna组] [P0]（6 张分析表数据来源；契约见 docs/database_design.md「数据分析」

用法：
    python scripts/sync_analytics.py           # 增量同步（默认近 12 个月窗口）
    python scripts/sync_analytics.py --full    # 全量（清空事实表后重灌）

幂等策略：
    - 快照 / 维度表：ON CONFLICT 幂等 upsert（快照唯一键 snapshot_month+org+edu+age）；
    - 事实表（测评 / 培训）：按同步窗口 DELETE + INSERT（无自然唯一键）；
    - 维表差集清理：源端已删除 / 离职 / 部门撤销的行按 id 差集删除（临时表实现）；
    - 行为事件 fact_behavior_event：无 MySQL 来源，跳过并计数 0；
    - PGSQL_ENABLED=false 时直接失败退出（不静默）。

水位与自动化：
    - 每次运行把结果写入控制表 analytics_sync_state（job_name/last_synced_at/status/rows）；
    - 增量运行读取上次成功水位，把窗口收敛到「水位的月初」；失败不推进水位；
    - 触发方式二选一（可并存，靠 Redis 锁互斥）：
        · 应用内 APScheduler：settings.ANALYTICS_SYNC_ENABLED=true（默认 02:00）
        · 系统级 cron / systemd timer：见 deploy/ 目录示例

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


# ================================================================ 同步水位（控制表）
# 注意：这是 ETL 的运行时控制表，不属于 6 张分析表，不计入 ANALYTICS_TABLE_NAMES。
_STATE_TABLE = "analytics_sync_state"
_JOB_NAME = "sync_analytics"

_DDL_STATE = f"""
CREATE TABLE IF NOT EXISTS {_STATE_TABLE} (
    job_name       TEXT PRIMARY KEY,
    last_synced_at TIMESTAMPTZ,
    last_run_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_status    TEXT NOT NULL,
    rows_total     INTEGER NOT NULL DEFAULT 0,
    detail         JSONB,
    note           TEXT
)
"""

_SELECT_STATE = f"SELECT last_synced_at, last_status FROM {_STATE_TABLE} WHERE job_name = :job"

_UPSERT_STATE = f"""
INSERT INTO {_STATE_TABLE}
    (job_name, last_synced_at, last_run_at, last_status, rows_total, detail, note)
VALUES (:job, :last_synced_at, now(), :status, :rows_total, CAST(:detail AS JSONB), :note)
ON CONFLICT (job_name) DO UPDATE SET
    last_synced_at = COALESCE(EXCLUDED.last_synced_at, {_STATE_TABLE}.last_synced_at),
    last_run_at    = now(),
    last_status    = EXCLUDED.last_status,
    rows_total     = EXCLUDED.rows_total,
    detail         = EXCLUDED.detail,
    note           = EXCLUDED.note
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


def _window_bounds(since: date | None = None) -> tuple[date, date]:
    """同步窗口：默认近 SNAPSHOT_MONTHS 个月。

    传入 since（上次成功同步的水位）时收敛到「水位的月初」，既缩小扫描范围，
    又避免只同步半月造成月度快照口径不完整（upsert 幂等，重复跑无副作用）。
    """
    today = date.today()
    start = date(today.year, today.month, 1) - timedelta(days=SNAPSHOT_MONTHS * 31)
    start = date(start.year, start.month, 1)
    if since is not None:
        since_month = date(since.year, since.month, 1)
        if since_month > start:
            start = since_month
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
# asyncpg 单条语句绑定参数上限 32767；按行数分块，取 500 行（即使 20 列也仅 1 万参数）留足余量。
CHUNK_SIZE = 500


async def _exec_many(pg: Any, sql: str, rows: list[dict[str, Any]]) -> int:
    """分块批量写入，避免超过 asyncpg 的单语句参数上限。"""
    if not rows:
        return 0
    statement = text(sql)
    for offset in range(0, len(rows), CHUNK_SIZE):
        await pg.execute(statement, rows[offset : offset + CHUNK_SIZE])
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


# ================================================================ 水位 / 差集清理
async def _ensure_state_table(pg: Any) -> None:
    await pg.execute(text(_DDL_STATE))


async def _read_watermark() -> date | None:
    """读取上次成功同步的水位（日期）；无记录 / 表不存在时返回 None。"""
    async with pg_session() as pg:
        if pg is None:
            return None
        await _ensure_state_table(pg)
        await pg.commit()  # DDL 必须显式提交，pg_session 不自动 commit
        row = (await pg.execute(text(_SELECT_STATE), {"job": _JOB_NAME})).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    return value.date() if isinstance(value, datetime) else value


async def _write_state(
    *,
    status: str,
    counts: dict[str, int] | None,
    window: tuple[date, date] | None,
    note: str | None = None,
) -> None:
    """记录本次同步结果与水位（失败也记录，供 /system/info 与排障使用）。"""
    async with pg_session() as pg:
        if pg is None:
            return
        try:
            await _ensure_state_table(pg)
            await pg.execute(
                text(_UPSERT_STATE),
                {
                    "job": _JOB_NAME,
                    "last_synced_at": (
                        datetime.combine(window[1], datetime.min.time()) if window else None
                    )
                    if status == "success"
                    else None,
                    "status": status,
                    "rows_total": sum((counts or {}).values()),
                    "detail": json.dumps(counts or {}, ensure_ascii=False),
                    "note": note,
                },
            )
            await pg.commit()
        except Exception:  # noqa: BLE001 - 状态记录失败不影响主流程
            logger.warning("写入同步状态失败（忽略）", exc_info=True)


async def _delete_missing(pg: Any, table: str, key_column: str, ids: list[int]) -> int:
    """差集清理：删除源端已不存在（软删除 / 离职 / 部门撤销）的行。

    用临时表承载 id 集合，避免 `NOT IN (:ids)` 撞绑定参数上限；
    临时表 ON COMMIT DROP，随本次同步事务提交自动消失。
    """
    await pg.execute(
        text("CREATE TEMP TABLE IF NOT EXISTS tmp_sync_ids (id BIGINT PRIMARY KEY) ON COMMIT DROP")
    )
    await pg.execute(text("TRUNCATE tmp_sync_ids"))
    if not ids:
        # 源端确实为空时才该清空维表；这里保守跳过，避免上游读取异常导致全表被清空。
        logger.warning("差集清理跳过：%s 本轮源端 id 集合为空", table)
        return 0
    await _exec_many(pg, "INSERT INTO tmp_sync_ids (id) VALUES (:id)", [{"id": int(i)} for i in ids])
    result = await pg.execute(
        text(f"DELETE FROM {table} WHERE {key_column} NOT IN (SELECT id FROM tmp_sync_ids)")  # noqa: S608
    )
    return int(result.rowcount or 0)


# ================================================================ 主流程
async def sync_all(*, full_refresh: bool = False) -> dict[str, int]:
    """MySQL -> PgSQL 全量/增量同步（幂等），返回各表写入行数。

    契约：sync_all(*, full_refresh: bool = False) -> dict[str, int]（database_design）。
    PGSQL_ENABLED=false 时抛 RuntimeError（不静默）。

    增量语义：读取 analytics_sync_state 的水位，把时间窗口收敛到「水位的月初」；
    快照 / 维表始终全量 upsert（数据量小且需保证月度口径完整），
    事实表按窗口 DELETE+INSERT，维表再按 id 差集删除源端已消失的行。
    """
    if not settings.PGSQL_ENABLED:
        raise RuntimeError("PgSQL 未启用（PGSQL_ENABLED=false），无法执行分析库同步")

    watermark = None if full_refresh else await _read_watermark()
    start, end = _window_bounds(watermark)
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
        "deleted_organization": 0,
        "deleted_talent": 0,
    }

    try:
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
            # 差集清理：源端已删除 / 离职 / 部门撤销的行不能留在分析库
            counts["deleted_organization"] = await _delete_missing(
                pg, "dim_organization", "org_id", [int(o["id"]) for o in organizations]
            )
            counts["deleted_talent"] = await _delete_missing(
                pg, "dim_talent_wide", "talent_id", [int(t["id"]) for t in talents]
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

            # pg_session() 是只读语义（不 commit），写入必须显式提交，
            # 否则会话关闭时 SQLAlchemy 会回滚，同步结果全部丢失。
            await pg.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败状态后原样抛出
        await _write_state(
            status="failed", counts=counts, window=(start, end), note=str(exc)[:500]
        )
        raise

    # 成功才推进水位（失败保留旧水位，下轮自动补上这段窗口）
    await _write_state(status="success", counts=counts, window=(start, end))

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
