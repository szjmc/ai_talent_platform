"""
模块：app/models/talent.py
职责：人才档案表 + 人才画像表。

============================ 实现思路与代码要点 ============================
表设计：
1. talents（人才主档）
   - 基本：name、gender、birth_date、mobile、email、id_card(加密存储)
   - 教育：education(最高学历)、school、major
   - 工作：work_years、current_company、current_position、organization_id
   - 能力：skill_tags(JSON 数组)、certificates(JSON)、self_evaluation
   - 画像摘要：portrait_summary、potential_level、status(在职/离职/储备)
   - 系统：source(手动/AI解析)、resume_id(来源简历)、is_deleted 等（继承 BaseModel）
2. talent_portraits（人才画像，1:1 或 1:N 存版本）
   - talent_id、version、dimensions(JSON: 技能/经验/素质/潜力分数)、
     labels(JSON: AI 标签)、strengths、weaknesses、suggested_positions、
     potential_level、model_name、generated_at
关键思路：
- 唯一性：mobile + is_deleted 组合唯一（逻辑删除后允许重新录入），可用部分索引或 Service 校验；
- skill_tags 用 JSON 列，MySQL 8 支持 JSON 函数查询；需要高频检索的技能另建 talent_skills 关联表；
- 画像由 AI 生成，属于「可重建数据」，与主档分开存储，避免污染核心表。
待实现清单：
- [ ] 模型字段与索引
- [ ] TalentRepository：get_by_mobile、search_by_skills(JSON)、get_by_ids、count_by_org
- [ ] TalentService：创建/更新/查重/画像刷新
- [ ] 画像生成流程：ai/agents/talent_agent.py + ai/workflows/analysis_workflow.py
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 Talent / TalentPortrait 模型，并在 app/models/__init__.py 注册。
