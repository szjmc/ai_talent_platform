"""分析统计接口契约：受控组合筛选、分布、测评分数及快照对比。

标签：[Vanna组] [P1]（确定性统计请求与响应）

日期/字段按实际分析表表达，不把工作年限当年龄、不把平均匹配分当匹配率。
各指标只接受能落实到其数据源的筛选；不能支持的组合由Service明确拒绝。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema, ORMModel

Name = Annotated[str, Field(min_length=1, max_length=64)]
PositiveID = Annotated[int, Field(gt=0)]
DistributionType = Literal["education", "age", "skill", "org"]


class AnalyticsFilterBase(BaseSchema):
    """共用筛选及区间校验；测评分数只在需要该字段的请求中声明。"""

    organization_ids: list[PositiveID] = Field(default_factory=list, max_length=100)
    educations: list[Name] = Field(default_factory=list, max_length=30)
    age_groups: list[Name] = Field(default_factory=list, max_length=30)
    skills: list[Name] = Field(default_factory=list, max_length=30)
    skill_mode: Literal["all", "any"] = "all"
    potential_levels: list[Name] = Field(default_factory=list, max_length=20)
    min_work_years: float | None = Field(None, ge=0, allow_inf_nan=False)
    max_work_years: float | None = Field(None, ge=0, allow_inf_nan=False)
    month: date | None = Field(None, description="快照月份，必须为该月1日；省略时用授权范围最新月份")
    start_at: datetime | None = Field(None, description="测评起始时间，含起点，须带时区")
    end_at: datetime | None = Field(None, description="测评截止时间，不含终点，须带时区")
    assessment_ids: list[PositiveID] = Field(default_factory=list, max_length=100)
    position_ids: list[PositiveID] = Field(default_factory=list, max_length=100)
    levels: list[Name] = Field(default_factory=list, max_length=30)
    limit: int = Field(100, ge=1, le=1000, description="最多返回分类数，不限制数据库聚合总体")

    @model_validator(mode="after")
    def validate_ranges(self) -> AnalyticsFilterBase:
        """拒绝反向区间和不明确的时间，避免部署机器时区改变统计边界。"""
        if (self.min_work_years is not None and self.max_work_years is not None
                and self.min_work_years > self.max_work_years):
            raise ValueError("筛选区间下限不能大于上限")
        if self.month is not None and self.month.day != 1:
            raise ValueError("month必须为月份第一天")
        for value in (self.start_at, self.end_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("测评时间必须包含时区")
        if self.start_at is not None and self.end_at is not None and self.start_at >= self.end_at:
            raise ValueError("start_at必须早于end_at")
        return self


class AnalyticsFilters(AnalyticsFilterBase):
    """带测评分数的筛选；同类多值OR、跨字段AND，技能由skill_mode决定。"""

    min_score: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    max_score: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_score_range(self) -> AnalyticsFilters:
        if self.min_score is not None and self.max_score is not None and self.min_score > self.max_score:
            raise ValueError("筛选区间下限不能大于上限")
        return self


class DistributionQuery(AnalyticsFilters):
    type: DistributionType


class TrendQuery(AnalyticsFilterBase):
    """月度人才存量不接受min_score/max_score，不把人数差冒充流失人数。"""

    metric: Literal["talent_total"] = "talent_total"
    granularity: Literal["month"] = "month"
    comparison: Literal["none", "mom", "yoy", "both"] = Field(
        "none", description="自然月存量环比/同比；不含人才流失指标"
    )
    start_month: date
    end_month: date

    @model_validator(mode="after")
    def validate_months(self) -> TrendQuery:
        if self.start_month.day != 1 or self.end_month.day != 1:
            raise ValueError("趋势边界必须为月份第一天")
        if self.start_month > self.end_month:
            raise ValueError("start_month不能晚于end_month")
        if self.month is not None:
            raise ValueError("趋势使用start_month/end_month，不接受单个month")
        return self


class SnapshotSelection(AnalyticsFilters):
    month: date


class ComparisonRequest(BaseSchema):
    """对比用户明确指定的两个快照，不自行定义同比/环比的日历和补缺规则。"""

    type: Literal["education", "age", "org"]
    current: SnapshotSelection
    baseline: SnapshotSelection


class DistributionItem(ORMModel):
    value: str | int | None
    label: str
    count: int


class DistributionResponse(ORMModel):
    type: DistributionType
    source: str
    month: date | None = None
    items: list[DistributionItem]
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class AssessmentSummaryResponse(ORMModel):
    record_count: int
    scored_count: int
    talent_count: int
    average_score: float | None
    minimum_score: float | None
    maximum_score: float | None
    source: str = "fact_assessment_history"
    basis: str = "测评记录口径，平均分不按人才去重，NULL得分不参与平均值"


class GrowthResult(ORMModel):
    baseline_month: date | None
    baseline_value: int | None
    difference: int | None
    rate: float | None
    status: Literal["已计算", "无法计算"]
    reason: str | None = None


class TrendItem(ORMModel):
    month: date
    value: int | None
    missing: bool
    mom: GrowthResult | None = None
    yoy: GrowthResult | None = None


class TrendResponse(ORMModel):
    metric: Literal["talent_total"]
    granularity: Literal["month"]
    source: str
    comparison: Literal["none", "mom", "yoy", "both"] = "none"
    items: list[TrendItem]
    warnings: list[str] = Field(default_factory=list)


class ComparisonItem(ORMModel):
    value: str | int | None
    label: str
    current: int | None
    baseline: int | None
    difference: int | None
    change_rate: float | None = Field(None, description="差额/基期，比例值而非已乘100的百分数")
    rate_status: Literal["已计算", "无法计算"]


class ComparisonResponse(ORMModel):
    type: Literal["education", "age", "org"]
    current_month: date
    baseline_month: date
    items: list[ComparisonItem]
    warnings: list[str] = Field(default_factory=list)
