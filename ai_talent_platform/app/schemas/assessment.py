"""
模块：app/schemas/assessment.py
职责：人才测评相关请求 / 响应结构。

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. AssessmentCreate       title、talent_id、position_id(可空)、question_count、deadline
2. QuestionResponse       id、type(单选/多选/简答)、content、options、score
3. AssessmentStartResponse assessment_id、questions: list[QuestionResponse]、expire_at
4. AnswerSubmit            answers: list[{question_id, answer}]、submit_at
5. AssessmentResult       total_score、dimension_scores: dict[str, float]、
                          level(初级/中级/高级/专家)、suggestions、ai_report
6. AssessmentQuery        talent_id、status、date_range
实现要点：
- 答题接口需校验测评状态（进行中）与超时；
- 评分规则在 Service（客观题按答案、主观题交 AI 评分），Router 不计算分数；
- 结果中的雷达图数据由 dimension_scores 提供，前端直接渲染。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
