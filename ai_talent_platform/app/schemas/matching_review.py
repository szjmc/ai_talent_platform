"""
模块：app/schemas/matching_review.py
职责：HR 人工复核流程的请求 / 响应契约（本阶段新建）。

设计原因：
- 复核属于匹配业务域，但与「跑批」契约解耦，单独成文件避免 matching.py 继续膨胀
  （与 warning.py 同样的理由）；
- 状态与原因码用 Literal 从模型常量派生，Schema 与模型不会各写一套导致漂移；
- 所有响应字段都能从 matching_reviews / matching_review_events 直接映射，
  不做二次加工，保证「库里是什么，接口就是什么」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.matching_review import (
    REVIEW_PENDING,
    REVIEW_REASON_CODES,
    REVIEW_STATUSES,
)
from app.schemas.common import BaseSchema

ReviewStatusLiteral = Literal[
    "pending", "shortlisted", "interviewing", "rejected", "hired"
]
ReviewReasonLiteral = Literal[
    "skill_gap",
    "experience_gap",
    "education",
    "salary_mismatch",
    "availability",
    "better_candidate",
    "withdraw",
    "other",
]

assert set(REVIEW_STATUSES) == {
    "pending", "shortlisted", "interviewing", "rejected", "hired"
}, "REVIEW_STATUSES 常量与 Schema 的 Literal 定义不同步，请同时修改"
assert set(REVIEW_REASON_CODES) == {
    "skill_gap", "experience_gap", "education", "salary_mismatch",
    "availability", "better_candidate", "withdraw", "other",
}, "REVIEW_REASON_CODES 常量与 Schema 的 Literal 定义不同步，请同时修改"


class MatchingReviewUpsertRequest(BaseSchema):
    """提交一次复核决策（同一批次同一人才重复提交为更新，不是新增）。

    expected_version 的用途：
        复核工作台可能被两个人同时打开（HR 与用人经理）。带上 version 后，
        后者提交会收到 409 而不是静默覆盖前者的决策。不传则跳过该检查
        （兼容脚本导入场景），但生产前端应始终传。
    """

    status: ReviewStatusLiteral = Field(..., description="目标复核状态")
    reason_code: ReviewReasonLiteral | None = Field(
        None, description="原因码；status=rejected 时必填"
    )
    note: str | None = Field(None, max_length=512, description="备注（reason_code=other 时必填）")
    expected_version: int | None = Field(
        None, ge=1, description="乐观锁版本号；与库中不一致返回 409，防止并发覆盖"
    )

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        """去掉首尾空白；全空白视为未填（否则会出现『   』这种假备注）。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class MatchingReviewItem(BaseSchema):
    """一条复核记录的当前状态。"""

    id: int = Field(..., description="复核记录 ID")
    batch_id: str = Field(..., max_length=32, description="匹配批次 ID")
    position_id: int = Field(..., description="岗位 ID")
    talent_id: int = Field(..., description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名（来自批次快照）")
    review_status: ReviewStatusLiteral = Field(
        REVIEW_PENDING, description="当前复核状态"
    )
    status_label: str | None = Field(None, max_length=16, description="状态中文名")
    reason_code: ReviewReasonLiteral | None = Field(None, description="决策原因码")
    note: str | None = Field(None, max_length=512, description="备注")
    score_snapshot: float | None = Field(
        None, description="决策时的匹配分快照（不是当前重算值）"
    )
    rank_snapshot: int | None = Field(None, description="决策时的名次快照")
    decided_by: int | None = Field(None, description="最后决策人用户 ID")
    decided_at: datetime | None = Field(None, description="最后决策时间")
    version: int = Field(1, ge=1, description="乐观锁版本号（提交时回传为 expected_version）")
    created_at: datetime | None = Field(None, description="首次创建时间")
    updated_at: datetime | None = Field(None, description="最后更新时间")


class MatchingReviewEventItem(BaseSchema):
    """一条复核动作流水（append-only）。"""

    id: int = Field(..., description="流水 ID")
    review_id: int = Field(..., description="所属复核记录 ID")
    from_status: ReviewStatusLiteral | None = Field(None, description="变更前状态；首次为 None")
    to_status: ReviewStatusLiteral = Field(..., description="变更后状态")
    reason_code: ReviewReasonLiteral | None = Field(None, description="本次原因码")
    note: str | None = Field(None, max_length=512, description="本次备注")
    operator_id: int | None = Field(None, description="操作人用户 ID")
    created_at: datetime | None = Field(None, description="操作时间")


class MatchingReviewDecideResponse(BaseSchema):
    """一次决策的结果。

    changed=False 表示「提交的状态与当前状态相同」，未产生流水：
        这既让重试安全（网络重发不会刷出重复流水），也让前端能区分
        「真的推进了」和「点了个已经点过的按钮」。
    """

    review: MatchingReviewItem = Field(..., description="更新后的复核记录")
    event: MatchingReviewEventItem | None = Field(
        None, description="本次产生的流水；状态未变化时为 None"
    )
    changed: bool = Field(True, description="状态是否真的发生了变化")
    message: str | None = Field(None, max_length=128, description="提示语")


class ReviewFunnelStage(BaseSchema):
    """漏斗的单个阶段。"""

    status: ReviewStatusLiteral = Field(..., description="阶段状态")
    label: str = Field(..., max_length=16, description="阶段中文名")
    count: int = Field(0, ge=0, description="当前处于该阶段的人数")


class ReviewFunnelResponse(BaseSchema):
    """岗位级复核漏斗。

    口径说明（写死在响应里，避免报表各说各话）：
        - 统计的是「最新一次复核状态」，不是历史累计进入过该阶段的人数；
        - 因此各阶段 count 之和 = 已建档人数 ≤ 该岗位批次输出人数；
        - 转化率分母为 pending+shortlisted+interviewing+rejected+hired 的合计。
    """

    position_id: int = Field(..., description="岗位 ID")
    total: int = Field(0, ge=0, description="已建档的复核记录总数")
    stages: list[ReviewFunnelStage] = Field(
        default_factory=list, description="按漏斗顺序排列的各阶段人数"
    )
    converted: int = Field(0, ge=0, description="已推进到面试及以后（面试中+录用）的人数")
    hired_rate: float = Field(0.0, ge=0, le=1, description="录用率 = hired / total")
    caliber_note: str | None = Field(None, max_length=256, description="统计口径说明")
