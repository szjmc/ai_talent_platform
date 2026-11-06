"""
模块：app/schemas/training.py
职责：培训管理（课程 / 培训计划 / 学习记录 / 课程推荐）请求与响应结构。

标签：[Dify组] [P1]

设计要点：
1. 写模型继承 BaseSchema（extra=forbid），防止前端多传字段被静默忽略；
2. 读模型继承 ORMModel（from_attributes=True），可直接由 ORM 对象构造；
3. AI 生成计划的入参允许「显式传 gaps」：这是与测评 Agent 的契约字段，
   也方便联调 / 单测时跳过测评模块直接验证本模块；
4. 进度、分数等数值在这里做边界校验，业务规则（反作弊、单调递增）放 Service。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import BaseSchema, ORMModel


# ============================================================== 课程
class CourseCreate(BaseSchema):
    """创建课程。"""

    title: str = Field(..., min_length=1, max_length=128, description="课程名称")
    category: str | None = Field(None, max_length=32, description="分类 / 对应能力维度")
    level: str | None = Field(None, max_length=20, description="适用层级：L1~L5 或 初级/中级/高级")
    duration_hours: Decimal | None = Field(None, ge=0, description="学时")
    resource_url: str | None = Field(None, max_length=512, description="资源地址")
    cover: str | None = Field(None, max_length=255, description="封面")
    description: str | None = Field(None, description="简介")
    skill_tags: list[str] | None = Field(None, description="对应技能标签（用于按维度匹配）")
    is_active: bool = Field(True, description="是否上架")


class CourseUpdate(BaseSchema):
    """更新课程（全部字段可选，只传要改的）。"""

    title: str | None = Field(None, min_length=1, max_length=128)
    category: str | None = Field(None, max_length=32)
    level: str | None = Field(None, max_length=20)
    duration_hours: Decimal | None = Field(None, ge=0)
    resource_url: str | None = Field(None, max_length=512)
    cover: str | None = Field(None, max_length=255)
    description: str | None = None
    skill_tags: list[str] | None = None
    is_active: bool | None = None


class CourseResponse(ORMModel):
    """课程响应。"""

    id: int
    title: str
    category: str | None = None
    level: str | None = None
    duration_hours: Decimal | None = None
    resource_url: str | None = None
    cover: str | None = None
    description: str | None = None
    skill_tags: list[Any] | None = None
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ============================================================== 能力短板（节点 1 的输入契约）
class GapItem(BaseSchema):
    """一项能力短板（与测评 Agent 的交接字段）。

    字段可缺省策略（对应「岗位能力模型」需求）：
        测评 Agent 往往只能给出「考了多少分」，给不出岗位要求分。所以
        `required` 与 `weight` 都允许缺——缺了由岗位能力模型补，再缺才用默认值。
        但 `current` 必须给：没有当前分就算不出缺口，宁可报错也不能静默丢弃。
    """

    dimension: str = Field(..., min_length=1, max_length=32, description="能力维度")
    current: float = Field(..., ge=0, le=100, description="当前得分（必填）")
    required: float | None = Field(
        None, ge=0, le=100, description="岗位要求分；不传则取岗位能力模型，再兜底 80"
    )
    delta: float | None = Field(None, description="缺口（不传则由 required-current 推导）")
    weight: float | None = Field(
        None, ge=0, le=1, description="岗位权重；不传则取岗位能力模型，再兜底 0.2"
    )

    @model_validator(mode="after")
    def _check_source(self) -> "GapItem":
        """当前分是硬要求；目标分/权重允许缺省（由岗位模型补）。"""
        if self.current is None:  # pragma: no cover - pydantic 已保证非空，双保险
            raise ValueError(f"维度「{self.dimension}」缺少 current（当前得分），无法计算缺口")
        return self


class DiagnosedGap(ORMModel):
    """诊断后的短板（带优先级与预估学时）。"""

    dimension: str
    current: float | None = None
    required: float | None = None
    delta: float
    weight: float
    priority: str
    est_hours: float


# ============================================================== 培训计划
class TrainingPlanGenerateRequest(BaseSchema):
    """节点 1~4：由能力短板生成 4 周培训计划。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名（用于方案说明）")
    assessment_id: int | None = Field(None, gt=0, description="能力缺口来源测评 ID（可选）")
    position_id: int | None = Field(None, gt=0, description="目标岗位 ID（可选）")
    gaps: list[GapItem] | None = Field(None, description="能力短板；不传则从测评结果读取")
    max_weeks: int = Field(4, ge=1, le=12, description="培训周期（周）")
    max_hours_per_week: float = Field(8, gt=0, le=40, description="每周学时上限")
    pass_score: float = Field(80, ge=0, le=100, description="结课考核及格线")
    goal: str | None = Field(None, max_length=512, description="培养目标")
    start_date: date | None = Field(None, description="开始日期，默认今天")
    auto_push: bool = Field(True, description="是否把方案推送给学员（站内信 + 事件）")
    draft: bool = Field(
        False,
        description=(
            "是否存为草稿。草稿不参与滞后扫描、不发推送；"
            "此前该语义被 auto_push 兼职，导致「不推送」等于「不启动」，语义错位。"
        ),
    )
    replace_existing: bool = Field(
        False,
        description=(
            "同人才已有进行中计划时是否替换。"
            "false（默认）返回 409 并提示已有计划，避免连点生成出一堆同名方案；"
            "true 则把旧计划置为 cancelled 后再建新的。"
        ),
    )
    use_ai: bool = Field(True, description="是否尝试用 LLM 排期（失败自动回退规则）")


