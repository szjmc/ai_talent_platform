"""独立执行 MySQL -> PgSQL 分析库全量同步（问数 / 看板的数据底座）。

标签：[Vanna组] [P0]（6 张分析表数据来源；契约见 docs/database_design.md「数据分析」

用法：
    python scripts/sync_analytics.py           # 增量同步（默认近 12 个月窗口）
    python scripts/sync_analytics.py --full    # 全量（清空事实表后重灌）

幂等策略：
    - 快照：只重建当前月（源表无历史离职/调岗时间，不伪造历史），维度表幂等 upsert；
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
    fact_talent_snapshot   talents（当前月快照：存量 + 学历/年龄段/部门明细，org0=全公司）
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
from datetime import date, datetime, timezone as datetime_timezone, timedelta
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
    (snapshot_month, organization_id, education, age_group, talent_count, avg_work_years)
VALUES (:snapshot_month, :organization_id, :education, :age_group, :talent_count, :avg_work_years)
ON CONFLICT (snapshot_month, organization_id, education, age_group) DO UPDATE SET
    talent_count = EXCLUDED.talent_count,
    avg_work_years = EXCLUDED.avg_work_years,
    updated_at = now()
"""

_UPSERT_DIM_WIDE = """
INSERT INTO dim_talent_wide
    (talent_id, name, org_path, education, skill_tags, work_years, potential_level, match_avg_score)
VALUES (:talent_id, :name, :org_path, :education, :skill_tags, :work_years, :potential_level, :match_avg_score)
ON CONFLICT (talent_id) DO UPDATE SET
    name = EXCLUDED.name,
    org_path = EXCLUDED.org_path,
    education = EXCLUDED.education,
    skill_tags = EXCLUDED.skill_tags,
    work_years = EXCLUDED.work_years,
    potential_level = EXCLUDED.potential_level,
    match_avg_score = EXCLUDED.match_avg_score,
    synced_at = now()
"""

_UPSERT_ORG = """
INSERT INTO dim_organization (org_id, code, name, level, path, parent_id)
VALUES (:org_id, :code, :name, :level, :path, :parent_id)
ON CONFLICT (org_id) DO UPDATE SET
    code = EXCLUDED.code,
    name = EXCLUDED.name,
    level = EXCLUDED.level,
    path = EXCLUDED.path,
    parent_id = EXCLUDED.parent_id,
    synced_at = now()
"""

_INSERT_ASSESSMENT = """
INSERT INTO fact_assessment_history
    (assessment_id, talent_id, position_id, total_score, level, dimension_scores, scored_at)
VALUES (:assessment_id, :talent_id, :position_id, :total_score, :level,
        CAST(:dimension_scores AS JSONB), :scored_at)
"""

_INSERT_TRAINING = """
INSERT INTO fact_training_track
    (talent_id, course_id, plan_id, progress, study_minutes, status, event_at)
VALUES (:talent_id, :course_id, :plan_id, :progress, :study_minutes, :status, :event_at)
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

    存量口径：created_at < 下月初且当前非离职。源表没有离职/调岗时间，
    生产同步只重建当前月；纯函数传入历史月份时仅按当前状态近似，不冒充历史事实。
    """
    rows: list[dict[str, Any]] = []
    for month in months:
        month_end = _month_end(month)
        age_ref = min(date.today(), month_end - timedelta(days=1))
        members: list[dict[str, Any]] = []
        for talent in talents:
            created = _as_date(talent.get("created_at"))
            if created is None or created >= month_end or talent.get("status") == "left":
                continue
            members.append(talent)

        # 学历和年龄分别聚合，避免查询单维分布时漏人；另保留组织和全公司总计。
        grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
        for talent in members:
            org_id = int(talent.get("organization_id") or 0)
            education = str(talent.get("education") or "")
            age_group = age_group_label(talent.get("birth_date"), ref=age_ref)
            for key in (
                (org_id, education, ""),
                (org_id, "", age_group),
                (org_id, "", ""),
                (0, education, ""),
                (0, "", age_group),
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
                    "avg_work_years": (
                        round(sum(work_years) / len(work_years), 2)
                        if work_years
                        else None
                    ),
                }
            )
    return rows


