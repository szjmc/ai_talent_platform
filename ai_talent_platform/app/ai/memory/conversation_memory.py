"""
模块：app/ai/memory/conversation_memory.py
职责：多轮对话记忆（短期 Redis + 长期 MySQL）。

标签：[架构组] [P1]（被 Agent 组会话链路依赖）

============================ 实现思路与代码要点 ============================
1. 短期记忆：最近 N 轮（默认 10）消息放 Redis（key: chat:mem:{conversation_id}，TTL 1h）；
2. 长期记忆：全部消息落 chat_messages 表（ConversationRepository）；
3. 加载策略：先读 Redis，miss 则从 DB 取最近 N 轮并回填 Redis；
4. 摘要压缩：超过 N 轮时把更早的消息压缩成 summary 存 Redis，控制上下文长度；
5. 注意：对话记忆 ≠ 人才档案，长期业务数据必须落业务库。

降级：Redis 未启用 / 模块异常时退化为进程内字典，保证本地与单测可跑。
=========================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.adapters.llm.base_llm import ChatMessage
from app.config.settings import settings

logger = logging.getLogger(__name__)

# 进程内兜底存储：{key: [message dict, ...]}
_MEMORY_STORE: dict[str, list[dict[str, Any]]] = {}
_SUMMARY_STORE: dict[str, str] = {}


@dataclass
class MemoryMessage:
    """一条对话记忆（与 ChatMessage 互转）。"""

    role: str
    content: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }

    def to_chat_message(self) -> ChatMessage:
        return ChatMessage(role=self.role, content=self.content)

    @classmethod
    def from_any(cls, item: Any) -> "MemoryMessage":
        if isinstance(item, MemoryMessage):
            return item
        if isinstance(item, dict):
            return cls(
                role=str(item.get("role", "user")),
                content=str(item.get("content", "")),
                created_at=str(item.get("created_at") or datetime.now().isoformat(timespec="seconds")),
                meta=dict(item.get("meta") or {}),
            )
        return cls(role=str(getattr(item, "role", "user")), content=str(getattr(item, "content", "")))


class ConversationMemory:
    """单会话的记忆读写（短期 Redis + 可选长期落库）。"""

    def __init__(
        self,
        conversation_id: str | int,
        *,
        window: int = 10,
        ttl: int = 3600,
        summary_ttl: int = 3600,
    ) -> None:
        self.conversation_id = str(conversation_id)
        self.window = max(2, window)
        self.ttl = ttl
        self.summary_ttl = summary_ttl

    # ---------------------------------------------------------------- key
    @property
    def max_raw(self) -> int:
        """短期记忆的保留上限（需大于 window，给摘要压缩留出素材）。"""
        return max(self.window * 4, 50)

    @property
    def mem_key(self) -> str:
        return f"chat:mem:{self.conversation_id}"

    @property
    def summary_key(self) -> str:
        return f"chat:summary:{self.conversation_id}"

    # ---------------------------------------------------------------- 写入
    async def append(self, message: Any, *, db: Any | None = None) -> None:
        """追加一条消息（同时尝试落库）。"""
        item = MemoryMessage.from_any(message)
        messages = await self._read_raw()
        messages.append(item.to_dict())
        await self._write_raw(messages[-self.max_raw :])
        if db is not None:
            await self._persist(db, item)

    async def extend(self, messages: list[Any], *, db: Any | None = None) -> None:
        for message in messages or []:
            await self.append(message, db=db)

    # ---------------------------------------------------------------- 读取
    async def get_recent(self, limit: int | None = None, *, db: Any | None = None) -> list[dict[str, Any]]:
        """取最近 N 条消息；Redis 未命中时尝试从 DB 回填。"""
        limit = limit or self.window
        messages = await self._read_raw()
        if not messages and db is not None:
            messages = await self._load_from_db(db, limit)
            if messages:
                await self._write_raw(messages)
        return messages[-limit:]

    async def get_context_messages(self, limit: int | None = None, *, db: Any | None = None) -> list[ChatMessage]:
        """组装给模型的上下文：历史摘要（system）+ 最近 N 轮消息。"""
        result: list[ChatMessage] = []
        summary = await self.get_summary()
        if summary:
            result.append(ChatMessage.system(f"以下是更早对话内容的摘要，可用于理解上下文：\n{summary}"))
        for item in await self.get_recent(limit=limit, db=db):
            result.append(MemoryMessage.from_any(item).to_chat_message())
        return result

    async def get_summary(self) -> str | None:
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import get_json

                value = await get_json(self.summary_key)
                if value:
                    return str(value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取会话摘要失败：%s", exc)
        return _SUMMARY_STORE.get(self.summary_key)

    # ---------------------------------------------------------------- 摘要
    async def summarize_if_needed(self, *, threshold: int | None = None, use_llm: bool = True) -> str | None:
        """消息超过阈值时，把更早的内容压缩成摘要（LLM 不可用时截断拼接）。"""
        threshold = threshold or self.window * 2
        messages = await self._read_raw()
        if len(messages) <= threshold:
            return await self.get_summary()

        keep = messages[-self.window :]
        older = messages[: len(messages) - self.window]
        summary = await self._summarize(older, use_llm=use_llm)
        await self.set_summary(summary)
        await self._write_raw(keep)
        logger.info("会话 %s 已压缩 %s 条历史消息", self.conversation_id, len(older))
        return summary

    async def set_summary(self, summary: str) -> None:
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import set_json

                await set_json(self.summary_key, summary, self.summary_ttl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入会话摘要失败：%s", exc)
        _SUMMARY_STORE[self.summary_key] = summary

    # ---------------------------------------------------------------- 清理
    async def clear(self) -> None:
        """清空短期记忆与摘要（长期消息仍在 DB）。"""
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import delete_keys

                await delete_keys(f"chat:*:{self.conversation_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("清理会话缓存失败：%s", exc)
        _MEMORY_STORE.pop(self.mem_key, None)
        _SUMMARY_STORE.pop(self.summary_key, None)

    # ---------------------------------------------------------------- 内部
    async def _read_raw(self) -> list[dict[str, Any]]:
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import get_json

                value = await get_json(self.mem_key)
                if value is not None:
                    return list(value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取会话记忆失败，退化进程内：%s", exc)
        return list(_MEMORY_STORE.get(self.mem_key, []))

    async def _write_raw(self, messages: list[dict[str, Any]]) -> None:
        _MEMORY_STORE[self.mem_key] = list(messages)
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import set_json

                await set_json(self.mem_key, messages, self.ttl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入会话记忆失败：%s", exc)

    async def _summarize(self, messages: list[dict[str, Any]], *, use_llm: bool) -> str:
        text = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in messages)
        if use_llm:
            try:
                from app.ai.core.llm_manager import llm_manager

                result = await llm_manager.chat(
                    [
                        ChatMessage.system("你是对话摘要助手，用不超过 200 字概括对话要点，中文输出。"),
                        ChatMessage.user(text[:6000]),
                    ],
                    purpose="summary",
                )
                if result.content:
                    return result.content.strip()
            except Exception as exc:  # noqa: BLE001 - LLM 不可用时走截断兜底
                logger.warning("LLM 摘要失败，改用截断拼接：%s", exc)
        return f"（历史对话摘要，共 {len(messages)} 条）{text[:800]}"

    @staticmethod
    async def _persist(db: Any, message: MemoryMessage) -> None:
        """长期记忆落库（Agent 组实现 ConversationRepository 后自动生效）。"""
        try:
            from app.repositories.conversation_repository import ConversationRepository

            repo = ConversationRepository()
            create = getattr(repo, "append_message", None) or getattr(repo, "create", None)
            if create is None:
                return
            payload = {
                "conversation_id": None,
                "role": message.role,
                "content": message.content,
                **message.meta,
            }
            result = create(db, obj_in=payload) if "obj_in" in create.__code__.co_varnames else create(db, payload)
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # noqa: BLE001 - 落库失败不影响对话进行
            logger.debug("会话消息落库跳过：%s", exc)

    @staticmethod
    async def _load_from_db(db: Any, limit: int) -> list[dict[str, Any]]:
        try:
            from app.repositories.conversation_repository import ConversationRepository

            repo = ConversationRepository()
            method = getattr(repo, "get_recent_messages", None)
            if method is None:
                return []
            result = method(db, limit=limit)
            if hasattr(result, "__await__"):
                result = await result
            return [MemoryMessage.from_any(item).to_dict() for item in (result or [])]
        except Exception as exc:  # noqa: BLE001
            logger.debug("从 DB 回填会话记忆跳过：%s", exc)
            return []


__all__ = ["ConversationMemory", "MemoryMessage"]
