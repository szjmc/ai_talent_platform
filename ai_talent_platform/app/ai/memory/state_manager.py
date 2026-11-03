"""
模块：app/ai/memory/state_manager.py
职责：LangGraph 工作流状态持久化。

============================ 实现思路与代码要点 ============================
1. 目的：工作流（简历解析、匹配跑批）可能跨请求/跨进程，需要断点续跑；
2. 存储：Redis（短期，TTL 24h）或 MySQL 表 workflow_states（长期）；
3. API：save(thread_id, state) / load(thread_id) / delete(thread_id)；
4. 与 LangGraph checkpointer 对接，保持一致的状态结构。
待实现清单：
    - [ ] save / load / delete
    - [ ] 序列化（JSON，datetime 转 isoformat）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