class TrainingPlanCreate(BaseSchema):
    """手工创建培训计划（不经过 AI）。"""

    name: str = Field(..., min_length=1, max_length=128)
    talent_id: int = Field(..., gt=0)
    course_ids: list[int] = Field(default_factory=list, description="课程 ID 列表（按顺序）")
    goal: str | None = Field(None, max_length=512)
    start_date: date | None = None
    end_date: date | None = None
    created_by_ai: bool = False


class PlanItemResponse(ORMModel):
    """计划中的一条学习安排。"""

    week: int
    dimension: str
    hours: float
    course_id: int | None = None
    title: str | None = None
    level: int | None = None
    duration_hours: float | None = None
    is_platform: bool = True
    reason: str = ""
    external_suggestion: str | None = None


class PlanAssessment(ORMModel):
    """考核节点。"""

    week: int
    type: str = "结课考核"
    pass_score: float = 80


class TrainingPlanResponse(ORMModel):
    """培训计划响应（含完成率、降级信息、方案说明）。"""

    # 全维度达标时不建计划，此时没有 id。曾经返回 id=0 让前端拿 0 去查详情直接 404，
    # 现在明确给 null + no_training_needed=True，前端据此走「无需培训」分支。
    id: int | None = None
    no_training_needed: bool = Field(
        False, description="各维度均已达标、本次未生成计划（此时 id 为 null）"
    )
    name: str
    talent_id: int
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = "draft"
    created_by_ai: bool = False
    source_assessment_id: int | None = None
    total_weeks: int = 4
    total_hours: float = Field(0, description="总学时（已落库课程的实际学时之和，与进度计算同源）")
    scheduled_hours: float | None = Field(
        None, description="排期学时（按短板预估排出的学时；与 total_hours 的差额来自课程实际时长）"
    )
    completion_rate: float = Field(0, description="完成率 0~1")
    items: list[PlanItemResponse] = Field(default_factory=list)
    assessment: PlanAssessment | None = None
    summary: str = ""
    summary_source: Literal["rule", "llm"] | str = Field(
        "rule", description="方案说明来源：llm=模型撰写，rule=规则模板（模型不可用时的兜底）"
    )
    degrade: bool = Field(False, description="是否存在降级为外部建议的单元")
    external_count: int = 0
    library_rate: float | None = Field(None, description="课程来自平台库比例")
    generated_by: Literal["rule", "llm"] | str = "rule"
    requirement_source: str = Field(
        "default",
        description="目标分/权重的来源：position_model=岗位能力模型，explicit=调用方显式传入，default=默认值兜底",
    )
    requirement_position_id: int | None = Field(None, description="实际采用的岗位 ID（未匹配到则为空）")
    requirement_version: int | None = Field(None, description="实际采用的岗位能力模型版本")
    prompt_version: str | None = None
    model_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TrainingPlanBrief(ORMModel):
    """计划列表项（不带明细）。"""

    id: int
    name: str
    talent_id: int
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = "draft"
    created_by_ai: bool = False
    completion_rate: float = 0
    created_at: datetime | None = None


