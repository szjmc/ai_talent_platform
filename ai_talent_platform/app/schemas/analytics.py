"""
模块：app/schemas/analytics.py
职责：分析问数 / 报告相关的请求与响应结构定义。

标签：[Vanna组] [P1]（NL2SQLRequest / NL2SQLResponse / ChartConfig / 报告结构）

============================ 实现思路与代码要点 ============================
1. 请求模型继承 BaseSchema：拒绝未知字段、自动去首尾空白，防止前端多传 sql/roles；
2. 响应模型继承 ORMModel：可直接从 ORM 对象或 dict 构造；
3. 图表用 ChartConfig 描述「画什么」，不包含任何可执行代码：
     bar / line  -> x_field + 多个 series（series[i].data 为 [{name, value}]）
     pie         -> 单个 series，data 为 [{name, value}]
     radar       -> indicators（维度与量程）+ 单个 series
     heatmap     -> x_labels / y_labels + values（[x下标, y下标, 数值] 三元组）
4. 图表外观（主题/尺寸/配色）放在 ChartOptions，由请求传入、响应回显，前端负责渲染；
5. 报告支持 query_id 复用结果，或 question 重新查询；两种模式互斥。

其他约束（跨组约定）：
- SQL 必须经 adapters/nl2sql/sql_validator.py 校验（只允许 SELECT、禁止系统表、强制 limit）；
- 数据权限：生成 SQL 后追加角色数据范围条件（行级权限）；
- 大查询走 PgSQL（分析库），不要打到 MySQL 主库。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import BaseSchema, ORMModel

# 响应里出现的图表类型（auto 只存在于请求，响应一定是确定类型）
ChartType = Literal["bar", "line", "pie", "radar", "heatmap"]
ChartTypeRequest = Literal["auto", "bar", "line", "pie", "radar", "heatmap"]


class ChartOptions(BaseSchema):
    """图表外观选项。

    后端只负责把选项透传到 ChartConfig，真正生效的是前端 ECharts 或报告渲染器。
    「样式自定义 / 尺寸调整 / 主题切换」三个需求点都落在这里，不新增接口。
    """

    theme: Literal["light", "dark"] = Field("light", description="主题：浅色 / 深色")
    width: int = Field(800, ge=320, le=2000, description="图表宽度（px）")
    height: int = Field(450, ge=240, le=1200, description="图表高度（px）")
    palette: list[str] = Field(default_factory=list, description="配色列表（十六进制色值）")
    show_legend: bool = Field(True, description="是否显示图例")


class NL2SQLRequest(BaseSchema):
    """问数请求：只接收业务参数，不允许额外传入 sql、roles、user_scope。

    身份和数据范围由后端登录上下文提供，不信任请求体。
    chart_type 默认 auto：不传时按数据形态推断，行为与 MVP 完全一致。
    """

    question: str = Field(..., min_length=1, max_length=500, description="自然语言问题")
    max_rows: int = Field(200, ge=1, le=1000, description="返回行数上限，另受服务端硬上限约束")
    with_insight: bool = Field(True, description="是否生成数据解读")
    chart_type: ChartTypeRequest = Field(
        "auto", description="图表类型；auto 表示由后端按数据自动推断"
    )
    chart_options: ChartOptions | None = Field(None, description="图表外观选项（可选）")


class ChartConfig(ORMModel):
    """声明式图表配置：描述「画什么」，由前端映射到 ECharts，不含绘图脚本。"""

    type: ChartType
    title: str
    x_field: str | None = Field(None, description="分类/时间列名")
    y_field: str | None = Field(None, description="数值列名（多系列时为空）")
    value_field: str | None = Field(None, description="热力图的数值列名")
    series: list[dict[str, Any]] = Field(default_factory=list, description="数据系列")
    indicators: list[dict[str, Any]] | None = Field(None, description="雷达图维度与量程")
    x_labels: list[str] | None = Field(None, description="横轴标签")
    y_labels: list[str] | None = Field(None, description="纵轴标签（热力图）")
    values: list[list[Any]] | None = Field(None, description="热力图三元组 [x, y, value]")
    style: dict[str, Any] | None = Field(None, description="样式（配色、图例等）")
    size: dict[str, int] | None = Field(None, description="尺寸（width / height）")
    theme: str | None = Field(None, description="主题 light / dark")


class InsightDetail(ORMModel):
    """结构化解读：把原来的一段纯文本拆成五块，前端可分块展示。"""

    summary: str = Field("", description="总体结论")
    anomalies: list[str] = Field(default_factory=list, description="异常点")
    causes: list[str] = Field(default_factory=list, description="可能成因（待验证假设）")
    risks: list[str] = Field(default_factory=list, description="潜在风险")
    suggestions: list[str] = Field(default_factory=list, description="优化建议")


class NL2SQLResponse(ORMModel):
    """API / Adapter / Skill 共用的结果结构。

    columns 与 rows 按列序一一对应；sql 是执行前已校验/过滤/限行的语句。
    chart 可为空；insight 为兼容旧前端的纯文本，insight_detail 为结构化解读。
    cached 在当前 MVP 中固定为 False；warnings 表示查询成功但附加步骤受限。
    """

    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    chart: ChartConfig | None = None
    insight: str | None = None
    insight_detail: InsightDetail | None = None
    elapsed_ms: int = 0
    cached: bool = False
    warnings: list[str] = Field(default_factory=list)
    query_id: str | None = Field(None, description="已保存结果快照ID；不可用时为空")
    expires_at: datetime | None = Field(None, description="快照过期时间；读取/导出不续期")


class ReportRequest(BaseSchema):
    """query_id复用页面结果；question保留旧的重新问数入口，两者只能选一个。"""

    question: str | None = Field(None, min_length=1, max_length=500, description="重新问数的问题")
    query_id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$", description="复用问数返回的快照ID")
    format: Literal["pdf", "docx"] = Field("pdf", description="报告格式")
    title: str | None = Field(None, max_length=100, description="报告标题；为空时用问题截断")
    max_rows: int = Field(200, ge=1, le=1000, description="报告内数据行数上限")
    include_chart: bool = Field(True, description="是否在报告中嵌入图表图片")
    chart_type: ChartTypeRequest = Field("auto", description="图表类型，auto 为自动推断")

    @model_validator(mode="after")
    def validate_source(self) -> ReportRequest:
        """快照模式禁止重写行数/图表类型，避免声称复用却改变原结果。"""
        if (self.question is None) == (self.query_id is None):
            raise ValueError("question与query_id必须且只能提供一个")
        if self.query_id is not None and self.model_fields_set & {"max_rows", "chart_type"}:
            raise ValueError("query_id模式不接受max_rows或chart_type，请重新问数以改变结果")
        return self


class ReportItem(ORMModel):
    """报告元数据：生成接口与列表接口共用。"""

    report_id: str = Field(..., description="报告 ID（32 位十六进制）")
    title: str
    format: str
    size: int = Field(0, description="文件大小（字节）")
    created_at: datetime
    url: str = Field("", description="下载地址（相对路径）")


# ====================================================================== 概览与分布/趋势
# 说明：合并 origin/develop 时补齐。develop 侧 ac239d7 提交误将未解决冲突的文件入库，
#      以下 6 个模型从其作者本地版本中提取，供 tests/test_analytics_channel 工作流用例使用；
#      严格模式（_StrictModel）重构未完成，未采纳，避免破坏 ChartOptions/InsightDetail/ReportItem 引用。

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
