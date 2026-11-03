"""
模块：app/ai/skills/matching_skill.py
职责：人岗匹配技能（供 Agent 调用）。

============================ 实现思路与代码要点 ============================
技能方法：
    calculate_matching_score(talent_id, position_id) -> float
    search_positions(keyword) -> list[dict]
    list_candidates(position_id, top_k) -> list[dict]
要点：
- 打分逻辑复用 MatchingService，避免两套算法；
- 返回结构精简（只给 LLM 必要字段），控制上下文长度。
待实现清单：
    - [ ] 上述方法与工具描述
    - [ ] 注册到 skill_registry
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