# ============================================================== 学习记录（节点 5）
class LearningRecordUpdate(BaseSchema):
    """更新学习进度（进度只允许单调递增）。"""

    plan_id: int = Field(..., gt=0)
    course_id: int = Field(..., gt=0)
    talent_id: int = Field(..., gt=0)
    progress: int = Field(..., ge=0, le=100, description="进度 0~100")
    study_minutes: int = Field(..., ge=0, description="本次累计学习分钟")


class LearningRecordResponse(ORMModel):
    """学习记录响应。"""

    id: int
    plan_id: int
    course_id: int
    talent_id: int
    progress: int = 0
    study_minutes: int = 0
    last_study_at: datetime | None = None
    status: str = "not_started"
    score: Decimal | None = None
    passed: bool | None = None
    examined_at: datetime | None = None
    suspicious: bool = False


class ProgressResponse(ORMModel):
    """进度统计结果（节点 5）。"""

    plan_id: int
    total_hours: float
    completed_h: float
    expected_h: float
    completion: float
    lag_ratio: float
    alert: str
    event: dict[str, Any] | None = None


# ============================================================== 考核（节点 6）
class ScoreSubmitRequest(BaseSchema):
    """提交结课考核成绩，触发复测事件。

    plan_id / talent_id 均为可缺省：
    - plan_id 由路由从路径参数注入（POST /trainings/plans/{plan_id}/score），
      请求体再传一遍属于冗余，早期版本设为必填会导致该接口恒定 422；
    - talent_id 缺省时取计划所属人才，避免前端传错人把成绩记到别人头上。
    两者都由 Service 做最终校验（缺失即报 400，不猜）。
    """

    plan_id: int | None = Field(None, gt=0, description="培训计划 ID（路径已带，可不传）")
    talent_id: int | None = Field(None, gt=0, description="人才 ID；不传则取计划所属人才")
    course_id: int | None = Field(None, gt=0, description="可指定某门课的考核")
    score: float = Field(..., ge=0, le=100, description="考核分数")
    auto_retest: bool = Field(True, description="是否自动触发复测事件（HR 可关闭）")
    force: bool = Field(
        False,
        description=(
            "计划已结课（finished）时是否允许再次打分。"
            "默认 false：重复提交会拒绝，避免把已通过的结论覆盖成不通过、"
            "把已归档的通过率统计改掉；确需改判（复议/补考）再显式传 true。"
        ),
    )
    dimensions: list[str] = Field(default_factory=list, description="本次覆盖的能力维度")


class ScoreSubmitResponse(ORMModel):
    """考核结果。"""

    plan_id: int
    talent_id: int
    score: float
    passed: bool | None = None
    assessment: str
    plan_status: str
    need_human: bool = False
    events: list[dict[str, Any]] = Field(default_factory=list)


class CoursePassRate(BaseSchema):
    """单门课的考核通过情况。"""

    course_id: int | None = None
    title: str | None = Field(None, description="课程名（课程被删则为空）")
    examined: int = Field(0, description="参加考核人数")
    passed: int = Field(0, description="通过人数")
    pass_rate: float = Field(0, description="通过率 0~1")
    avg_score: float | None = Field(None, description="平均分")


