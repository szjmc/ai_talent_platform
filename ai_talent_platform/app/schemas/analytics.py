"""
模块：app/schemas/analytics.py
职责：数据分析 / NL2SQL 相关请求 / 响应结构。

标签：[Vanna组] [P1]（NL2SQLResponse / 图表配置结构，被 analytics_router 依赖）

Schema 清单：
1. DashboardOverview     total_talents、active_positions、match_rate、training_completion、
                         assessment_avg_score、monthly_trend: list[{month, value}]
2. NL2SQLRequest         question、conversation_id(可空)、max_rows
3. NL2SQLResponse        question、sql(Vanna 生成，需脱敏展示)、
                         columns: list[str]、rows: list[list]、chart: dict(类型/配置)、
                         insight: str(AI 解读)、elapsed_ms
4. ChartConfig           type(bar/line/pie/radar/heatmap)、x_field、y_field、title
5. ReportRequest         report_type(monthly/quarterly/yearly/custom)、date_range、format(pdf/docx)
6. DistributionResponse / TrendResponse / ReportTask（看板与报告任务配套结构）

实现要点：
- SQL 必须经 adapters/nl2sql/sql_validator.py 校验（只允许 SELECT、禁止系统表、强制 limit）；
- 数据权限：生成 SQL 后追加角色数据范围条件（行级权限）；
- 大查询走 PgSQL（分析库），不要打到 MySQL 主库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """拒绝未知字段（防参数漂移）。"""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ 图表
class ChartConfig(BaseModel):
    """统一图表配置（前端按 type 用 ECharts 渲染）。"""

    type: Literal["bar", "line", "pie", "radar", "heatmap"] = Field(
        "bar", description="图表类型（时间->line、枚举->bar、占比->pie）"
    )
    x_field: str = Field("", description="X 轴 / 类目字段名")
    y_field: str = Field("", description="Y 轴 / 数值字段名")
    title: str = Field("", description="图表标题（默认取问题或字段名）")
    series: list[str] = Field(default_factory=list, description="多系列字段（折线/柱状可多列）")


# ------------------------------------------------------------------ 概览
class MonthlyTrendPoint(BaseModel):
    """月度趋势点（概览卡内嵌小趋势）。"""

    month: str = Field(..., description="YYYY-MM")
    value: float = Field(0, description="指标值（人才总数口径）")


class DashboardOverview(BaseModel):
    """首页驾驶舱概览（口径见 analytics_service.docstring）。"""

    total_talents: int = Field(0, description="人才总数（在职+储备，不含离职与逻辑删除）")
    active_positions: int = Field(0, description="在招岗位数（positions.status=open）")
    match_rate: float = Field(0.0, description="匹配率 = 近90天匹配记录 score>=60 占比（0-1）")
    training_completion: float = Field(0.0, description="培训完成率（0-1）")
    assessment_avg_score: float | None = Field(None, description="平均测评分（0-100）")
    monthly_trend: list[MonthlyTrendPoint] = Field(
        default_factory=list, description="近 6 个月人才总数趋势"
    )
    data_notes: list[str] = Field(
        default_factory=list, description="数据来源降级说明（如 PgSQL 未启用时的回退口径）"
    )


# ------------------------------------------------------------------ 分布
class DistributionItem(BaseModel):
    label: str = Field(..., description="分组标签（学历/年龄段/技能/部门）")
    value: float = Field(..., description="数量或占比值")


class DistributionResponse(BaseModel):
    type: Literal["education", "age", "skill", "org"] = Field(..., description="分布维度")
    items: list[DistributionItem] = Field(default_factory=list)
    note: str = Field("", description="口径 / 降级说明")


class TrendPoint(BaseModel):
    month: str = Field(..., description="YYYY-MM")
    value: float = Field(0, description="指标值")


class TrendResponse(BaseModel):
    metric: Literal["talent_new", "turnover", "match"] = Field(..., description="趋势指标")
    granularity: str = Field("month", description="粒度（当前仅 month）")
    unit: str = Field("count", description="count / score / percent")
    points: list[TrendPoint] = Field(default_factory=list)
    note: str = Field("", description="统计口径说明")


# ------------------------------------------------------------------ NL2SQL
class NL2SQLRequest(_StrictModel):
    question: str = Field(..., min_length=1, max_length=500, description="自然语言问题")
    conversation_id: str | None = Field(None, max_length=64, description="会话 ID（可空）")
    max_rows: int = Field(200, ge=1, le=1000, description="返回行数上限")


class NL2SQLResponse(BaseModel):
    question: str = ""
    sql: str | None = Field(None, description="校验后的只读 SQL（已注入 LIMIT / 数据范围）")
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    chart: ChartConfig | None = None
    insight: str = Field("", description="数据解读（AI 或启发式）")
    elapsed_ms: float = 0.0


# ------------------------------------------------------------------ 报告
class ReportRequest(_StrictModel):
    report_type: Literal["monthly", "quarterly", "yearly", "custom"] = "monthly"
    date_range: tuple[str, str] | None = Field(
        None, description="自定义区间 [start, end]（report_type=custom 时必填）"
    )
    format: Literal["pdf", "docx"] = Field("docx", description="导出格式")


class ReportTask(BaseModel):
    """报告生成任务（POST /report 立即返回 task_id，GET /reports 查状态）。"""

    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    report_type: str = "monthly"
    requested_format: str = "docx"
    actual_format: str = Field("", description="实际落盘格式（缺 docx/pdf 依赖时降级为 md）")
    url: str | None = Field(None, description="报告文件路径（文件中心就绪后转为下载地址）")
    error: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


__all__ = [
    "ChartConfig",
    "DashboardOverview",
    "DistributionItem",
    "DistributionResponse",
    "MonthlyTrendPoint",
    "NL2SQLRequest",
    "NL2SQLResponse",
    "ReportRequest",
    "ReportTask",
    "TrendPoint",
    "TrendResponse",
]
