"""
模块：app/models/matching.py
职责：人岗匹配记录表。

============================ 实现思路与代码要点 ============================
表设计：matching_records
- position_id、talent_id、score(0-100)、dimension_scores(JSON)、
  highlights(JSON)、gaps(JSON)、suggestion、strategy(hard/soft/hybrid)、
  model_name、matched_by(用户 ID)、matched_at、batch_id
关键思路：
- 每次匹配跑批生成一批记录，用 batch_id(uuid hex) 归组，便于前端按批次展示；
- 唯一索引 (batch_id, position_id, talent_id) 防重复写入；
- 匹配记录是「可重算数据」，量大会按月分区（本实现先落 MySQL，后续可迁 PgSQL）。
已完成：
- [x] 模型字段与索引
待实现清单：
- [ ] MatchingRepository：get_latest_by_position、get_history_by_talent、save_batch
- [ ] MatchingService：硬性过滤 + 向量召回 + 加权打分 + AI 解释
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MatchingRecord(BaseModel):
    """人岗匹配记录（按 batch_id 归组，可重算）。"""

    __tablename__ = "matching_records"
    __table_args__ = (
        UniqueConstraint("batch_id", "position_id", "talent_id", name="uq_matching_records_batch_position_talent"),
        Index("ix_matching_records_position_id_score", "position_id", "score"),
        {"comment": "人岗匹配记录表"},
    )

    batch_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="跑批 ID（uuid hex）")
    # 逻辑外键：匹配记录量大、可重建，且与 positions/talents 生命周期不一致，
    # 因此不建物理外键，由 Service 校验存在性。
    position_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="岗位 ID（逻辑外键）")
    talent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="人才 ID（逻辑外键）"
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default="0", comment="匹配总分 0-100"
    )
    dimension_scores: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="维度分：skill/experience/education/quality"
    )
    highlights: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="优势亮点")
    gaps: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="短板")
    suggestion: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="适配建议")
    strategy: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="匹配策略：hard/soft/hybrid"
    )
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="AI 解释所用模型")
    matched_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="触发用户 ID")
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="匹配时间（分区键）"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MatchingRecord id={self.id} position_id={self.position_id} talent_id={self.talent_id} score={self.score}>"
