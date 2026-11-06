"""
功能：定义匹配结果及批次快照的数据库结构。
输入：由Service构造的ORM字段。
输出：matching_records人才明细、matching_batches批次快照。
流程：批次ID关联多个人才明细；快照独立保存零输出报告。
边界：模型不执行匹配算法；存量数据库新增表或列需迁移。

标签：[Agent组] [P0]（匹配记录与批次快照表模型）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MatchingBatch(BaseModel):
    """每次正式匹配的结果快照；零输出也保存，不伪造人才明细。"""
    __tablename__ = "matching_batches"
    batch_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    position_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    matched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_scope: Mapped[dict] = mapped_column(JSON, nullable=False)


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

    # ======================================================================
    # *kwq* 新增（MATCH-009）：匹配结果可追溯字段。
    # 修改原因：
    #   1) 原表只有分数与几段文本，无法回答「这条结论当时依据哪版画像、哪些四态判定」，
    #      而业务要求「证据可追溯、解释可复核」，仅靠日志不可查询、不可审计；
    #   2) explanation 与 hard_results 若塞进 highlights/gaps 会污染字段语义
    #      （前者是解释文本，后者是事实技能命中），查询与展示都会错位；
    #   3) recall_mode 必须落库，否则重放历史批次时无法区分向量召回与 SQL 降级。
    # 变更内容：4 个「可空」列（不设默认值、不参与唯一键、不改既有列类型），
    #          对既有数据与既有查询完全兼容（旧行为 NULL）。
    # 影响范围：
    #   - 新增列：requirement_version / recall_mode / hard_results / explanation / stats；
    #   - 需要执行 DDL：见 docs 交付说明中的 ALTER TABLE（新库由 create_all 直接建出）；
    #   - 未改动 UniqueConstraint，重复跑批仍以 (batch_id, position_id, talent_id) 约束。
    # ======================================================================
    requirement_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次匹配使用的岗位画像版本"
    )
    recall_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="召回方式：vector / sql_fallback（如实记录，不虚报）"
    )
    hard_results: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="四态判定明细（HardConditionResult 列表）"
    )
    explanation: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="校验后的匹配解释（MatchExplanation）"
    )
    stats: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="跑批统计（候选/淘汰/待补全/输出）"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MatchingRecord id={self.id} position_id={self.position_id} talent_id={self.talent_id} score={self.score}>"
