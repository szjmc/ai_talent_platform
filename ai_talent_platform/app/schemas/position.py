"""
模块：app/schemas/position.py
职责：岗位管理相关请求 / 响应结构。

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. PositionCreate    title、code、organization_id、level(初/中/高/专家)、
                     headcount(编制人数)、description(岗位说明书全文)、
                     requirements(结构化要求：学历/年限/技能列表)、status
2. PositionUpdate    全部可选字段
3. PositionQuery     keyword、organization_id、level、status、created_at 区间
4. PositionResponse  id、title、code、organization_id、organization_name、level、
                     headcount、status、requirements、skill_tags、created_at
5. PositionRequirement  结构化岗位要求子模型：education、experience_years、
                     skills: list[str]、certificates: list[str]、soft_skills: list[str]
实现要点：
- requirements 建议使用嵌套 Pydantic 模型而非裸 dict，保证 AI 匹配时的字段稳定；
- 岗位说明书解析（AI 提取 requirements）走 Service -> ai/agents/position 流程，
  Router 只接收解析结果或触发解析任务 ID。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
