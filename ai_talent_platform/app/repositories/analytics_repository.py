"""PgSQL确定性分析仓库：只用SQLAlchemy表达式聚合，不执行客户端SQL。

标签：[Vanna组] [P1]（统计与组合筛选数据访问）

Service传入服务端重算的权限范围。本仓库不commit；受限用户仅允许有明确
部门键的快照/组织表。技能按人才-技能去重计数，不能按整个数组组合分组。
"""
from __future__ import annotations

from sqlalchemy import String, cast, distinct, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import DimOrganization, DimTalentWide, FactAssessmentHistory, FactTalentSnapshot
from app.schemas.analytics_statistics import AnalyticsFilterBase, AnalyticsFilters


class AnalyticsRepository:
    """只读聚合仓库；字段适配及可用性校验由Service先完成。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def snapshot_scope(scope: dict, filters: AnalyticsFilterBase) -> list:
        """以与NL2SQL相同的组织维表/祖先路径限制快照，客户端只能缩小范围。"""
        conditions = [FactTalentSnapshot.organization_id > 0]
        if scope["data_scope"] != "all":
            org_id = scope["organization_id"]
            allowed = DimOrganization.org_id == org_id
            if scope["data_scope"] == "dept_sub":
                own_path = select(DimOrganization.path).where(DimOrganization.org_id == org_id).scalar_subquery()
                allowed = or_(allowed, DimOrganization.path.like(own_path + f"{org_id}/%"))
            conditions.append(FactTalentSnapshot.organization_id.in_(select(DimOrganization.org_id).where(allowed)))
        if filters.organization_ids:
            conditions.append(FactTalentSnapshot.organization_id.in_(filters.organization_ids))
        return conditions

    @staticmethod
    def wide_filters(filters: AnalyticsFilters) -> list:
        """宽表可证实的组合条件：学历、潜力、年限及技能数组。"""
        conditions = []
        for values, column in ((filters.educations, DimTalentWide.education),
                               (filters.potential_levels, DimTalentWide.potential_level)):
            if values:
                conditions.append(column.in_(values))
        if filters.min_work_years is not None:
            conditions.append(DimTalentWide.work_years >= filters.min_work_years)
        if filters.max_work_years is not None:
            conditions.append(DimTalentWide.work_years <= filters.max_work_years)
        if filters.skills:
            tags = list(dict.fromkeys(filters.skills))
            # 与技能分布/训练语料使用同一去空白规则，避免“显示Python却筛不到”。
            # UNNEST放在相关EXISTS中，既不复制外层人才行，也不改模型ARRAY类型。
            expanded = func.unnest(DimTalentWide.skill_tags).table_valued("tag").render_derived(name="filter_skills")
            skill = func.btrim(expanded.c.tag)
            matching = select(literal(1)).select_from(expanded).where(skill.in_(tags))
            if filters.skill_mode == "all":
                matching = matching.having(func.count(distinct(skill)) == len(tags))
            conditions.append(matching.exists())
        return conditions

    async def latest_month(self, scope: dict, filters: AnalyticsFilters):
        statement = select(func.max(FactTalentSnapshot.snapshot_month)).where(*self.snapshot_scope(scope, filters))
        return (await self.db.execute(statement)).scalar_one()

    async def snapshot_distribution(self, type_: str, month, scope: dict, filters: AnalyticsFilters) -> list[dict]:
        """只选一个快照层级，避免汇总行与学历/年龄细分行重复求和。"""
        table = FactTalentSnapshot
        conditions = self.snapshot_scope(scope, filters) + [table.snapshot_month == month]
        conditions += self.snapshot_layer(type_, filters)
        key = {"education": table.education, "age": table.age_group, "org": table.organization_id}[type_]
        label = key if type_ != "org" else func.coalesce(DimOrganization.name, cast(key, String))
        statement = select(key.label("value"), label.label("label"),
                           func.sum(table.talent_count).label("count")).select_from(table)
        if type_ == "org":
            statement = statement.outerjoin(DimOrganization, DimOrganization.org_id == table.organization_id)
        statement = statement.where(*conditions).group_by(key, label).order_by(func.sum(table.talent_count).desc(), key)
        result = await self.db.execute(statement.limit(filters.limit + 1))
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    def snapshot_layer(type_: str, filters: AnalyticsFilterBase) -> list:
        """选取唯一快照层级；调用前Service已拒绝没有口径依据的交叉层级。"""
        table = FactTalentSnapshot
        if type_ == "education" or filters.educations:
            conditions = [table.education != "", table.age_group == ""]
        elif type_ == "age" or filters.age_groups:
            conditions = [table.education == "", table.age_group != ""]
        else:
            conditions = [table.education == "", table.age_group == ""]
        if filters.educations:
            conditions.append(table.education.in_(filters.educations))
        if filters.age_groups:
            conditions.append(table.age_group.in_(filters.age_groups))
        return conditions

    async def snapshot_trend(self, scope: dict, filters: AnalyticsFilterBase, start_month, end_month) -> list[dict]:
        table = FactTalentSnapshot
        statement = select(table.snapshot_month.label("month"), func.sum(table.talent_count).label("value")).where(
            *self.snapshot_scope(scope, filters), *self.snapshot_layer("org", filters),
            table.snapshot_month >= start_month, table.snapshot_month <= end_month,
        ).group_by(table.snapshot_month).order_by(table.snapshot_month)
        return [dict(row) for row in (await self.db.execute(statement)).mappings().all()]

    async def skill_distribution(self, filters: AnalyticsFilters) -> list[dict]:
        """每个人的重复技能只计一次；无有效技能的人员保留为未知，不编造技能。"""
        wide = select(DimTalentWide.talent_id, DimTalentWide.skill_tags).where(*self.wide_filters(filters)).subquery()
        expanded = select(wide.c.talent_id, func.unnest(wide.c.skill_tags).label("skill")).subquery()
        valid = select(expanded.c.talent_id, func.btrim(expanded.c.skill).label("skill")).where(
            expanded.c.skill.is_not(None), func.btrim(expanded.c.skill) != "",
        ).distinct().subquery()
        known = select(valid.c.skill.label("value"), valid.c.skill.label("label"),
                       func.count().label("count")).group_by(valid.c.skill)
        unknown = select(literal(None).label("value"), literal("未知").label("label"),
                         func.count().label("count")).select_from(wide).where(
            ~select(valid.c.talent_id).where(valid.c.talent_id == wide.c.talent_id).exists(),
        ).having(func.count() > 0)
        buckets = union_all(known, unknown).subquery()
        statement = select(buckets).order_by(buckets.c.count.desc(), buckets.c.value.nulls_last()).limit(filters.limit + 1)
        return [dict(row) for row in (await self.db.execute(statement)).mappings().all()]

    async def talent_wide_list(self) -> list[dict]:
        """查出人才宽表全部人才的关键字段（供继任规则引擎取数）。

        字段：talent_id / name / match_avg_score / potential_level / work_years。
        这是继任准备度公式里「匹配分/潜力/年限」三项的数据来源。
        """
        statement = select(
            DimTalentWide.talent_id,
            DimTalentWide.name,
            DimTalentWide.match_avg_score,
            DimTalentWide.potential_level,
            DimTalentWide.work_years,
        ).order_by(DimTalentWide.talent_id)
        return [dict(row) for row in (await self.db.execute(statement)).mappings().all()]

    async def assessment_pass_rate(self) -> dict[int, float]:
        """按人算「测评达标率」= 优秀/良好记录数 ÷ 总测评记录数。

        口径说明：测评 level 取「优秀 / 良好」视为达标（比用 total_score≥80 更有区分度，
        后者在真实数据里几乎无人达到，会让 10% 权重项形同虚设）。
        返回 {talent_id: 达标率(0~1)}；无测评记录的人才不在结果里（由 Service 兜底为 0）。
        """
        table = FactAssessmentHistory
        passed = func.count().filter(table.level.in_(["优秀", "良好"]))
        total = func.count()
        statement = select(
            table.talent_id,
            (passed * 1.0 / total).label("pass_rate"),
        ).group_by(table.talent_id)
        return {row["talent_id"]: float(row["pass_rate"])
                for row in (await self.db.execute(statement)).mappings().all()}

    async def assessment_summary(self, filters: AnalyticsFilters) -> dict:
        """按评分记录聚合；宽表组合条件通过子查询限制人才，不复制测评行。"""
        table = FactAssessmentHistory
        conditions = []
        for values, column in ((filters.assessment_ids, table.assessment_id),
                               (filters.position_ids, table.position_id), (filters.levels, table.level)):
            if values:
                conditions.append(column.in_(values))
        for value, predicate in ((filters.start_at, table.scored_at >= filters.start_at if filters.start_at else None),
                                 (filters.end_at, table.scored_at < filters.end_at if filters.end_at else None),
                                 (filters.min_score, table.total_score >= filters.min_score if filters.min_score is not None else None),
                                 (filters.max_score, table.total_score <= filters.max_score if filters.max_score is not None else None)):
            if value is not None:
                conditions.append(predicate)
        wide_conditions = self.wide_filters(filters)
        if wide_conditions:
            conditions.append(table.talent_id.in_(select(DimTalentWide.talent_id).where(*wide_conditions)))
        statement = select(
            func.count().label("record_count"), func.count(table.total_score).label("scored_count"),
            func.count(distinct(table.talent_id)).label("talent_count"),
            func.avg(table.total_score).label("average_score"), func.min(table.total_score).label("minimum_score"),
            func.max(table.total_score).label("maximum_score"),
        ).where(*conditions)
        return dict((await self.db.execute(statement)).mappings().one())