class PassRateStatsResponse(BaseSchema):
    """考核通过率统计（节点 6）。

    注意两个「率」不是一回事：
    - completion_rate（完成率）衡量**过程**：学完了没有；
    - pass_rate（通过率）衡量**结果**：考过了没有。
    """

    scope: dict[str, Any] = Field(default_factory=dict, description="本次统计的筛选条件回显")
    total_examined: int = Field(0, description="已出考核结论的记录数（分母）")
    passed: int = 0
    failed: int = 0
    pass_rate: float = Field(0, description="整体通过率 0~1")
    avg_score: float | None = None
    by_course: list[CoursePassRate] = Field(default_factory=list)


# ============================================================== 课程推荐
class TrainingRecommendRequest(BaseSchema):
    """根据能力短板推荐课程（不生成计划）。"""

    talent_id: int = Field(..., gt=0)
    gaps: list[GapItem] | None = Field(None, description="能力短板；不传则从测评结果读取")
    top_k: int = Field(5, ge=1, le=20, description="每个维度最多推荐几门")


class RecommendationItem(ORMModel):
    """单条推荐。"""

    course_id: int
    title: str
    level: int | None = None
    duration_hours: float | None = None
    gap_dimension: str
    reason: str
    score: float = 0


class TrainingRecommendResult(ORMModel):
    """推荐结果。"""

    talent_id: int
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    summary: str = ""


# ============================================================== 滞后扫描
class LagAlertItem(ORMModel):
    """一条滞后预警。"""

    plan_id: int
    talent_id: int
    name: str
    total_hours: float
    completed_h: float
    completion: float
    lag_ratio: float
    alert: str
    event: dict[str, Any] | None = None


class LagScanResult(ORMModel):
    """滞后扫描结果（节点 5 定时任务输出）。"""

    scanned: int = 0
    alerts: list[LagAlertItem] = Field(default_factory=list)
    reminded: int = 0
    warned: int = 0


# ============================================================== 站内信（节点 4/5/6 推送落地）
class TrainingNotificationResponse(ORMModel):
    """一条培训站内信。"""

    id: int
    plan_id: int
    talent_id: int | None = None
    type: str
    level: str = "正常"
    title: str
    content: str
    notify_date: date
    is_read: bool = False
    read_at: datetime | None = None
    created_at: datetime | None = None


class NotificationBrief(BaseSchema):
    """站内信列表查询结果（含未读数）。"""

    total: int = 0
    unread: int = 0
    items: list[TrainingNotificationResponse] = Field(default_factory=list)


# ============================================================== 通用入参
class CourseQuery(BaseSchema):
    """课程列表查询参数（作为 Query 使用时可参考）。"""

    keyword: str | None = Field(None, max_length=64)
    category: str | None = Field(None, max_length=32)
    level: str | None = Field(None, max_length=20)
    is_active: bool | None = None

    @field_validator("keyword")
    @classmethod
    def _strip_keyword(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


__all__ = [
    "CourseCreate",
    "CourseQuery",
    "CourseResponse",
    "CourseUpdate",
    "DiagnosedGap",
    "GapItem",
    "LagAlertItem",
    "LagScanResult",
    "LearningRecordResponse",
    "LearningRecordUpdate",
    "NotificationBrief",
    "PlanAssessment",
    "PlanItemResponse",
    "ProgressResponse",
    "RecommendationItem",
    "ScoreSubmitRequest",
    "ScoreSubmitResponse",
    "TrainingNotificationResponse",
    "TrainingPlanBrief",
    "TrainingPlanCreate",
    "TrainingPlanGenerateRequest",
    "TrainingPlanResponse",
    "TrainingRecommendRequest",
    "TrainingRecommendResult",
]
