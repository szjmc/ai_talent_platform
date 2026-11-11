"""
模块：app/schemas/proctoring.py
职责：在线监考 / 防作弊接口契约（上报、状态、HR 复盘、可疑快照）。

标签：[Agent组] [P1]（防作弊监考接口契约：切屏 / 远程控制 / 投屏 / 多显示器）

================================ 设计思路 ================================
1. 上报侧「宽进严出」：客户端可以上报任意事件名（后端归一化，不因拼写差异丢证据），
   但请求体有硬上限（事件条数、快照体积、字符串长度），防止被当成刷分或存储炸弹；
2. 状态侧只返回候选人「需要知道的」：风险等级、是否被阻断、还需要完成哪些环境要求
   （共享屏幕 / 单显示器 / 关闭远程软件），**不返回**具体权重与判定阈值，
   避免候选人按分数反推「作弊到什么程度才被抓」；
3. HR 侧返回完整证据链：事件时间线、快照、硬性违规、建议处置，
   与 AI 面试报告里的 integrity / observations 口径一致。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 单张快照 base64 上限（约 1.2MB 的图片，前端已压到 320px 宽）
_SNAPSHOT_MAX_CHARS = 1_600_000

# 监考对象类型（与 models/proctoring.SUBJECT_TYPES 对应）
SubjectType = str


def _clean(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


class ProctorEventIn(BaseModel):
    """客户端上报的单条事件（宽进：type 允许任意字符串，服务端归一化）。"""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., min_length=1, max_length=48, description="事件类型，如 tab_hidden / remote_control / casting")
    detail: str = Field(default="", max_length=200, description="事件详情（可选，便于人工复核）")
    ts: float | int | None = Field(default=None, description="客户端时间戳（秒或毫秒，可空）")
    source: str = Field(default="client", max_length=16, description="来源标记，默认 client")

    @field_validator("type", mode="before")
    @classmethod
    def _strip_type(cls, value: Any) -> Any:
        return _clean(value, 48).lower() if isinstance(value, str) else value

    @field_validator("detail", mode="before")
    @classmethod
    def _strip_detail(cls, value: Any) -> Any:
        return _clean(value, 200) if isinstance(value, str) else value


class ProctorHeartbeatRequest(BaseModel):
    """监考心跳 / 事件批量上报（前端每 N 秒一次，事件可空 = 纯心跳）。"""

    model_config = ConfigDict(extra="ignore")

    events: list[ProctorEventIn] = Field(default_factory=list, max_length=50, description="本次新增事件")
    elapsed_seconds: int = Field(default=0, ge=0, le=86_400, description="进入考试/面试后经过的秒数")
    screen_shared: bool = Field(default=False, description="当前是否已共享屏幕")
    remote_control: bool | None = Field(default=None, description="前端可见的远程控制迹象（可选）")
    display_count: int = Field(default=1, ge=0, le=16, description="当前显示器数量")
    camera_id: str | None = Field(default=None, max_length=128, description="当前摄像头 deviceId")
    camera_label: str | None = Field(default=None, max_length=128, description="当前摄像头名称")
    virtual_camera: bool | None = Field(default=None, description="是否疑似虚拟摄像头")
    timezone: str | None = Field(default=None, max_length=64, description="浏览器时区")
    screen_width: int | None = Field(default=None, ge=0, le=20_000)
    screen_height: int | None = Field(default=None, ge=0, le=20_000)
    platform: str | None = Field(default=None, max_length=64, description="navigator.platform 快照")
    user_agent: str | None = Field(default=None, max_length=512, description="User-Agent（审计）")
    focus_lost_count: int = Field(default=0, ge=0, le=10_000, description="前端累计失焦次数（校验用）")

    @field_validator("camera_id", "camera_label", "timezone", "platform", "user_agent", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _clean(value, 512) or None if isinstance(value, str) else value


class ProctorSnapshotRequest(BaseModel):
    """可疑快照上报（摄像头或屏幕抽样帧，base64 JPEG/PNG）。"""

    image: str = Field(..., min_length=16, max_length=_SNAPSHOT_MAX_CHARS, description="base64 图片（可带 data: 前缀）")
    source: str = Field(default="screen", pattern="^(camera|screen)$", description="来源：camera / screen")
    trigger_type: str = Field(default="", max_length=40, description="触发快照的事件类型")
    ts: float | int | None = Field(default=None, description="客户端拍摄时间戳")


class ProctorRequirement(BaseModel):
    """候选人当前还需满足的环境要求（前端据此弹出阻断提示）。"""

    screen_share: bool = Field(default=False, description="是否要求共享屏幕")
    single_display: bool = Field(default=True, description="是否要求仅使用一块显示器")
    no_remote_control: bool = Field(default=True, description="是否要求关闭远程控制软件")
    no_virtual_camera: bool = Field(default=True, description="是否要求使用物理摄像头")
    fullscreen: bool = Field(default=True, description="是否要求保持全屏作答")


class ProctorStateResponse(BaseModel):
    """候选人视图：我要继续，还差什么。"""

    enabled: bool = True
    strict: bool = True
    risk_level: str = "clean"
    blocked: bool = False
    block_reason: str = ""
    warning: str = ""
    requirements: ProctorRequirement = Field(default_factory=ProctorRequirement)
    heartbeat_seconds: int = 15
    snapshot_interval_seconds: int = 20
    screen_shared: bool = False
    display_count: int = 1
    event_total: int = 0
    last_heartbeat_at: datetime | None = None


class ProctorReportItem(BaseModel):
    """HR 视图的一条事件。"""

    ts: float | None = None
    type: str
    label: str = ""
    detail: str = ""
    score: float = 0
    source: str = "client"


class ProctorSnapshotItem(BaseModel):
    """HR 视图的一张可疑快照。"""

    id: int
    source: str
    trigger_type: str = ""
    captured_at: datetime | None = None
    url: str = ""
    note: str | None = None


class ProctorSessionSummary(BaseModel):
    """HR 列表行：谁可疑、可疑到什么程度。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    subject_type: str
    subject_id: int | None = None
    subject_token: str = ""
    candidate_name: str = ""
    position_title: str | None = None
    risk_score: float = 0
    risk_level: str = "clean"
    risk_advice: str = "clean"
    blocked: bool = False
    hard_violations: list[str] = Field(default_factory=list)
    event_total: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    created_at: datetime | None = None


