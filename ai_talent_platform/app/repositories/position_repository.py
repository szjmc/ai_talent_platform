"""
模块：app/repositories/position_repository.py
职责：岗位数据访问层。

标签：[Agent组] [P0]（list_open_by_org / search_by_title，被 position_search 技能依赖）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- requirements 是 JSON 列，MySQL 8 下可用 func.json_contains 查询；
- 开发/测试环境统一 MySQL 8，json_contains 可用；单测中建议用真实 MySQL 测试库而非内存库；
- 岗位创建/更新后需触发向量化（在 Service 层做，不放这里）。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] list_open_by_org(db, org_id)                   部门在招岗位
    - [ ] search_by_title(db, keyword)                   标题模糊搜索
    - [ ] get_requirements(db, position_id)              岗位结构化要求
    - [ ] count_open_by_org(db, org_ids)                 批量统计在招岗位数
    - [ ] list_by_skill(db, skill)                       按技能筛选岗位（JSON 字段）

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
# from app.models.position import Position

# TODO(负责人)：按上述方法清单实现。


class PositionRepository(BaseRepository):
    """岗位仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
