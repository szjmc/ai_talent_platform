"""
模块：app/repositories/matching_repository.py
职责：人岗匹配数据访问层。

标签：[Agent组] [P0]（save_batch / get_latest_by_position / get_history_by_talent）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 用 batch_id 归组一次跑批结果，唯一索引 (batch_id, position_id, talent_id)；
- 查询最新批次：先取该岗位最大 matched_at 的 batch_id，再按 batch 取明细；
- 匹配记录可重算，允许定期清理历史数据。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] save_batch(db, records)                       批量保存一批匹配结果
    - [ ] get_latest_by_position(db, position_id, limit) 某岗位最新匹配批次
    - [ ] get_history_by_talent(db, talent_id)           某人才历史匹配记录
    - [ ] top_matches(db, position_id, min_score)        高分候选人排序
    - [ ] count_by_score_range(db, position_id)          分数分布统计

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
# from app.models.matching import MatchingRecord

# TODO(负责人)：按上述方法清单实现。


class MatchingRepository(BaseRepository):
    """人岗匹配仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
