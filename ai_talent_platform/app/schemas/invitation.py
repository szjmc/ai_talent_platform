"""人才邀约 / 笔试 / 面试间相关请求响应模型。

标签：[Agent组] [P1]
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

InviteType = Literal["written_test", "assessment", "interview"]
InviteChannel = Literal["sms", "email"]


class InviteCreateRequest(BaseModel):
    """创建并发送一条邀约（host 侧，需 talent:update 权限）。"""

    model_config = ConfigDict(populate_by_name=True)

    type: InviteType = Field(default="written_test", description="written_test 笔试 / assessment 测评 / interview 面试")
    talent_id: int | None = Field(default=None, gt=0, description="库内人才 ID（演示数据可不传）")
    talent_name: str | None = Field(default=None, max_length=64, description="候选人姓名（无档案时用快照）")
    mobile: str | None = Field(default=None, max_length=20, description="手机号（sms 渠道必填）")
    email: str | None = Field(default=None, max_length=128, description="邮箱（email 渠道必填）")
    channels: list[InviteChannel] = Field(default_factory=lambda: ["sms"], description="发送渠道：sms / email")
    question_count: int | None = Field(default=None, ge=1, le=100, description="笔试题量；空=按简历解析结果自动")
    duration_minutes: int | None = Field(default=None, ge=5, le=600, description="笔试时长（分钟）")
    interview_time: datetime | None = Field(default=None, description="面试约定时间（type=interview 必填）")
    expires_hours: int | None = Field(default=None, ge=1, le=720, description="链接有效期（小时），默认取配置")
    title: str = Field(default="", max_length=128, description="邀约标题")

    @field_validator("channels")
    @classmethod
    def _unique_channels(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("至少选择一个发送渠道")
        if len(set(v)) != len(v):
            raise ValueError("发送渠道重复")
        return v

    @model_validator(mode="after")
    def _check_type(self) -> "InviteCreateRequest":
        if self.type == "interview" and self.interview_time is None:
            raise ValueError("面试邀约必须设置 interview_time")
        if "sms" in self.channels and not self.mobile and not self.talent_id:
            raise ValueError("短信发送需要手机号 mobile（或绑定库内人才）")
        if "email" in self.channels and not self.email and not self.talent_id:
            raise ValueError("邮件发送需要邮箱 email（或绑定库内人才）")
        return self


class InviteSendResult(BaseModel):
    """单渠道发送结果；mode 由发送端显式返回，当前实现不会返回 mock 成功。"""

    channel: str
    ok: bool
    mode: str = "mock"
    error: str | None = None


class InviteResponse(BaseModel):
    """邀约（host 侧，含 token 与链接）。"""

    id: int
    talent_id: int | None = None
    talent_name: str | None = None
    type: str
    title: str
    token: str
    url: str
    status: str
    channels: list[str] = Field(default_factory=list)
    send_results: list[InviteSendResult] = Field(default_factory=list)
    question_count: int | None = None
    duration_minutes: int
    interview_time: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class InviteVisitResponse(BaseModel):
    """公开落地页信息（候选人持 token 访问，无需登录）。"""

    type: str
    title: str
    status: str
    candidate_name: str | None = None
    interview_time: datetime | None = None
    expires_at: datetime | None = None
    duration_minutes: int
    question_count: int | None = None
    proctor_enabled: bool = True
    proctor_engine: str = "disabled"
    frame_interval_seconds: int = 5
    ice_servers: list[dict[str, Any]] = Field(default_factory=list)


class ExamQuestionResponse(BaseModel):
    """公开题面（answer 字段永不返回；options=[{key,text}]，作答提交 key）。"""

    id: int
    seq: int
    type: str
    content: str
    options: list[dict[str, str]] | None = None


class ExamStartResponse(BaseModel):
    """开始/恢复作答：返回题面快照与截止时间。"""

    status: str
    started_at: datetime | None = None
    deadline: datetime | None = None
    questions: list[ExamQuestionResponse] = Field(default_factory=list)
    total_score: float = 0
    proctor_enabled: bool = True
    proctor_engine: str = "disabled"
    frame_interval_seconds: int = 5


class ExamSubmitRequest(BaseModel):
    """交卷：{question_id: answer}；单选 str、多选 list[str]、文本 str。"""

    answers: dict[str, Any] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def _cap(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 300:
            raise ValueError("答案数量异常")
        return v


class ExamResultResponse(BaseModel):
    """交卷/查询结果（客观题即时出分；含文本题则待人工）。"""

    status: str
    score: float | None = None
    total_score: float | None = None
    submitted_at: datetime | None = None
    violations_count: int = 0
    pending_manual: bool = False


class FrameCheckRequest(BaseModel):
    """监考帧上报：base64 JPEG/PNG（可带 data: 前缀）。"""

    image: str = Field(..., min_length=16, max_length=600_000)


class ViolationItem(BaseModel):
    ts: float
    type: str
    detail: str


class FrameCheckResponse(BaseModel):
    """帧检测结果：新增/累计违规 + 引擎状态。"""

    enabled: bool
    engine: str
    violations: list[ViolationItem] = Field(default_factory=list)
    total_violations: int = 0
    person_like: int = 0
