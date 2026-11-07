"""业务主库降级统计仓库：PgSQL 分析库关闭时，直接聚合 MySQL/SQLite 业务表。

标签：[Vanna组] [P1]（分析降级：PGSQL_ENABLED=False 时的确定性统计）

为什么需要这一层：
    settings / lifespan / README 一致承诺「PGSQL_ENABLED=False 时分析类功能自动
    降级为仅统计 MySQL 数据，不影响业务接口」，但 AnalyticsRepository 只实现了
    PgSQL 分析表（fact_talent_snapshot / dim_talent_wide）版本，关闭 PgSQL 后
    人才驾驶舱、数据洞察全部报「分析库未启用，暂时无法问数」—— 承诺从未兑现。

口径说明（与 PgSQL 快照版的差异，会在 warnings 里向用户明示）：
    - 分布 / 趋势：基于 talents 表实时数据（active 状态），不是月度快照；
      趋势按 created_at 逐月累计（存量口径：该月末已入库且未删除的人数）；
    - 技能分布：skill_tags JSON 数组在应用层展开去重（跨方言，MySQL/SQLite 通用）；
    - 测评统计：assessments + assessment_results 实时聚合，对应分析库的
      fact_assessment_history；
    - NL2SQL 问数（vanna）不在降级范围：它必须走 PgSQL 只读账号，关闭时明确报错。

所有查询只用 SQLAlchemy 表达式 + 应用层聚合，不拼接 SQL 字符串，
因此同一份代码在 MySQL 与 SQLite（本地开发回退）上都能跑。
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import Assessment, AssessmentResult
from app.models.organization import Organization
from app.models.talent import Talent
from app.schemas.analytics_statistics import AnalyticsFilterBase, AnalyticsFilters

# 年龄分组口径（与分析库 fact_talent_snapshot.age_group 一致）
_AGE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("25岁以下", 0, 24),
    ("25-30岁", 25, 30),
    ("31-35岁", 31, 35),
    ("36-45岁", 36, 45),
    ("46岁以上", 46, 200),
)


def _age_bucket(birth: date | None) -> str | None:
    if birth is None:
        return None
    today = date.today()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    for label, low, high in _AGE_BUCKETS:
        if low <= age <= high:
            return label
    return None


def _month_of(value: datetime | date) -> date:
    return date(value.year, value.month, 1)


def _month_end(month: date) -> datetime:
    if month.month == 12:
        nxt = date(month.year + 1, 1, 1)
    else:
        nxt = date(month.year, month.month + 1, 1)
    return datetime(nxt.year, nxt.month, nxt.day) - timedelta(microseconds=1)


class AnalyticsFallbackRepository:
    """PGSQL_ENABLED=False 时的确定性统计仓库（接口与 AnalyticsRepository 对齐）。"""

    # 响应里的 source 标签：前端原样展示，因此用可读名称区分降级来源
    source_name = "业务库实时统计"
    snapshot_source = "业务库实时统计"
    trend_source = "业务库实时统计"

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------ 通用条件
    @staticmethod
    def _talent_conditions(scope: dict, filters: AnalyticsFilterBase) -> list:
        """活跃人才 + 权限范围 + 组织筛选（业务库版，语义对齐 snapshot_scope）。"""
        conditions = [Talent.is_deleted.is_(False), Talent.status == "active"]
        if scope.get("data_scope") != "all":
            org_id = scope.get("organization_id")
            allowed = Organization.id == org_id
            if scope.get("data_scope") == "dept_sub":
                own_path = select(Organization.path).where(Organization.id == org_id).scalar_subquery()
                allowed = allowed.is_(True) | (Organization.path.like(own_path + f"{org_id}/%"))
            subtree = select(Organization.id).where(allowed)
            conditions.append(Talent.organization_id.in_(subtree))
        if getattr(filters, "organization_ids", None):
            conditions.append(Talent.organization_id.in_(filters.organization_ids))
        if getattr(filters, "educations", None):
            conditions.append(Talent.education.in_(filters.educations))
        if getattr(filters, "potential_levels", None):
            conditions.append(Talent.potential_level.in_(filters.potential_levels))
        if getattr(filters, "min_work_years", None) is not None:
            conditions.append(Talent.work_years >= filters.min_work_years)
        if getattr(filters, "max_work_years", None) is not None:
            conditions.append(Talent.work_years <= filters.max_work_years)
        if getattr(filters, "skills", None):
            # 技能筛选在应用层做（JSON 数组跨方言展开，见 skill_distribution）
            conditions.append(Talent.skill_tags.is_not(None))
        return conditions

    async def _iter_talents(self, conditions: list):
        statement = select(
            Talent.id, Talent.name, Talent.education, Talent.birth_date, Talent.organization_id,
            Talent.skill_tags, Talent.potential_level, Talent.work_years,
        ).where(*conditions)
        result = await self.db.execute(statement)
        return result.mappings().all()

    @staticmethod
    def _talent_skills(row) -> list[str]:
        tags = row["skill_tags"] or []
        if isinstance(tags, str):
            tags = re.split(r"[,，;；\s]+", tags)
        seen: list[str] = []
        for tag in tags:
            name = str(tag).strip()
            if name and name not in seen:
                seen.append(name)
        return seen

    # ------------------------------------------------------------ 快照分布
    async def latest_month(self, scope: dict, filters: AnalyticsFilters):
        """实时数据没有快照月份，返回当前月表示「截至本月」。"""
        return date.today().replace(day=1)

    async def snapshot_distribution(self, type_: str, month, scope: dict, filters: AnalyticsFilters) -> list[dict]:
        rows = await self._iter_talents(self._talent_conditions(scope, filters))
        wanted_ages = set(getattr(filters, "age_groups", None) or [])
        wanted_skills = [str(s).strip() for s in (getattr(filters, "skills", None) or [])]

        counts: Counter = Counter()
        for row in rows:
            if wanted_skills:
                owned = set(self._talent_skills(row))
                if filters.skill_mode == "all" and not all(s in owned for s in wanted_skills):
                    continue
                if filters.skill_mode != "all" and not any(s in owned for s in wanted_skills):
                    continue
            if type_ == "education":
                counts[str(row["education"] or "未知")] += 1
            elif type_ == "age":
                bucket = _age_bucket(row["birth_date"])
                if wanted_ages and (bucket is None or bucket not in wanted_ages):
                    continue
                counts[bucket or "未知"] += 1
            else:  # org
                counts[f"org:{row['organization_id']}"] += 1

        if type_ == "org":
            org_rows = (await self.db.execute(select(Organization.id, Organization.name))).all()
            names = {oid: name for oid, name in org_rows}
            items = [
                {"value": str(oid), "label": names.get(oid) or "未知部门", "count": count}
                for oid, count in counts.items()
            ]
        else:
            items = [
                {"value": value, "label": value, "count": count}
                for value, count in counts.items()
            ]
        items.sort(key=lambda item: (-item["count"], str(item["value"])))
        return items[: filters.limit + 1]

    # ------------------------------------------------------------ 趋势
    async def snapshot_trend(self, scope: dict, filters: AnalyticsFilterBase, start_month, end_month) -> list[dict]:
        """月末存量：created_at <= 月末 且未删除的在库人数，按月累计。

        与快照版语义一致（各月存量，非当月新增）；缺期不补 0，由上层标记 missing。
        """
        conditions = self._talent_conditions(scope, filters)
        statement = select(Talent.created_at).where(*conditions)
        created = [value for value in (await self.db.execute(statement)).scalars().all() if value is not None]

        # 逐月统计存量
        months: list[date] = []
        cursor = date(start_month.year, start_month.month, 1)
        while cursor <= end_month:
            months.append(cursor)
            cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)

        created_months = sorted(_month_of(value) for value in created)
        result: list[dict] = []
        for month in months:
            # 该月末之前（含当月）入库的人数
            count = sum(1 for m in created_months if m <= month)
            result.append({"month": month, "value": count})
        return result

    # ------------------------------------------------------------ 技能分布
    async def skill_distribution(self, filters: AnalyticsFilters) -> list[dict]:
        rows = await self._iter_talents(self._talent_conditions({"data_scope": "all"}, filters))
        counter: Counter = Counter()
        no_skill = 0
        for row in rows:
            skills = self._talent_skills(row)
            if not skills:
                no_skill += 1
                continue
            for skill in skills:
                counter[skill] += 1
        items = [
            {"value": skill, "label": skill, "count": count}
            for skill, count in counter.most_common(filters.limit + 1)
        ]
        if no_skill and len(items) <= filters.limit:
            items.append({"value": None, "label": "未知", "count": no_skill})
        return items[: filters.limit + 1]

    # ------------------------------------------------------------ 继任规则引擎取数
    async def talent_wide_list(self) -> list[dict]:
        """人才关键字段清单（继任规则引擎），对齐 DimTalentWide 字段。

        match_avg_score 取 matching_records 里该人才历史匹配分的均值；
        业务库没有匹配记录时为 None，由规则引擎按 0 处理。
        """
        from app.models.matching import MatchingRecord

        rows = await self._iter_talents(
            [Talent.is_deleted.is_(False), Talent.status == "active"]
        )
        score_rows = (
            await self.db.execute(
                select(MatchingRecord.talent_id, func.avg(MatchingRecord.score)).where(
                    MatchingRecord.is_deleted.is_(False)
                ).group_by(MatchingRecord.talent_id)
            )
        ).all()
        avg_scores = {row[0]: float(row[1]) for row in score_rows if row[1] is not None}
        return [
            {
                "talent_id": row["id"],
                "name": row["name"] or "",
                "match_avg_score": avg_scores.get(row["id"]),
                "potential_level": row["potential_level"],
                "work_years": row["work_years"],
            }
            for row in rows
        ]

    async def assessment_pass_rate(self) -> dict[int, float]:
        """按人统计测评达标率（优秀/良好 ÷ 总记录），对齐 FactAssessmentHistory 口径。"""
        statement = (
            select(
                Assessment.talent_id,
                func.count().label("total"),
                func.count().filter(AssessmentResult.level.in_(["优秀", "良好"])).label("passed"),
            )
            .join(AssessmentResult, AssessmentResult.assessment_id == Assessment.id, isouter=True)
            .where(Assessment.is_deleted.is_(False), Assessment.total_score.is_not(None))
            .group_by(Assessment.talent_id)
        )
        rows = (await self.db.execute(statement)).all()
        return {
            row[0]: (float(row[2]) / float(row[1]) if row[1] else 0.0)
            for row in rows
        }

    # ------------------------------------------------------------ 测评统计
    async def assessment_summary(self, filters: AnalyticsFilters) -> dict:
        conditions = [Assessment.is_deleted.is_(False)]
        if getattr(filters, "assessment_ids", None):
            conditions.append(Assessment.id.in_(filters.assessment_ids))
        if getattr(filters, "position_ids", None):
            conditions.append(Assessment.position_id.in_(filters.position_ids))
        if getattr(filters, "levels", None):
            conditions.append(AssessmentResult.level.in_(filters.levels))
        if getattr(filters, "start_at", None):
            conditions.append(AssessmentResult.created_at >= filters.start_at)
        if getattr(filters, "end_at", None):
            conditions.append(AssessmentResult.created_at < filters.end_at)
        if getattr(filters, "min_score", None) is not None:
            conditions.append(AssessmentResult.total_score >= filters.min_score)
        if getattr(filters, "max_score", None) is not None:
            conditions.append(AssessmentResult.total_score <= filters.max_score)

        # 宽表组合条件（学历/潜力/年限/技能）通过 talent 子查询收窄，不复制测评行
        talent_conditions = []
        if getattr(filters, "educations", None):
            talent_conditions.append(Talent.education.in_(filters.educations))
        if getattr(filters, "potential_levels", None):
            talent_conditions.append(Talent.potential_level.in_(filters.potential_levels))
        if getattr(filters, "min_work_years", None) is not None:
            talent_conditions.append(Talent.work_years >= filters.min_work_years)
        if getattr(filters, "max_work_years", None) is not None:
            talent_conditions.append(Talent.work_years <= filters.max_work_years)
        if getattr(filters, "skills", None):
            talent_conditions.append(Talent.skill_tags.is_not(None))
        if talent_conditions:
            conditions.append(
                Assessment.talent_id.in_(select(Talent.id).where(Talent.is_deleted.is_(False), *talent_conditions))
            )

        statement = (
            select(
                func.count().label("record_count"),
                func.count(AssessmentResult.total_score).label("scored_count"),
                func.count(func.distinct(Assessment.talent_id)).label("talent_count"),
                func.avg(AssessmentResult.total_score).label("average_score"),
                func.min(AssessmentResult.total_score).label("minimum_score"),
                func.max(AssessmentResult.total_score).label("maximum_score"),
            )
            .join(AssessmentResult, AssessmentResult.assessment_id == Assessment.id, isouter=True)
            .where(*conditions)
        )
        row = dict((await self.db.execute(statement)).mappings().one())
        # Decimal -> float，避免 JSON 序列化差异
        for key in ("average_score", "minimum_score", "maximum_score"):
            if isinstance(row.get(key), Decimal):
                row[key] = float(row[key])
        return row