# 从 current_company 字段推导地域信息（不新增 talents 表列）
_CITY_PROVINCE: dict[str, tuple[str, str]] = {
    "北京": ("北京市", "北京"), "上海": ("上海市", "上海"), "天津": ("天津市", "天津"),
    "重庆": ("重庆市", "重庆"), "广州": ("广东省", "广州"), "深圳": ("广东省", "深圳"),
    "珠海": ("广东省", "珠海"), "东莞": ("广东省", "东莞"), "佛山": ("广东省", "佛山"),
    "中山": ("广东省", "中山"), "惠州": ("广东省", "惠州"),
    "杭州": ("浙江省", "杭州"), "宁波": ("浙江省", "宁波"), "温州": ("浙江省", "温州"),
    "南京": ("江苏省", "南京"), "苏州": ("江苏省", "苏州"), "无锡": ("江苏省", "无锡"),
    "成都": ("四川省", "成都"), "武汉": ("湖北省", "武汉"), "西安": ("陕西省", "西安"),
    "厦门": ("福建省", "厦门"), "福州": ("福建省", "福州"),
    "长沙": ("湖南省", "长沙"), "青岛": ("山东省", "青岛"), "济南": ("山东省", "济南"),
    "大连": ("辽宁省", "大连"), "沈阳": ("辽宁省", "沈阳"), "长春": ("吉林省", "长春"),
    "哈尔滨": ("黑龙江省", "哈尔滨"), "石家庄": ("河北省", "石家庄"),
    "太原": ("山西省", "太原"), "南昌": ("江西省", "南昌"), "贵阳": ("贵州省", "贵阳"),
    "昆明": ("云南省", "昆明"), "兰州": ("甘肃省", "兰州"), "海口": ("海南省", "海口"),
    "南宁": ("广西壮族自治区", "南宁"), "合肥": ("安徽省", "合肥"), "郑州": ("河南省", "郑州"),
}
_COUNTRY_MAP: dict[str, tuple[str, str]] = {
    "中国": ("中国", "CN"), "美国": ("美国", "US"), "新加坡": ("新加坡", "SG"),
    "德国": ("德国", "DE"), "英国": ("英国", "GB"), "日本": ("日本", "JP"),
    "法国": ("法国", "FR"), "加拿大": ("加拿大", "CA"), "澳大利亚": ("澳大利亚", "AU"),
    "印度": ("印度", "IN"), "韩国": ("韩国", "KR"), "以色列": ("以色列", "IL"),
    "荷兰": ("荷兰", "NL"), "瑞士": ("瑞士", "CH"), "瑞典": ("瑞典", "SE"),
    "西班牙": ("西班牙", "ES"), "意大利": ("意大利", "IT"), "爱尔兰": ("爱尔兰", "IE"),
    "新西兰": ("新西兰", "NZ"), "马来西亚": ("马来西亚", "MY"), "泰国": ("泰国", "TH"),
    "越南": ("越南", "VN"), "印度尼西亚": ("印度尼西亚", "ID"), "菲律宾": ("菲律宾", "PH"),
    "俄罗斯": ("俄罗斯", "RU"),
}


