"""
模块：app/models/reserve_warning.py
职责：岗位储备预警记录表。

============================ 实现思路与代码要点 ============================
表设计：reserve_warnings（需求输出版 §07 岗位储备预警）
- 预警类型 warning_type：
    position_no_output         岗位零产出：最新批次 output=0（无可用候选人）severity=high
    position_reserve_shortage  储备不足：最新批次 PASS 人数低于阈值 severity=medium
    position_long_vacant       长期空缺：开放岗位超过 N 天从未跑批 severity=medium
- 关键思路：
  1) 逻辑外键（position_id / talent_id / batch_id），不建物理外键 —— 预警是派生数据，
     源数据删除后预警仍应可追溯；
  2) 幂等扫描：同 (warning_type, position_id) 只允许一条「未解决」记录，
     由 Service 层在扫描时去重（resolved 后允许再次产生）；
  3) 数据闭环：扫描时自动把「已不再成立」的未解决预警标记 resolved（§7.3 要求）。
标签：[Agent组] [P1]（储备预警表模型，需求模块五）
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

# 预警类型常量（Service / Schema / 前端共用，禁止散落字符串）
WARNING_POSITION_NO_OUTPUT = "position_no_output"
WARNING_POSITION_SHORTAGE = "position_reserve_shortage"
WARNING_POSITION_LONG_VACANT = "position_long_vacant"
WARNING_TYPES: tuple[str, ...] = (
    WARNING_POSITION_NO_OUTPUT,
    WARNING_POSITION_SHORTAGE,
    WARNING_POSITION_LONG_VACANT,
)

# 严重级别
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


class ReserveWarning(BaseModel):
    """岗位储备预警记录（扫描产生，可人工解决）。"""

    __tablename__ = "reserve_warnings"
    __table_args__ = (
        Index("ix_reserve_warnings_position_type_resolved", "position_id", "warning_type", "resolved"),
        {"comment": "岗位储备预警记录表"},
    )

    warning_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="预警类型（见 WARNING_TYPES 常量）"
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SEVERITY_MEDIUM, comment="严重级别：high/medium/low"
    )
    position_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="岗位 ID（逻辑外键，岗位删除后保留）"
    )
    talent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="人才 ID（逻辑外键，预留人才侧预警）"
    )
    batch_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="触发预警的跑批 ID（可追溯）"
    )
    message: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="人可读的预警摘要"
    )
    detail: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="预警明细（计数、阈值、淘汰原因摘要等）"
    )
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", comment="是否已解决"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="解决时间"
    )
    resolved_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="解决人用户 ID（自动闭环时为 NULL）"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1", comment="乐观锁版本"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReserveWarning id={self.id} type={self.warning_type} "
            f"position_id={self.position_id} resolved={self.resolved}>"
        )


__all__ = [
    "ReserveWarning",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "WARNING_POSITION_LONG_VACANT",
    "WARNING_POSITION_NO_OUTPUT",
    "WARNING_POSITION_SHORTAGE",
    "WARNING_TYPES",
]
