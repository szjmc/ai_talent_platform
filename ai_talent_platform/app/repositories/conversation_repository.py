"""
模块：app/repositories/conversation_repository.py
职责：AI 会话数据访问层。

标签：[Agent组] [P1]（记忆持久化：get_recent_messages / append_message / list_by_user）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 短期记忆走 Redis（最近 10 轮），长期落库；
- 消息中的 citations 必须持久化，用于答案溯源；
- Token 统计用于配额与成本分析，建议按天聚合。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_recent_messages(db, conversation_id, limit) 最近 N 轮消息（短期记忆）
    - [ ] append_message(db, message)                     追加消息
    - [ ] list_by_user(db, user_id, page)                 会话列表
    - [ ] update_message_count(db, conversation_id)       会话统计
    - [ ] sum_tokens_by_user(db, user_id, days)           Token 消耗统计（成本）

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
=========================================================================
"""

from __future__ import annotations

from app.models.base import Base
from app.repositories.base import BaseRepository

# 模型实现后取消注释：
# from app.models.conversation import Conversation

# TODO(负责人)：按上述方法清单实现。


class ConversationRepository(BaseRepository):
    """AI 会话仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
