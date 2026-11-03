"""
模块：app/schemas/talent.py
职责：人才档案相关请求 / 响应结构。

标签：[业务组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. TalentCreate      name、gender、birth_date、mobile、email、education、school、major、
                     work_years、current_company、current_position、organization_id、
                     skill_tags: list[str]、self_evaluation
2. TalentUpdate      全部可选
3. TalentQuery       keyword（姓名/手机/技能）、education、work_years_min/max、
                     organization_id、status、skill（技能包含）
4. TalentResponse    id、name、education、work_years、current_position、skill_tags、
                     organization_name、portrait(人才画像摘要)、created_at
5. TalentDetailResponse  在 Response 基础上加：简历列表、测评记录、匹配记录、画像详情
6. TalentPortrait    画像：dimensions(维度分)、labels(标签)、strengths、weaknesses、
                     suggested_positions、potential_level
7. TalentSemanticSearchRequest  语义检索：query(str)、top_k(int=10)、filters
实现要点：
- 手机号、身份证等敏感字段响应时脱敏（core.security.mask_sensitive）；
- skill_tags 在 MySQL 可用 JSON 字段存储，查询时用 JSON_CONTAINS / LIKE（Repository 负责）；
- 语义检索走 Milvus，Service 先取向量再回表（Repository 提供 get_by_ids 批量查询）。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
