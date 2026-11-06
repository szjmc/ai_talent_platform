"""确定性分析业务：校验筛选语义、执行受权聚合、说明数据与指标边界。

标签：[Vanna组] [P1]（测评统计 / 人才结构 / 分布 / 趋势 / 显式快照对比）

依赖AnalyticsRepository；不调用模型、不读取MySQL主库、不将缺数据解释为0。
人才存量按自然月计算同比环比，缺期/零分母明确无法计算；人才流失不在本次范围。
培训完成率按用户要求暂停，不扩表；匹配率公式与默认70分已确认，等待匹配/数据组接口。
"""
from __future__ import annotations

from datetime import date

from app.config.settings import settings
from app.core.exceptions import ParamError, PermissionDeniedError
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_statistics import (
    AnalyticsFilterBase, AnalyticsFilters, AssessmentSummaryResponse, ComparisonRequest, DistributionItem,
    DistributionResponse, TrendQuery,
)

_WIDE_FIELDS = {"skills", "potential_levels", "min_work_years", "max_work_years"}
_ASSESSMENT_FIELDS = {"start_at", "end_at", "assessment_ids", "position_ids", "levels", "min_score", "max_score"}


class AnalyticsStatisticsService:
    """scope只能由外层Service从登录用户计算；Repository从不接受请求中的权限。"""

    def __init__(self, db, scope: dict, *, repository=None):
        self.scope = scope
        self.repository = repository or AnalyticsRepository(db)

    @staticmethod
    def _reject(filters: AnalyticsFilterBase, fields: set[str], reason: str) -> None:
        # 专属请求模型可不声明无关字段；已声明但不支持的条件仍须明确拒绝。
        unsupported = [name for name in sorted(fields) if getattr(filters, name, None) not in (None, [])]
        if unsupported:
            raise ParamError(f"{reason}，不支持筛选：{', '.join(unsupported)}")

    @classmethod
    def _snapshot_filters(cls, type_: str, filters: AnalyticsFilterBase) -> None:
        cls._reject(filters, _WIDE_FIELDS | _ASSESSMENT_FIELDS, "月度快照没有这些明细字段")
        if (type_ == "age" and filters.educations) or (type_ == "education" and filters.age_groups):
            raise ParamError("尚未确认学历与年龄交叉快照的ETL口径，不能用单维汇总冒充联合分布")
        if filters.educations and filters.age_groups:
            raise ParamError("现有快照口径不支持同时筛选学历与年龄")

    def _require_full_scope(self) -> None:
        if self.scope["data_scope"] != "all":
            raise PermissionDeniedError("此分析表尚无可靠部门映射，仅允许all范围；不能放宽部门权限")

    def validate_distribution(self, type_: str, filters: AnalyticsFilters) -> None:
        """在建立数据库连接前完成指标、筛选与范围校验。"""
        if type_ not in {"education", "age", "skill", "org"}:
            raise ParamError("不支持的分布类型")
        if type_ == "skill":
            self._require_full_scope()
            self._reject(filters, {"organization_ids", "age_groups", "month"} | _ASSESSMENT_FIELDS,
                         "人才宽表没有可靠组织/年龄/快照月份映射")
        else:
            self._snapshot_filters(type_, filters)

    def validate_assessment(self, filters: AnalyticsFilters) -> None:
        """测评缺可靠组织/年龄映射，连接数据库前拒绝越权及不支持的条件。"""
        self._require_full_scope()
        self._reject(filters, {"organization_ids", "age_groups", "month"},
                     "测评历史没有可靠组织/年龄字段，月份快照不等于评分时间筛选")

    def validate_trend(self, data: TrendQuery) -> None:
        """校验快照层级及月份数量，不静默忽略筛选或输出无限时间序列。"""
        self._snapshot_filters("org", data)
        count = (data.end_month.year - data.start_month.year) * 12 + data.end_month.month - data.start_month.month + 1
        if count > settings.NL2SQL_MAX_ROWS:
            raise ParamError("趋势月份数超过分析返回上限，请缩小区间")

    def validate_comparison(self, data: ComparisonRequest) -> None:
        """两边筛选都先验证，避免取完本期数据才发现基期条件不支持。"""
        self.validate_distribution(data.type, data.current)
        self.validate_distribution(data.type, data.baseline)

    async def distribution(self, type_: str, filters: AnalyticsFilters) -> DistributionResponse:
        """分类统计只聚合授权数据；limit截分类展示，不拿截断结果算总体比例。"""
        self.validate_distribution(type_, filters)
        warnings = []
        if filters.limit > settings.NL2SQL_MAX_ROWS:
            filters = filters.model_copy(update={"limit": settings.NL2SQL_MAX_ROWS})
            warnings.append("分类展示上限已按服务端NL2SQL_MAX_ROWS收紧")
        month = None
        if type_ == "skill":
            rows = await self.repository.skill_distribution(filters)
            source = "dim_talent_wide"
            warnings.append("每个人的每项技能只计一次；同一个人可计入多个技能，分类计数之和不等于人才总数")
        else:
            month = filters.month or await self.repository.latest_month(self.scope, filters)
            source = "fact_talent_snapshot"
            rows = [] if month is None else await self.repository.snapshot_distribution(type_, month, self.scope, filters)
            if month is None or not rows:
                warnings.append("指定授权范围/月份/筛选下没有快照数据，不能据此认定实际人数为0")
        truncated = len(rows) > filters.limit
        if truncated:
            warnings.append("分类结果已按limit截断，不代表完整分布")
        items = [DistributionItem(value=row["value"], label=str(row["label"] or "未知"), count=row["count"])
                 for row in rows[:filters.limit]]
        return DistributionResponse(type=type_, source=source, month=month, items=items,
                                    truncated=truncated, warnings=warnings)

    async def assessment_summary(self, filters: AnalyticsFilters) -> AssessmentSummaryResponse:
        """按测评记录统计；学历/技能/年限条件用talent_id子查询，不扩大行数。"""
        self.validate_assessment(filters)
        return AssessmentSummaryResponse.model_validate(await self.repository.assessment_summary(filters))

    async def trend(self, data: TrendQuery) -> dict:
        """按用户显式给定的月份区间返回人才存量；缺期保留None，不补零。"""
        self.validate_trend(data)
        month_count = (data.end_month.year - data.start_month.year) * 12 + data.end_month.month - data.start_month.month + 1
        starts = [data.start_month]
        for name, offset in (("mom", -1), ("yoy", -12)):
            reference = self._shift_month(data.start_month, offset)
            if data.comparison in {name, "both"} and reference is not None:
                starts.append(reference)
        query_start = min(starts)
        rows = await self.repository.snapshot_trend(self.scope, data, query_start, data.end_month)
        lookup = {row["month"]: row["value"] for row in rows}
        items = []
        year, month = data.start_month.year, data.start_month.month
        for _ in range(month_count):
            key = date(year, month, 1)
            item = {"month": key, "value": lookup.get(key), "missing": key not in lookup}
            if data.comparison in {"mom", "both"}:
                item["mom"] = self._growth(key, self._shift_month(key, -1), lookup)
            if data.comparison in {"yoy", "both"}:
                item["yoy"] = self._growth(key, self._shift_month(key, -12), lookup)
            items.append(item)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return {"metric": "talent_total", "granularity": "month", "comparison": data.comparison,
                "source": "fact_talent_snapshot", "items": items,
                "warnings": ["人数为各月存量，不等于新增/流失；缺期不补0；同比/环比缺期或零分母返回无法计算"]}

    @staticmethod
    def _shift_month(value: date, offset: int) -> date | None:
        """以月份索引平移，正确跨年；超出Python日期范围则没有可比较基期。"""
        index = (value.year - 1) * 12 + value.month - 1 + offset
        if not 0 <= index < 9999 * 12:
            return None
        return date(index // 12 + 1, index % 12 + 1, 1)

    @staticmethod
    def _growth(current_month: date, baseline_month: date | None, lookup: dict) -> dict:
        """只计算有本期/基期记录且基期非0的变化率，不用缺失值冒充零。"""
        current = lookup.get(current_month)
        baseline = lookup.get(baseline_month)
        difference = None if current is None or baseline is None else current - baseline
        if current is None:
            reason = "本期缺少快照数据"
        elif baseline_month is None or baseline is None:
            reason = "基期缺少快照数据"
        elif baseline == 0:
            reason = "基期为0，分母为0"
        else:
            reason = None
        return {"baseline_month": baseline_month, "baseline_value": baseline, "difference": difference,
                "rate": None if reason else difference / baseline,
                "status": "无法计算" if reason else "已计算", "reason": reason}

    async def compare(self, data: ComparisonRequest) -> dict:
        """比较显式快照，不自动推断同比/环比；任一侧缺失则不计算差额或比例。"""
        self.validate_comparison(data)
        current = await self.distribution(data.type, data.current)
        baseline = await self.distribution(data.type, data.baseline)
        if current.truncated or baseline.truncated:
            raise ParamError("对比分类被limit截断，请增大limit或缩小筛选范围后再比较")
        now = {item.value: item for item in current.items}
        before = {item.value: item for item in baseline.items}
        items = []
        for key in dict.fromkeys([*now, *before]):
            current_value = now[key].count if key in now else None
            baseline_value = before[key].count if key in before else None
            delta = None if current_value is None or baseline_value is None else current_value - baseline_value
            rate = None if delta is None or baseline_value == 0 else delta / baseline_value
            items.append({"value": key, "label": (now.get(key) or before[key]).label,
                          "current": current_value, "baseline": baseline_value,
                          "difference": delta, "change_rate": rate,
                          "rate_status": "无法计算" if rate is None else "已计算"})
        return {"type": data.type, "current_month": data.current.month, "baseline_month": data.baseline.month,
                "items": items, "warnings": [*current.warnings, *baseline.warnings,
                    "显式快照对比；缺失分类或零分母返回null，不自动当作0；不是已确认口径的同比/环比"]}
