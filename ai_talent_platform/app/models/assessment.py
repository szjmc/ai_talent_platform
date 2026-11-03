"""
模块：app/models/assessment.py
职责：测评任务、测评题目、答题记录、测评结果。

============================ 实现思路与代码要点 ============================
表设计：
1. assessments        测评任务：talent_id、position_id、title、status、deadline、
                      question_count、started_at、submitted_at、created_by_ai
2. assessment_questions 题目：assessment_id、type、content、options(JSON)、
                      answer(标准答案，JSON)、score、dimension(考察维度)
3. assessment_answers 答题：assessment_id、question_id、answer(JSON)、
                      score、scored_by(rule/ai)、scored_at
4. assessment_results 结果：assessment_id、total_score、dimension_scores(JSON)、
                      level、report(JSON AI 报告)、suggestions
关键思路：
- 一个测评 = 一套题（千人千卷：按岗位/层级抽题生成）；
- 状态机：pending -> in_progress -> submitted -> scored；
- 客观题规则评分，主观题交 AI 评分（记录 model_name 与 prompt_version 便于复现）。
待实现清单：
- [ ] 四个模型及外键级联
- [ ] AssessmentRepository：get_active_by_talent、save_answers、save_result
- [ ] AssessmentService：抽题、超时处理、评分、AI 报告生成
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现测评相关模型，并在 app/models/__init__.py 注册。
