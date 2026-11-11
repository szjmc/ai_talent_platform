"""
模块：app/schemas/ai_interview.py
职责：AI 数字人面试请求 / 响应模型。

标签：[Agent组] [P1]（AI 面试接口契约）

说明：
- 创建请求允许「绑定库内人才」或「手工录入候选人姓名」两种方式；
- 公开接口（候选人）只接受 token + 回答内容，绝不允许客户端指定分数/维度/题目；
- 响应结构拆成 host 视角（含计划与逐轮明细）与 candidate 视角（不含内部评分信号），
  避免把规则权重、关键词等评估细节暴露给候选人。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

InterviewLanguage = Literal["zh-CN", "en-US"]
InterviewDifficulty = Literal["junior", "middle", "senior"]


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


class AiInterviewCreateRequest(BaseModel):
    """创建 AI 数字人面试（host 侧）。"""

    model_config = ConfigDict(populate_by_name=True)

    talent_id: int | None = Field(default=None, gt=0, description="库内人才 ID（与 candidate_name 二选一）")
    position_id: int | None = Field(default=None, gt=0, description="应聘岗位 ID（可空）")
    invitation_id: int | None = Field(default=None, gt=0, description="关联面试邀约 ID（可空）")
    assessment_id: int | None = Field(default=None, gt=0, description="关联测评 ID（可空）")

    candidate_name: str | None = Field(default=None, max_length=64, description="候选人姓名（无人才档案时使用）")
    candidate_email: str | None = Field(default=None, max_length=128, description="候选人邮箱（用于发送链接）")

    title: str = Field(default="", max_length=128, description="面试标题；空则自动生成")
    interviewer_persona: str = Field(
        default="friendly_hr", max_length=32,
        description="数字人人设：friendly_hr / tech_lead / strict_expert / global_interviewer",
    )
    interviewer_name: str | None = Field(default=None, max_length=64, description="覆盖数字人姓名（可空）")
    interviewer_photo_url: str | None = Field(default=None, max_length=512, description="数字人面试官真人肖像照片 URL；空则使用人设默认头像")
    language: InterviewLanguage = Field(default="zh-CN", description="面试语言")
    difficulty: InterviewDifficulty = Field(default="middle", description="难度：junior / middle / senior")
    question_count: int = Field(default=6, ge=3, le=10, description="问题数量（3-10）")
    duration_minutes: int = Field(default=30, ge=5, le=120, description="面试时长（分钟）")
    expires_hours: int = Field(default=72, ge=1, le=720, description="面试链接有效期（小时）")
    send_email: bool = Field(default=True, description="是否向候选人邮箱发送面试链接")

    @field_validator("candidate_name", "candidate_email", "title", "interviewer_name", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("candidate_email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if "@" not in value or value.startswith("@") or value.endswith("@") or "." not in value.split("@")[-1]:
            raise ValueError("邮箱格式不正确")
        return value

    @model_validator(mode="after")
    def _require_candidate(self) -> "AiInterviewCreateRequest":
        if not self.talent_id and not self.candidate_name:
            raise ValueError("请选择库内人才，或填写候选人姓名")
        return self


class AiInterviewAnswerRequest(BaseModel):
    """候选人提交单题回答（公开接口）。"""

    answer: str = Field(..., min_length=1, max_length=4000, description="回答文本（语音转写或键盘输入）")
    duration_seconds: int = Field(default=0, ge=0, le=7200, description="本题作答耗时（秒）")
    input_mode: Literal["voice", "text"] = Field(default="text", description="作答方式")

    @field_validator("answer")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("回答不能为空")
        return text


class AiInterviewQuestionResponse(BaseModel):
    """数字人当前提问（候选人视角）。"""

    index: int
    text: str
    kind: Literal["main", "follow_up"] = "main"
    dimension: str
    dimension_label: str
    intent: str = ""


class AiInterviewTurnResponse(BaseModel):
    """逐轮对话（host 复盘视角）。"""

    id: int | None = None
    seq: int
    role: str
    kind: str
    question_index: int | None = None
    dimension: str | None = None
    content: str
    input_mode: str | None = None
    duration_seconds: int = 0
    score: float | None = None
    signals: dict[str, Any] | None = None
    feedback: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AiInterviewReportResponse(BaseModel):
    """AI 面试评估报告。"""

    total_score: float = 0
    level: str = ""
    recommendation: str = ""
    recommendation_label: str = ""
    dimension_scores: dict[str, float | None] = Field(default_factory=dict)
    dimension_labels: dict[str, str] = Field(default_factory=dict)
    dimension_weights: dict[str, float] = Field(default_factory=dict)
    question_count: int = 0
    answered_count: int = 0
    completion_rate: float = 0
    per_question: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    summary: str = ""
    scoring_method: str = "rule"
    prompt_version: str = ""
    generated_at: str | None = None
    integrity: dict[str, Any] = Field(default_factory=dict)


class AiInterviewSessionResponse(BaseModel):
    """AI 面试会话（host 列表 / 详情基础字段）。"""

    id: int
    talent_id: int | None = None
    position_id: int | None = None
    invitation_id: int | None = None
    assessment_id: int | None = None
    token: str
    url: str = ""
    title: str
    candidate_name: str
    candidate_email: str | None = None
    position_title: str | None = None
    status: str
    difficulty: str
    question_count: int
    duration_minutes: int
    language: str
    interviewer: dict[str, Any] = Field(default_factory=dict)
    answered_count: int = 0
    total_score: float | None = None
    level: str | None = None
    recommendation: str | None = None
    recommendation_label: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


class AiInterviewDetailResponse(AiInterviewSessionResponse):
    """AI 面试详情（含问题计划与逐轮记录）。"""

    plan: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[AiInterviewTurnResponse] = Field(default_factory=list)
    report: AiInterviewReportResponse | None = None


class AiInterviewProgress(BaseModel):
    """面试进度。"""

    answered: int = 0
    total: int = 0
    percent: int = 0
    current_index: int = 0
    remaining_seconds: int | None = None


class AiInterviewPublicState(BaseModel):
    """候选人公开面试页状态（不含规则权重 / 逐题评分信号）。"""

    token: str
    title: str
    status: str
    candidate_name: str
    position_title: str | None = None
    language: str
    difficulty: str
    duration_minutes: int
    question_count: int
    interviewer: dict[str, Any] = Field(default_factory=dict)
    greeting: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline: datetime | None = None
    progress: AiInterviewProgress = Field(default_factory=AiInterviewProgress)
    current_question: AiInterviewQuestionResponse | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    server_time: datetime | None = None


class AiInterviewAnswerResult(BaseModel):
    """提交回答后的结果。"""

    accepted: bool = True
    completed: bool = False
    timed_out: bool = False
    interviewer_reply: str = ""
    follow_up: bool = False
    next_question: AiInterviewQuestionResponse | None = None
    progress: AiInterviewProgress = Field(default_factory=AiInterviewProgress)
    state: AiInterviewPublicState | None = None
    result: dict[str, Any] | None = None


class AiInterviewPersonaOption(BaseModel):
    """可选数字人面试官。"""

    key: str
    name: str
    title: str
    style: str
    tagline: str = ""
    avatar_theme: str
    language: str
    voice_hint: str = ""
    photo_url: str = ""


class AiInterviewDigitalHumanVideoRequest(BaseModel):
    """生成数字人说话头视频请求。"""

    text: str = Field(..., min_length=1, max_length=2000, description="数字人播报文本")
    persona_key: str = Field(default="default", max_length=32, description="数字人人设 key")
    voice: str | None = Field(default=None, max_length=64, description="语音音色（留空则由供应商默认）")
    language: str = Field(default="zh-CN", max_length=16, description="播报语言")


class AiInterviewDigitalHumanVideoResponse(BaseModel):
    """生成数字人视频响应。"""

    ok: bool = False
    provider: str = ""
    video_url: str = ""
    elapsed_ms: float = 0.0
    error: str = ""


class AiInterviewConfigResponse(BaseModel):
    """创建面试所需的配置项。"""

    personas: list[AiInterviewPersonaOption] = Field(default_factory=list)
    default_question_count: int = 6
    max_question_count: int = 10
    default_duration_minutes: int = 30
    default_expires_hours: int = 72
    languages: list[dict[str, str]] = Field(default_factory=list)
    difficulties: list[dict[str, str]] = Field(default_factory=list)


__all__ = [
    "AiInterviewAnswerRequest",
    "AiInterviewAnswerResult",
    "AiInterviewConfigResponse",
    "AiInterviewCreateRequest",
    "AiInterviewDigitalHumanVideoRequest",
    "AiInterviewDigitalHumanVideoResponse",
    "AiInterviewDetailResponse",
    "AiInterviewPersonaOption",
    "AiInterviewProgress",
    "AiInterviewPublicState",
    "AiInterviewQuestionResponse",
    "AiInterviewReportResponse",
    "AiInterviewSessionResponse",
    "AiInterviewTurnResponse",
]
