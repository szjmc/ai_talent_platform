"""
模块：app/models/conversation.py
职责：AI 会话与消息表（多轮对话记忆的持久化）。

============================ 实现思路与代码要点 ============================
表设计：
1. conversations  会话：user_id、title、agent_type(assistant/resume/matching...)、
                  status、message_count、last_message_at
2. chat_messages  消息：conversation_id、role(user/assistant/system/tool)、
                  content、tool_calls(JSON)、citations(JSON)、
                  prompt_tokens、completion_tokens、model_name、elapsed_ms
关键思路：
- 短期记忆（最近 N 轮）走 Redis，长期历史落 MySQL；
- 消息表存 token 统计，便于成本分析与限流；
- RAG 回答的 citations 与消息一起存储，保证可溯源。
已完成：
- [x] 模型字段与索引（conversation_id + created_at）
待实现清单：
- [ ] ConversationRepository：get_recent_messages、append_message、list_by_user
- [ ] ai/memory/conversation_memory.py 基于此表实现记忆读写
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Conversation(BaseModel):
    """AI 会话。"""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id_last_message_at", "user_id", "last_message_at"),
        {"comment": "AI 会话表"},
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="用户 ID（FK -> users.id，CASCADE）",
    )
    title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="会话标题")
    agent_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="assistant",
        server_default=text("'assistant'"),
        comment="Agent 类型：assistant/resume/talent/matching/assessment/training",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        comment="状态：active/archived",
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="消息数"
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最后消息时间"
    )

    # ------------------------------------------------------------------ 关系
    user: Mapped["User"] = relationship("User", lazy="selectin")  # noqa: F821
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="conversation", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation id={self.id} agent_type={self.agent_type}>"


class ChatMessage(BaseModel):
    """会话消息（含 token 统计与 RAG 引用）。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),
        {"comment": "会话消息表"},
    )

    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="会话 ID（FK -> conversations.id，CASCADE）",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default=text("'user'"),
        comment="角色：user/assistant/system/tool",
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="消息内容")
    tool_calls: Mapped[dict | list | None] = mapped_column(JSON, nullable=True, comment="工具调用记录")
    citations: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="RAG 引用：doc_id/chunk/page/score"
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="提示 token 数"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="生成 token 数"
    )
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="模型")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时（毫秒）")

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatMessage id={self.id} role={self.role}>"
