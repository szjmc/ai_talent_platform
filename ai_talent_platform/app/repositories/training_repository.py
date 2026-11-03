"""
模块：app/repositories/training_repository.py
职责：培训数据访问层。

标签：[业务组] [P1]（完成率聚合）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 完成率、学时统计都用聚合查询完成；
- 学习记录需要记录 last_study_at，用于「学习滞后预警」；
- 反作弊规则（进度与学时比例）在 Service 实现，仓库只存数据。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] list_courses(db, filters)                     课程列表
    - [ ] get_plan_with_courses(db, plan_id)             计划 + 课程 + 学习进度
    - [ ] completion_rate(db, plan_id)                   计划完成率（聚合）
    - [ ] list_progress_by_talent(db, talent_id)         个人学习进度
    - [ ] upsert_learning_record(db, ...)                学习记录写回（存在则更新）

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
# from app.models.training import TrainingPlan

# TODO(负责人)：按上述方法清单实现。


class TrainingRepository(BaseRepository):
    """培训仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
