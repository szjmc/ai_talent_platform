"""
模块：app/repositories/talent_repository.py
职责：人才档案数据访问层。

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 向量检索流程：Milvus 召回 talent_id 列表 -> 本仓库 get_by_ids 回表 -> 保持得分排序；
- 统计类方法返回 dict 列表（键名与 schemas/analytics.py 对应）；
- 敏感字段（手机号/身份证）在 Schema 层脱敏，不在仓库层处理。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_by_mobile(db, mobile)                      手机号查重
    - [ ] get_by_ids(db, ids)                            批量查询（向量检索回表）
    - [ ] search_by_skills(db, skills)                   技能包含筛选（JSON）
    - [ ] count_by_organization(db, org_ids)             部门人才分布统计
    - [ ] stats_by_education(db)                         学历分布（分组统计）
    - [ ] stats_by_month(db, year)                       月度新增趋势

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
# from app.models.talent import Talent

# TODO(负责人)：按上述方法清单实现。


class TalentRepository(BaseRepository):
    """人才档案仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
