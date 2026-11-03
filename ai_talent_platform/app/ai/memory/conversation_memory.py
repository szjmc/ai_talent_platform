"""
模块：app/ai/memory/conversation_memory.py
职责：多轮对话记忆（短期 Redis + 长期 MySQL）。

============================ 实现思路与代码要点 ============================
1. 短期记忆：最近 N 轮（默认 10）消息放 Redis（key: chat:mem:{conversation_id}，TTL 1h）；
2. 长期记忆：全部消息落 chat_messages 表（ConversationRepository）；
3. 加载策略：先读 Redis，miss 则从 DB 取最近 N 轮并回填 Redis；
4. 摘要压缩：超过 N 轮时把更早的消息压缩成 summary 存 Redis，控制上下文长度；
5. 注意：对话记忆 ≠ 人才档案，长期业务数据必须落业务库。
待实现清单：
    - [ ] append(conversation_id, message)
    - [ ] get_recent(conversation_id, limit=10) -> list[ChatMessage]
    - [ ] clear(conversation_id)
    - [ ] summarize_if_needed(...)
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
