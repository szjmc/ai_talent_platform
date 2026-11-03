"""
模块：app/models/talent.py
职责：人才档案表 + 人才画像表。

============================ 实现思路与代码要点 ============================
表设计：
1. talents（人才主档）
   - 基本：name、gender、birth_date、mobile、email、id_card(加密存储)
   - 教育：education(最高学历)、school、major
   - 工作：work_years、current_company、current_position、organization_id
   - 能力：skill_tags(JSON 数组)、certificates(JSON)、self_evaluation
   - 画像摘要：portrait_summary、potential_level、status(在职/离职/储备)
   - 系统：source(手动/AI解析)、resume_id(来源简历)、is_deleted 等（继承 BaseModel）
2. talent_portraits（人才画像，1:1 或 1:N 存版本）
   - talent_id、version、dimensions(JSON: 技能/经验/素质/潜力分数)、
     labels(JSON: AI 标签)、strengths、weaknesses、suggested_positions、
     potential_level、model_name、prompt_version、generated_at
关键思路：
- 唯一性：mobile + is_deleted 组合唯一（逻辑删除后允许重新录入）；
- skill_tags 用 JSON 列，MySQL 8 支持 JSON 函数查询；需要高频检索的技能另建 talent_skills 关联表；
- 画像由 AI 生成，属于「可重建数据」，与主档分开存储，避免污染核心表。
已完成：
- [x] 模型字段与索引
待实现清单：
- [ ] TalentRepository：get_by_mobile、search_by_skills(JSON)、get_by_ids、count_by_org
- [ ] TalentService：创建/更新/查重/画像刷新
- [ ] 画像生成流程：ai/agents/talent_agent.py + ai/workflows/analysis_workflow.py
=========================================================================
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Talent(BaseModel):
    """人才档案（主档）。"""

    __tablename__ = "talents"
    __table_args__ = (
        UniqueConstraint("mobile", "is_deleted", name="uq_talents_mobile_is_deleted"),
        Index("ix_talents_organization_id_status", "organization_id", "status"),
        {"comment": "人才档案表"},
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="姓名")
    gender: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
        comment="性别：male/female/unknown",
    )
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="出生日期")
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="手机号")
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="邮箱")
    id_card: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="身份证号（AES 加密存储，响应脱敏）"
    )
    education: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="最高学历")
    school: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="毕业院校")
    major: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="专业")
    work_years: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), index=True, comment="工作年限"
    )
    current_company: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="现公司")
    current_position: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="现职位")
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        comment="归属部门 ID（FK -> organizations.id，SET NULL）",
    )
    skill_tags: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="技能标签数组")
    certificates: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="证书列表")
    self_evaluation: Mapped[str | None] = mapped_column(Text, nullable=True, comment="自我评价")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        comment="状态：active/left/reserve",
    )
    potential_level: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="潜力等级：A/B/C/D（画像生成）"
    )
    portrait_summary: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="画像摘要")
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="manual",
        server_default=text("'manual'"),
        comment="来源：manual/ai_resume",
    )
    # 逻辑外键：resumes.talent_id 已指向 talents，这里再建物理外键会形成循环依赖，
    # 因此 resume_id 只在应用层保证有效性（见 docs/database_design.md 2.5 外键策略）。
    resume_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="来源简历 ID（逻辑外键 -> resumes.id）"
    )

    # ------------------------------------------------------------------ 关系
    organization: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization", lazy="selectin", back_populates="talents"
    )
    portraits: Mapped[list["TalentPortrait"]] = relationship(
        "TalentPortrait", back_populates="talent", cascade="all, delete-orphan", lazy="select"
    )
    resumes: Mapped[list["Resume"]] = relationship(  # noqa: F821
        "Resume", back_populates="talent", foreign_keys="Resume.talent_id", lazy="select"
    )
    training_plans: Mapped[list["TrainingPlan"]] = relationship(  # noqa: F821
        "TrainingPlan", back_populates="talent", cascade="all, delete-orphan", lazy="select"
    )
    learning_records: Mapped[list["LearningRecord"]] = relationship(  # noqa: F821
        "LearningRecord", back_populates="talent", cascade="all, delete-orphan", lazy="select"
    )

    @property
    def age(self) -> int | None:
        """根据出生日期计算周岁（无出生日期返回 None）。"""
        if self.birth_date is None:
            return None
        today = date.today()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Talent id={self.id} name={self.name}>"


class TalentPortrait(BaseModel):
    """人才画像（版本化，AI 可重建数据）。"""

    __tablename__ = "talent_portraits"
    __table_args__ = (
        UniqueConstraint("talent_id", "version", name="uq_talent_portraits_talent_version"),
        {"comment": "人才画像表（版本化）"},
    )

    talent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="人才 ID（FK -> talents.id，CASCADE）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="画像版本（每次重算 +1）"
    )
    dimensions: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="维度分：skill/experience/quality/potential，0-100"
    )
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="AI 标签数组")
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="优势列表")
    weaknesses: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="短板列表")
    suggested_positions: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="建议岗位")
    potential_level: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="潜力等级")
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="生成模型")
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="提示词版本")
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="生成时间"
    )

    talent: Mapped["Talent"] = relationship("Talent", back_populates="portraits")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TalentPortrait id={self.id} talent_id={self.talent_id} v{self.version}>"