def derive_location(current_company: str | None) -> dict[str, str | None]:
    """从 current_company 字段推导国家/省份/城市（不依赖新增数据库列）。"""
    if not current_company:
        return {"country": None, "country_code": None, "province": None, "work_city": None}
    text = str(current_company)
    # 优先匹配国家名
    for cn_name, (country, code) in _COUNTRY_MAP.items():
        if cn_name in text:
            return {"country": country, "country_code": code, "province": None, "work_city": None}
    # 匹配城市名 → 推导省份
    for city, (province, work_city) in _CITY_PROVINCE.items():
        if city in text:
            return {"country": "中国", "country_code": "CN", "province": province, "work_city": work_city}
    return {"country": None, "country_code": None, "province": None, "work_city": None}


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
                "name": talent.get("name"),
                "org_path": org_paths.get(org_id, ""),
                "education": str(talent.get("education") or ""),
                "skill_tags": _parse_skill_tags(talent.get("skill_tags")),
                "work_years": (
                    float(talent["work_years"])
                    if talent.get("work_years") is not None
                    else None
                ),
                "potential_level": talent.get("potential_level"),
                "match_avg_score": match_scores.get(int(talent["id"])),
                # 优先使用 talents 表地域字段；字段为空时从 current_company 推导
                "country": talent.get("country") or derive_location(talent.get("current_company"))["country"],
                "country_code": talent.get("country_code") or derive_location(talent.get("current_company"))["country_code"],
                "province": talent.get("province") or derive_location(talent.get("current_company"))["province"],
                "work_city": talent.get("work_city") or derive_location(talent.get("current_company"))["work_city"],
                "job_level": talent.get("job_level"),
            }
        )
    return rows


