"""
模块：app/schemas/training.py
职责：培训管理相关请求 / 响应结构。

标签：[Dify组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. CourseCreate / CourseUpdate   title、category、cover、description、duration_hours、
                                 resource_url、level、skill_tags
2. TrainingPlanCreate            name、talent_ids、course_ids、start_date、end_date、
                                 goal、created_by_ai(bool)
3. TrainingPlanResponse          计划信息 + progress(完成率) + items
4. LearningRecordCreate           plan_id、course_id、talent_id、progress、study_minutes
5. TrainingRecommendRequest      talent_id、top_k
6. TrainingRecommendResult       recommendations: list[{course, reason, gap_dimension}]
实现要点：
- AI 推荐的输入是「能力缺口」(来自测评结果) + 岗位模型，输出课程列表 + 推荐理由；
- 学习记录需做反作弊校验（学习时长与进度是否匹配），规则写在 Service。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
