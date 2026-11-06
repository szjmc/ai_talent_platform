"""
模块：app/repositories/conversation_repository.py
职责：会话与消息数据访问（create_conversation / append_message / 分页 / 聚合）。

标签：[Agent组] [P1]

设计要点（与 app/ai/memory/conversation_memory.py 的回调契约严格对齐）：
1. append_message(db, obj_in=payload) —— ConversationMemory._persist 会按
   "obj_in" in co_varnames 选择关键字调用，签名必须保留 obj_in；
   payload = {conversation_id, role, content, **message.meta}，meta 里的
   citations/prompt_tokens/... 映射到 ChatMessage 列；
2. AI 上下文带 suppress_conversation_persist 标记时跳过落库
   （AgentService 统一落库，避免 BaseAgent 内部记忆双写）；
3. conversation_id 无法解析（无会话上下文）时返回 None 并记 debug 日志，
   不在持久化回调里自作主张建会话；
4. get_recent_messages(db, limit=N) 返回按时间升序的 dict 列表
   （ConversationMemory 先取最近 N 条再截尾，须为正序）；
5. 越权防护：get_by_session_key_or_id / paginate_messages 带 user_id 时
   校验 Conversation.user_id，不一致抛 PermissionDeniedError(2004)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams, build_page_result
from app.models.conversation import ChatMessage, Conversation
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

__all__ = ["ConversationRepository"]

# 消息 meta → ChatMessage 列 的映射白名单
_MESSAGE_META_COLUMNS = {
    "citations": "citations",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "model_name": "model_name",
    "model": "model_name",
    "elapsed_ms": "elapsed_ms",
    "tool_calls": "tool_calls",
}


class ConversationRepository(BaseRepository[Conversation]):
    """会话 / 消息数据访问层（只管读写，业务判断在 Service）。"""

    model = Conversation
    filterable_fields = {"user_id", "agent_type", "status"}
    searchable_fields = {"title"}
    sortable_fields = {"id", "created_at", "last_message_at"}
    default_order = "-last_message_at"

    # ---------------------------------------------------------------- 会话
    async def create_conversation(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        agent_type: str = "assistant",
        title: str | None = None,
    ) -> Conversation:
        """新建会话（首次对话由 Service 调用）。"""
        return await self.create(
            db,
            {
                "user_id": user_id,
                "agent_type": agent_type or "assistant",
                "title": (title or "")[:128],
                "status": "active",
                "message_count": 0,
            },
        )

    async def get_by_session_key_or_id(
        self,
        db: AsyncSession,
        key: Any,
        *,
        user_id: int | None = None,
    ) -> Conversation | None:
        """按会话 ID（int / 数字字符串）取会话；带 user_id 时强制归属校验。

        - 不存在 → None（Service 转 NotFoundError）
        - 存在但不属于 user_id → PermissionDeniedError(2004, 403) 防越权
        """
        if key is None or key == "":
            return None
        try:
            conv_id = int(str(key).strip())
        except (TypeError, ValueError):
            logger.debug("非法 conversation_id：%r", key)
            return None
        conversation = await self.get_by_id(db, conv_id)
        if conversation is None:
            return None
        if user_id is not None and conversation.user_id != int(user_id):
            raise PermissionDeniedError("无权访问他人会话")
        return conversation

    async def list_by_user(
        self,
        db: AsyncSession,
        user_id: int,
        page_params: PageParams,
        *,
        agent_type: str | None = None,
    ) -> Any:
        """当前用户的会话分页（默认按最近活跃倒序）。"""
        filters: dict[str, Any] = {"user_id": user_id}
        if agent_type:
            filters["agent_type"] = agent_type
        return await self.paginate(db, page_params, filters=filters)

    # ---------------------------------------------------------------- 消息
    async def append_message(
        self,
        db: AsyncSession | None,
        obj_in: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage | None:
        """写入一条消息（ConversationMemory._persist 回调入口）。

        conversation_id 取值优先级：payload > AI 上下文；
        上下文标记 suppress_conversation_persist 或无会话时跳过（返回 None）。
        """
        payload = dict(obj_in or {})
        payload.update(kwargs)

        conversation_id = payload.get("conversation_id")
        context = None
        try:
            from app.ai.context import get_context

            context = get_context()
        except Exception:  # noqa: BLE001 - 无上下文（脚本/单测）按无会话处理
            context = None
        if context is not None:
            if (context.extra or {}).get("suppress_conversation_persist"):
                return None  # AgentService 统一落库，跳过 BaseAgent 内部记忆写入
            if not conversation_id:
                conversation_id = context.conversation_id
        if not conversation_id or db is None:
            logger.debug("append_message 跳过（conversation_id=%s, db=%s）", conversation_id, bool(db))
            return None

        try:
            conversation_id = int(str(conversation_id).strip())
        except (TypeError, ValueError):
            logger.debug("append_message 跳过（非法 conversation_id=%r）", conversation_id)
            return None

        fields: dict[str, Any] = {
            "conversation_id": conversation_id,
            "role": str(payload.get("role") or "user"),
            "content": str(payload.get("content") or ""),
        }
        for meta_key, column in _MESSAGE_META_COLUMNS.items():
            if meta_key in payload:
                fields[column] = payload[meta_key]

        message = ChatMessage(**fields)
        db.add(message)
        await db.flush()
        await self._bump_conversation(db, conversation_id)
        return message

    async def _bump_conversation(self, db: AsyncSession, conversation_id: int) -> None:
        """消息数 +1 与最近活跃时间（纯 ORM update，随请求事务提交）。"""
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.is_deleted.is_(False))
            .values(
                message_count=func.coalesce(Conversation.message_count, 0) + 1,
                last_message_at=datetime.now(),
            )
        )

    async def get_recent_messages(
        self,
        db: AsyncSession,
        limit: int = 10,
        conversation_id: Any = None,
    ) -> list[dict[str, Any]]:
        """最近 N 条消息（时间升序，供记忆回放；conversation_id 缺省取上下文）。"""
        if conversation_id is None:
            try:
                from app.ai.context import get_context

                conversation_id = get_context().conversation_id
            except Exception:  # noqa: BLE001
                conversation_id = None
        if not conversation_id or db is None:
            return []
        try:
            conversation_id = int(str(conversation_id).strip())
        except (TypeError, ValueError):
            return []

        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.is_deleted.is_(False),
            )
            .order_by(ChatMessage.id.desc())
            .limit(max(1, int(limit)))
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "meta": {
                    key: getattr(row, column)
                    for key, column in (("citations", "citations"),)
                    if getattr(row, column, None)
                },
            }
            for row in reversed(list(rows))
        ]

    async def paginate_messages(
        self,
        db: AsyncSession,
        conversation_id: Any,
        page_params: PageParams,
        *,
        user_id: int | None = None,
    ) -> Any:
        """会话消息分页（正序）；带 user_id 时校验会话归属防越权。"""
        conversation = await self.get_by_session_key_or_id(db, conversation_id, user_id=user_id)
        if conversation is None:
            raise NotFoundError("会话不存在")

        stmt = select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.is_deleted.is_(False),
        )
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        stmt = stmt.order_by(ChatMessage.id.asc()).offset(page_params.skip).limit(page_params.limit)
        items = list((await db.execute(stmt)).scalars().all())
        return build_page_result(items, int(total or 0), page_params)

    # ---------------------------------------------------------------- 兼容层
    # 合并后存在两套命名：架构组用 get_by_session_key_or_id / get_recent_messages /
    # append_message，RAG组与 Agent组的 MemoryService 用 owned / messages / append_turn。
    # 少了这三个方法，/text2answer 与 /conversations 每次请求都会 AttributeError。
    # 这里只做薄适配（复用上面的实现），不重复业务逻辑。
    async def owned(
        self,
        db: AsyncSession,
        conversation_id: Any,
        user_id: Any,
        lock: bool = False,
    ) -> Conversation | None:
        """按 ID 取会话并强制归属校验；查不到返回 None（不可访问不区分存在性）。"""
        try:
            cid, uid = int(str(conversation_id).strip()), int(user_id)
        except (TypeError, ValueError):
            return None
        stmt = select(Conversation).where(
            Conversation.id == cid,
            Conversation.user_id == uid,
            Conversation.is_deleted.is_(False),
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def messages(
        self,
        db: AsyncSession,
        conversation_id: Any,
        limit: int = 6,
        before_id: Any = None,
    ) -> list[ChatMessage]:
        """按会话取消息（时间正序，取最近 limit 条；before_id 用于向前翻页）。"""
        try:
            cid = int(str(conversation_id).strip())
        except (TypeError, ValueError):
            return []
        stmt = select(ChatMessage).where(
            ChatMessage.conversation_id == cid,
            ChatMessage.is_deleted.is_(False),
        )
        if before_id is not None:
            stmt = stmt.where(ChatMessage.id < int(before_id))
        stmt = stmt.order_by(ChatMessage.id.desc()).limit(max(1, int(limit)))
        rows = list((await db.execute(stmt)).scalars().all())
        return list(reversed(rows))

    async def append_turn(
        self,
        db: AsyncSession,
        conversation: Conversation,
        question: str,
        answer: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> ChatMessage | None:
        """一轮问答落库：先写 user 再写 assistant（引用挂在回答上）。"""
        await self.append_message(
            db,
            {"conversation_id": conversation.id, "role": "user", "content": question or ""},
        )
        return await self.append_message(
            db,
            {
                "conversation_id": conversation.id,
                "role": "assistant",
                "content": answer or "",
                "citations": citations or None,
            },
        )

    # ---------------------------------------------------------------- 聚合
    async def update_message_count(self, db: AsyncSession, conversation_id: int) -> int:
        """重算会话消息数（幂等），返回最新计数。"""
        count = (
            await db.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.is_deleted.is_(False))
            .values(message_count=int(count or 0))
        )
        return int(count or 0)

    async def sum_tokens_by_user(self, db: AsyncSession, user_id: int, days: int = 1) -> int:
        """近 N 天该用户的 token 消耗总量（配额展示用）。"""
        since = datetime.now() - timedelta(days=max(1, int(days)))
        stmt = (
            select(
                func.coalesce(func.sum(ChatMessage.prompt_tokens), 0)
                + func.coalesce(func.sum(ChatMessage.completion_tokens), 0)
            )
            .select_from(ChatMessage)
            .join(Conversation, ChatMessage.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Conversation.is_deleted.is_(False),
                ChatMessage.is_deleted.is_(False),
                ChatMessage.created_at >= since,
            )
        )
        return int((await db.execute(stmt)).scalar_one() or 0)
