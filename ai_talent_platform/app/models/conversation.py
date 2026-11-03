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
待实现清单：
- [ ] 模型字段与索引（conversation_id + created_at）
- [ ] ConversationRepository：get_recent_messages、append_message、list_by_user
- [ ] ai/memory/conversation_memory.py 基于此表实现记忆读写
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 Conversation / ChatMessage 模型，并在 app/models/__init__.py 注册。
