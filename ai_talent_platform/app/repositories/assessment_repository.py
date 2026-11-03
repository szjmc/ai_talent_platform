"""
模块：app/repositories/assessment_repository.py
职责：测评数据访问层。

标签：[业务组] [P1]（答题 / 结果聚合）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 状态机 pending -> in_progress -> submitted -> scored，非法流转在 Service 拦截；
- 答题保存用 upsert 语义（同一题重复提交覆盖）；
- 结果表与测评表 1:1，用 assessment_id 唯一约束。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_active_by_talent(db, talent_id)           进行中的测评
    - [ ] list_questions(db, assessment_id)              题目列表
    - [ ] save_answers(db, answers)                      批量保存答题
    - [ ] get_result(db, assessment_id)                  测评结果
    - [ ] stats_avg_score(db, position_id)               平均分统计

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
# from app.models.assessment import Assessment

# TODO(负责人)：按上述方法清单实现。


class AssessmentRepository(BaseRepository):
    """测评仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
