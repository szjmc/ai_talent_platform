"""
模块：app/repositories/audit_repository.py
职责：审计日志数据访问层。

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 只增不改：不实现 update/delete 方法；
- 写审计失败不能影响主业务（Service 中 try/except 吞掉异常并记日志）；
- 敏感字段入库前脱敏。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] create_log(db, log)                           写审计（唯一写入口）
    - [ ] list_with_filters(db, page_params, filters)    审计列表（按用户/模块/动作）
    - [ ] count_by_action(db, date_range)                动作分布统计
    - [ ] get_by_trace_id(db, trace_id)                  按链路 ID 追踪一次请求

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
# from app.models.audit import AuditLog

# TODO(负责人)：按上述方法清单实现。


class AuditRepository(BaseRepository):
    """审计日志仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
