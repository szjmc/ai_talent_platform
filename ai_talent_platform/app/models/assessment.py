"""
模块：app/models/assessment.py
职责：测评任务、测评题目、答题记录、测评结果。

标签：[业务组] [P1]（测评表：题库 / 试卷 / 答题 / 结果）

============================ 实现思路与代码要点 ============================
表设计：
1. assessments        测评任务：talent_id、position_id、title、status、deadline、
                      question_count、started_at、submitted_at、created_by_ai
2. assessment_questions 题目：assessment_id、type、content、options(JSON)、
                      answer(标准答案，JSON)、score、dimension(考察维度)
3. assessment_answers 答题：assessment_id、question_id、answer(JSON)、
                      score、scored_by(rule/ai)、scored_at
4. assessment_results 结果：assessment_id、total_score、dimension_scores(JSON)、
                      level、report(JSON AI 报告)、suggestions
关键思路：
- 一个测评 = 一套题（千人千卷：按岗位/层级抽题生成）；
- 状态机：pending -> in_progress -> submitted -> scored；
- 客观题规则评分，主观题交 AI 评分（记录 model_name 与 prompt_version 便于复现）。
已完成：
- [x] 四个模型及外键级联
待实现清单：
- [ ] AssessmentRepository：get_active_by_talent、save_answers、save_result
- [ ] AssessmentService：抽题、超时处理、评分、AI 报告生成
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class Assessment(BaseModel):
    """测评任务。"""

    __tablename__ = "assessments"
    __table_args__ = (
        Index("ix_assessments_talent_id_status", "talent_id", "status"),
        {"comment": "测评任务表"},
    )

    talent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="CASCADE"),
        nullable=False,
        comment="人才 ID（FK -> talents.id，CASCADE）",
    )
    position_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("positions.id", ondelete="SET NULL"),
        nullable=True,
        comment="岗位 ID（FK -> positions.id，SET NULL，可空）",
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="测评标题")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        comment="状态：pending/in_progress/submitted/scored",
    )
    question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="题目数量"
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="截止时间"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始时间")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="提交时间")
    total_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True, comment="总分（提交后回填）"
    )
    created_by_ai: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
        comment="是否 AI 生成（千人千卷）",
    )

    # ------------------------------------------------------------------ 关系
    talent: Mapped["Talent"] = relationship("Talent", lazy="selectin")  # noqa: F821
    position: Mapped["Position | None"] = relationship("Position", lazy="selectin")  # noqa: F821
    questions: Mapped[list["AssessmentQuestion"]] = relationship(
        "AssessmentQuestion", back_populates="assessment", cascade="all, delete-orphan", lazy="select"
    )
    answers: Mapped[list["AssessmentAnswer"]] = relationship(
        "AssessmentAnswer", back_populates="assessment", cascade="all, delete-orphan", lazy="select"
    )
    result: Mapped["AssessmentResult | None"] = relationship(
        "AssessmentResult",
        back_populates="assessment",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Assessment id={self.id} title={self.title} status={self.status}>"


class AssessmentQuestion(BaseModel):
    """测评题目。"""

    __tablename__ = "assessment_questions"
    __table_args__ = (
        Index("ix_assessment_questions_assessment_sort", "assessment_id", "sort"),
        {"comment": "测评题目表"},
    )

    assessment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        comment="测评 ID（FK -> assessments.id，CASCADE）",
    )
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="single", server_default=text("'single'"),
        comment="题型：single/multiple/text",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="题干")
    options: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="选项 [{key,text}]（客观题）"
    )
    answer: Mapped[dict | list | str | None] = mapped_column(
        JSON, nullable=True, comment="标准答案（绝不返回给前端）"
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default=text("0"), comment="分值"
    )
    dimension: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="考察维度（雷达图）")
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="展示顺序"
    )

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="questions")
    answers: Mapped[list["AssessmentAnswer"]] = relationship(
        "AssessmentAnswer", back_populates="question", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssessmentQuestion id={self.id} type={self.type}>"


class AssessmentAnswer(BaseModel):
    """答题记录。"""

    __tablename__ = "assessment_answers"
    __table_args__ = (
        UniqueConstraint("assessment_id", "question_id", name="uq_assessment_answers_assessment_question"),
        {"comment": "测评答题记录表"},
    )

    assessment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        comment="测评 ID（FK -> assessments.id，CASCADE）",
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("assessment_questions.id", ondelete="CASCADE"),
        nullable=False,
        comment="题目 ID（FK -> assessment_questions.id，CASCADE）",
    )
    answer: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True, comment="用户答案")
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True, comment="本题得分")
    scored_by: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="评分方式：rule/ai"
    )
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="评分时间")

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="answers")
    question: Mapped["AssessmentQuestion"] = relationship(
        "AssessmentQuestion", back_populates="answers", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssessmentAnswer id={self.id} question_id={self.question_id}>"


class AssessmentResult(BaseModel):
    """测评结果（1:1 关联测评任务）。"""

    __tablename__ = "assessment_results"
    __table_args__ = {"comment": "测评结果表"}

    assessment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="测评 ID（唯一，FK -> assessments.id，CASCADE）",
    )
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True, comment="总分")
    dimension_scores: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="维度分（雷达图数据源）"
    )
    level: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True, comment="等级：初级/中级/高级/专家"
    )
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="AI 研判报告")
    suggestions: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="提升建议")
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="模型（可追溯）")
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="提示词版本")

    assessment: Mapped["Assessment"] = relationship("Assessment", back_populates="result")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssessmentResult id={self.id} assessment_id={self.assessment_id}>"
