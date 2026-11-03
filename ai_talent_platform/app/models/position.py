"""
模块：app/models/position.py
职责：岗位表 + 岗位能力要求。

============================ 实现思路与代码要点 ============================
表设计：
1. positions
   - title、code(唯一)、organization_id、level(初/中/高/专家)、
     headcount(编制)、status(open/closed/paused)、
     description(岗位说明书全文)、requirements(JSON 结构化要求)、
     skill_tags(JSON)、min_education、min_work_years、
     salary_range_min/max、publish_at、close_at
2. position_requirements（可选拆表，用于多版本能力模型）
   - position_id、version、dimensions(JSON)、weights(JSON 维度权重)
关键思路：
- requirements 结构化后才能做「硬性条件过滤 + 软性加权」匹配；
- 岗位向量（job embedding）在岗位创建/更新时写入 Milvus，需在 Service 中触发；
- 岗位关闭后不再参与匹配，但历史匹配记录保留。
待实现清单：
- [ ] 模型字段与索引（organization_id + status 联合索引）
- [ ] PositionRepository：list_open_by_org、get_requirements、search_by_title
- [ ] PositionService：创建/更新时触发岗位向量化
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 Position 模型，并在 app/models/__init__.py 注册。