class ProctorReportResponse(ProctorSessionSummary):
    """HR 详情：汇总 + 事件时间线 + 快照 + 结论。"""

    message: str = ""
    require_review: bool = False
    void_suggested: bool = False
    event_counts: dict[str, int] = Field(default_factory=dict)
    event_labels: dict[str, str] = Field(default_factory=dict)
    timeline: list[ProctorReportItem] = Field(default_factory=list)
    snapshots: list[ProctorSnapshotItem] = Field(default_factory=list)
    integrity: dict[str, Any] = Field(default_factory=dict)
    strict_mode: bool = True
    require_screen_share: bool = False
    screen_shared: bool = False
    remote_control: bool = False
    display_count: int = 1
    heartbeat_lost: bool = False


class ProctorAckResponse(BaseModel):
    """上报回执：候选人视图 + 本次新增的违规（前端用于即时提示）。"""

    state: ProctorStateResponse
    new_violations: list[ProctorReportItem] = Field(default_factory=list)
    score: float = 0
    level: str = "clean"


__all__ = [
    "ProctorAckResponse",
    "ProctorEventIn",
    "ProctorHeartbeatRequest",
    "ProctorReportItem",
    "ProctorReportResponse",
    "ProctorRequirement",
    "ProctorSessionSummary",
    "ProctorSnapshotItem",
    "ProctorSnapshotRequest",
    "ProctorStateResponse",
    "SubjectType",
]
