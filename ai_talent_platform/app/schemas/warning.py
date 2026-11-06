"""
模块：app/schemas/warning.py
职责：岗位储备预警的请求 / 响应契约。

设计原因：
- 预警属于匹配业务域（需求模块五），但与「跑批」契约解耦：单独文件，
  避免匹配 Schema 膨胀；
- 扫描结果必须包含 created / resolved / unchanged 三个计数，让「数据闭环」
  （过期预警自动解决）可被核对，而不是只返回一个列表。

标签：[Agent组] [P1]（储备预警出入参 Schema）
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.reserve_warning import WARNING_TYPES
from app.schemas.common import BaseSchema

WarningTypeLiteral = Literal[
    "position_no_output", "position_reserve_shortage", "position_long_vacant"
]

assert set(WARNING_TYPES) == {
    "position_no_output",
    "position_reserve_shortage",
    "position_long_vacant",
}, "WARNING_TYPES 常量与 Schema 的 Literal 定义不同步，请同时修改"


class ReserveWarningItem(BaseSchema):
    """一条预警记录。"""

    id: int = Field(..., description="预警 ID")
    warning_type: WarningTypeLiteral = Field(..., description="预警类型")
    severity: Literal["high", "medium", "low"] = Field(..., description="严重级别")
    position_id: int | None = Field(None, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称")
    talent_id: int | None = Field(None, description="人才 ID（人才侧预警预留）")
    batch_id: str | None = Field(None, max_length=32, description="触发跑批 ID")
    message: str = Field(..., max_length=256, description="预警摘要")
    detail: dict | None = Field(None, description="明细（计数 / 阈值 / 原因摘要）")
    resolved: bool = Field(False, description="是否已解决")
    resolved_at: datetime | None = Field(None, description="解决时间")
    created_at: datetime | None = Field(None, description="产生时间")


class WarningScanRequest(BaseSchema):
    """触发一次储备预警扫描。"""

    position_ids: list[int] | None = Field(
        None, description="限定扫描的岗位；为空表示扫描全部开放岗位"
    )
    shortage_threshold: int = Field(
        3, ge=1, le=100, description="储备不足阈值：最新批次 PASS 人数低于该值时预警"
    )
    long_vacant_days: int = Field(
        90, ge=1, le=3650, description="长期空缺阈值：开放岗位超过该天数从未跑批时预警"
    )
    # *kwq* 默认 True：扫描是幂等的，同类型未解决预警不重复产生；过期预警自动解决。
    auto_resolve: bool = Field(
        True, description="是否自动解决「已不再成立」的未解决预警（数据闭环）"
    )


class WarningScanResponse(BaseSchema):
    """扫描结果（含闭环计数）。"""

    positions_scanned: int = Field(0, ge=0, description="扫描的岗位数")
    created: int = Field(0, ge=0, description="本次新产生的预警数")
    resolved: int = Field(0, ge=0, description="本次自动解决的过期预警数")
    unchanged: int = Field(0, ge=0, description="已存在且仍然成立的预警数")
    items: list[ReserveWarningItem] = Field(
        default_factory=list, description="当前全部未解决预警（本次新建 + 沿用）"
    )


class WarningResolveRequest(BaseSchema):
    """人工解决预警。"""

    note: str | None = Field(None, max_length=256, description="处理备注（可空）")


__all__ = [
    "ReserveWarningItem",
    "WarningResolveRequest",
    "WarningScanRequest",
    "WarningScanResponse",
    "WarningTypeLiteral",
]
