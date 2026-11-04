"""
模块：app/models/position.py
职责：岗位表 + 岗位能力要求（多版本）。

标签：[Agent组] [P0]（岗位表，被 position_repository / position_search 技能依赖）

============================ 实现思路与代码要点 ============================
表设计：
1. positions（岗位）
   - title、code(唯一)、organization_id、level(初/中/高/专家)、
     headcount(编制)、status(open/closed/paused)、
     description(岗位说明书全文)、requirements(JSON 结构化要求)、
     skill_tags(JSON)、salary_min/max、publish_at、close_at
2. position_requirements（岗位能力模型，多版本）
   - position_id、version、dimensions(JSON)、weights(JSON 维度权重)、source
关键思路：
- requirements 结构化后才能做「硬性条件过滤 + 软性加权」匹配；
- 岗位向量（job embedding）在岗位创建/更新时写入 Milvus，需在 Service 中触发；
- 岗位关闭后不再参与匹配，但历史匹配记录保留。
已完成：
- [x] 模型字段与索引（organization_id + status 联合索引）
待实现清单：
- [ ] PositionRepository：list_open_by_org、get_requirements、search_by_title
- [ ] PositionService：创建/更新时触发岗位向量化
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Position(BaseModel):
    """岗位。"""

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_organization_id_status", "organization_id", "status"),
        {"comment": "岗位表"},
    )

    title: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="岗位名称")
    code: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="岗位编码"
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属部门 ID（FK -> organizations.id）",
    )
    level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="middle",
        server_default=text("'middle'"),
        index=True,
        comment="岗位层级：junior/middle/senior/expert",
    )
    headcount: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="编制人数"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
        server_default=text("'open'"),
        comment="状态：open/paused/closed（open ⇄ paused，closed 单向）",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="岗位说明书全文")
    requirements: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="结构化要求：education/experience_years/skills/certificates/soft_skills/weights",
    )
    skill_tags: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="技能标签数组")
    salary_min: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="薪资下限（元/月）"
    )
    salary_max: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="薪资上限（元/月）"
    )
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="发布时间")
    close_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="关闭时间")

    # ------------------------------------------------------------------ 关系
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", lazy="selectin", back_populates="positions"
    )
    # 注意：属性名不能用 requirements（与 JSON 列同名），故用 requirement_versions
    requirement_versions: Mapped[list["PositionRequirement"]] = relationship(
        "PositionRequirement",
        back_populates="position",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Position id={self.id} title={self.title}>"


class PositionRequirement(BaseModel):
    """岗位能力模型（多版本，同岗位 version 递增）。"""

    __tablename__ = "position_requirements"
    __table_args__ = (
        UniqueConstraint("position_id", "version", name="uq_position_requirements_position_version"),
        {"comment": "岗位能力模型表（多版本）"},
    )

    position_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("positions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="岗位 ID（FK -> positions.id，CASCADE）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="版本号（同岗位递增）"
    )
    dimensions: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="维度定义：技能/经验/学历/素质"
    )
    weights: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="维度权重（合计 1.0）")
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default=text("'manual'"),
        comment="来源：manual/ai_parse",
    )

    position: Mapped["Position"] = relationship("Position", back_populates="requirement_versions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PositionRequirement id={self.id} position_id={self.position_id} v{self.version}>"
