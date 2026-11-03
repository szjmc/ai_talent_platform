"""
模块：app/models/training.py
职责：课程、培训计划、学习记录。

============================ 实现思路与代码要点 ============================
表设计：
1. courses         课程：title、category、level、duration_hours、resource_url、
                   cover、description、skill_tags(JSON)、is_active
2. training_plans  培训计划：name、talent_id、goal、start_date、end_date、
                   status、created_by_ai、source_assessment_id
3. plan_courses    计划-课程关联：plan_id、course_id、sort、require_pass
4. learning_records 学习记录：plan_id、course_id、talent_id、progress(0-100)、
                   study_minutes、last_study_at、status、score、passed
关键思路：
- 「测评-诊断-培训-提升-复测」闭环：培训计划由能力缺口（测评结果）驱动生成；
- 学习记录做反作弊：study_minutes 与 progress 比例异常时标记 suspicious；
- 完成率统计在 Repository 用聚合查询实现（勿在 Python 里 for 循环算）。
待实现清单：
- [ ] 模型字段与索引（talent_id + status）
- [ ] TrainingRepository：completion_rate、list_progress_by_talent
- [ ] TrainingService：AI 生成计划、进度更新、滞后预警
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现培训相关模型，并在 app/models/__init__.py 注册。
