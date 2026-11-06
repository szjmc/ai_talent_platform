# 题库表记录结构化题目。向量索引与数据库记录通过题目标识关联。
"""独立可复用题库；不改变现有测评表。
标签：[RAG组] [P1]。
"""
from typing import Any

from sqlalchemy import JSON, BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class QuestionBankQuestion(BaseModel):
    __tablename__ = 'question_bank_questions'
    __table_args__ = (
        UniqueConstraint('bank_id', 'external_id', name='uq_question_bank_external'),
        Index('ix_question_bank_selection', 'technology', 'difficulty', 'question_type', 'status'),
        {'comment': '独立笔试题库，向量通过题目ID回表'},
    )

    bank_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment='业务题库ID')
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, comment='来源题号，同题库内唯一')
    technology: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    question_type: Mapped[str] = mapped_column(String(16), nullable=False)
    knowledge_points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[Any] = mapped_column(JSON, nullable=False)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='draft', server_default=text("'draft'"))
    permission_scope: Mapped[str] = mapped_column(String(16), nullable=False, default='private', server_default=text("'private'"))
    organization_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text('1'))
    indexed_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text('0'))
    index_status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending', server_default=text("'pending'"))
    index_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
