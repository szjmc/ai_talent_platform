"""
模块：app/models/matching.py
职责：人岗匹配记录表。

============================ 实现思路与代码要点 ============================
表设计：matching_records
- position_id、talent_id、score(0-100)、dimension_scores(JSON)、
  highlights(JSON)、gaps(JSON)、suggestion、strategy(hard/soft/hybrid)、
  model_name、matched_by(用户 ID)、matched_at
关键思路：
- 每次匹配跑批生成一批记录，用 batch_id(uuid) 归组，便于前端按批次展示；
- 唯一索引 (batch_id, position_id, talent_id) 防重复写入；
- 匹配记录是「可重算数据」，量大会按月分区（PgSQL 更合适）。
待实现清单：
- [ ] 模型字段与索引
- [ ] MatchingRepository：get_latest_by_position、get_history_by_talent、save_batch
- [ ] MatchingService：硬性过滤 + 向量召回 + 加权打分 + AI 解释
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 MatchingRecord 模型，并在 app/models/__init__.py 注册。