def build_org_rows(organizations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建 dim_organization 行（path 缺失时按 id 自构 /{id}/）。"""
    rows: list[dict[str, Any]] = []
    for org in organizations:
        org_id = int(org["id"])
        path = org.get("path") or "/"
        rows.append(
            {
                "org_id": org_id,
                "code": org.get("code"),
                "name": org.get("name"),
                "level": int(org.get("level") or 1),
                "path": path,
                "parent_id": org.get("parent_id"),
            }
        )
    return rows


def _window_bounds(since: date | None = None) -> tuple[date, date]:
    """同步窗口：默认近 SNAPSHOT_MONTHS 个月。

    传入 since（上次成功同步的水位）时收敛到「水位的月初」，既缩小扫描范围，
    又避免只同步半月造成月度快照口径不完整（upsert 幂等，重复跑无副作用）。
    """
    today = date.today()
    month_index = today.year * 12 + (today.month - 1) - (SNAPSHOT_MONTHS - 1)
    start = date(month_index // 12, month_index % 12 + 1, 1)
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
                    "SELECT id, name, organization_id, education, birth_date, "
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
                    "SELECT a.id AS assessment_id, a.talent_id, a.position_id, "
                    "COALESCE(r.created_at, a.updated_at) AS scored_at, "
                    "r.total_score, r.level, r.dimension_scores "
                    "FROM assessments a JOIN assessment_results r "
                    "ON r.assessment_id = a.id AND r.is_deleted = 0 "
                    "WHERE a.is_deleted = 0 AND a.status = 'scored'"
                )
            )
        ).all()
    ]
    training = [
        dict(row._mapping)
        for row in (
            await db.execute(
                text(
                    "SELECT talent_id, course_id, plan_id, progress, "
                    "COALESCE(last_study_at, updated_at, created_at) AS event_at, "
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
                    "FROM matching_records WHERE is_deleted = 0 GROUP BY talent_id"
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


def _aware_timestamp(value: Any) -> datetime | None:
    """MySQL DATETIME 按平台约定的服务器本地时间解释，再转为 PgSQL 时区时间。"""
    if not isinstance(value, datetime):
        return None
    return value.astimezone() if value.tzinfo is None else value.astimezone(datetime_timezone.utc)


def _assessment_rows(assessments: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assessments:
        scored_at = _aware_timestamp(item.get("scored_at"))
        if scored_at is None or not (start <= scored_at.date() < end):
            continue
        dimensions = item.get("dimension_scores")
        rows.append({
            "assessment_id": int(item["assessment_id"]),
            "talent_id": int(item["talent_id"]),
            "position_id": item.get("position_id"),
            "scored_at": scored_at,
            "total_score": float(item["total_score"]) if item.get("total_score") is not None else None,
            "level": item.get("level"),
            "dimension_scores": dimensions if isinstance(dimensions, str)
                                else json.dumps(dimensions or {}, ensure_ascii=False),
        })
    return rows


def _training_rows(records: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in records:
        event_at = _aware_timestamp(item.get("event_at"))
        if event_at is None or not (start <= event_at.date() < end):
            continue
        rows.append({
            "talent_id": int(item["talent_id"]),
            "course_id": int(item["course_id"]),
            "plan_id": item.get("plan_id"),
            "progress": float(item.get("progress") or 0),
            "event_at": event_at,
            "study_minutes": int(item.get("study_minutes") or 0),
            "status": str(item["status"]),
        })
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
            {"start": datetime.combine(start, datetime.min.time()).astimezone(),
             "end": datetime.combine(end, datetime.min.time()).astimezone()},
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
                    "last_synced_at": datetime.now().astimezone() if status == "success" else None,
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
    # 查询成功但源端为空是合法状态：临时表为空时差集删除会清空目标维表。
    # 上游读取异常会在 _load_mysql_payload 阶段抛出，不会走到这里。
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
    当前月快照在事务内 DELETE+INSERT，历史月份保留既有同步结果；维表全量 upsert，
    事实表按窗口 DELETE+INSERT，维表再按 id 差集删除源端已消失的行。
    """
    if not settings.PGSQL_ENABLED:
        raise RuntimeError("PgSQL 未启用（PGSQL_ENABLED=false），无法执行分析库同步")

    watermark = None if full_refresh else await _read_watermark()
    start, end = _window_bounds(watermark)
    snapshot_month = date.today().replace(day=1)

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as db:
            payload = await _load_mysql_payload(db)
    finally:
        await engine.dispose()

    talents: list[dict[str, Any]] = payload["talents"]
    organizations: list[dict[str, Any]] = payload["organizations"]
    # 与 MySQL organizations.path / dim_organization.path 同口径：只含祖先，不含自身。
    org_paths = {int(org["id"]): str(org.get("path") or "/") for org in organizations}

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

            # 当前月可能有人离职/调岗/学历变更，先删除旧分组再写入，避免残留 stale 计数。
            await pg.execute(
                text("DELETE FROM fact_talent_snapshot WHERE snapshot_month = :month"),
                {"month": snapshot_month},
            )
            counts["fact_talent_snapshot"] = await _exec_many(
                pg, _UPSERT_SNAPSHOT, build_snapshot_rows(talents, [snapshot_month]),
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
                pg, "dim_talent_wide", "talent_id", [int(t["id"]) for t in talents if t.get("status") != "left"]
            )
            counts["fact_assessment_history"] = await _replace_window(
                pg,
                "fact_assessment_history",
                _INSERT_ASSESSMENT,
                start,
                end,
                _assessment_rows(payload["assessments"], start, end),
                "scored_at",
                full_refresh,
            )
            counts["fact_training_track"] = await _replace_window(
                pg,
                "fact_training_track",
                _INSERT_TRAINING,
                start,
                end,
                _training_rows(payload["training"], start, end),
                "event_at",
                full_refresh,
            )
            # 当前没有可靠的人才行为来源，清除历史导入/演示行，禁止继续作为真实分析结果。
            await pg.execute(text("DELETE FROM fact_behavior_event"))

            # pg_session() 是只读语义（不 commit），写入必须显式提交，
            # 否则会话关闭时 SQLAlchemy 会回滚，同步结果全部丢失。
            await pg.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败状态后原样抛出
        try:
            await _write_state(
                status="failed", counts=counts, window=(start, end), note=str(exc)[:500]
            )
        except Exception as state_exc:  # noqa: BLE001
            logger.warning("记录同步失败状态也失败：%s", state_exc)
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
