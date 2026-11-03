"""
模块：app/models/resume.py
职责：简历文件表 + 简历解析结果表。

============================ 实现思路与代码要点 ============================
表设计：
1. resumes
   - talent_id(可空，解析后回填)、file_id(->files)、file_name、file_type、file_size、
     file_hash(去重)、status(uploaded/parsing/success/failed)、
     parsed_data(JSON，结构化简历)、parse_error、parsed_at、accuracy
2. resume_experiences（拆表，便于「某公司背景人才」统计）
   - resume_id、company、title、start_date、end_date、description
关键思路：
- file_hash（sha256）用于秒传与重复简历识别；
- parsed_data 的 JSON 结构与 schemas/resume.py 中 ResumeParseResult.parsed 一致；
- 解析失败保留 parse_error，便于人工复核与 Prompt 迭代。
已完成：
- [x] 模型字段与索引（talent_id、status、file_hash 唯一）
待实现清单：
- [ ] ResumeRepository：get_by_hash、list_by_talent、update_parse_result
- [ ] ResumeService：上传 -> 存文件 -> 创建 resume 记录 -> 触发 AI 工作流
=========================================================================
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Resume(BaseModel):
    """简历（上传文件 + AI 解析结果）。"""

    __tablename__ = "resumes"
    __table_args__ = {"comment": "简历表"}

    talent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="人才 ID（FK -> talents.id，SET NULL，解析后回填）",
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
        comment="文件 ID（FK -> files.id，SET NULL）",
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    file_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="文件类型：pdf/docx/doc/xlsx/txt/png/jpg"
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="字节数"
    )
    file_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="sha256（秒传/查重，唯一）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="uploaded",
        server_default=text("'uploaded'"),
        index=True,
        comment="状态：uploaded/parsing/success/failed",
    )
    accuracy: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True, comment="解析置信度 0~1"
    )
    parsed_data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="结构化简历：basic_info/education/work_experience/projects/skills",
    )
    parse_error: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="解析失败原因")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="解析完成时间")

    # ------------------------------------------------------------------ 关系
    talent: Mapped["Talent | None"] = relationship(  # noqa: F821
        "Talent", back_populates="resumes", foreign_keys=[talent_id], lazy="selectin"
    )
    file: Mapped["File | None"] = relationship(  # noqa: F821
        "File", back_populates="resumes", foreign_keys=[file_id], lazy="selectin"
    )
    experiences: Mapped[list["ResumeExperience"]] = relationship(
        "ResumeExperience", back_populates="resume", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Resume id={self.id} file_name={self.file_name} status={self.status}>"


class ResumeExperience(BaseModel):
    """简历工作经历（拆表，便于统计）。"""

    __tablename__ = "resume_experiences"
    __table_args__ = {"comment": "简历工作经历表"}

    resume_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="简历 ID（FK -> resumes.id，CASCADE）",
    )
    company: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="公司名称")
    title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="职位")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="开始日期")
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="结束日期（空表示至今）")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工作内容")
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="展示顺序"
    )

    resume: Mapped["Resume"] = relationship("Resume", back_populates="experiences")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ResumeExperience id={self.id} company={self.company}>"
