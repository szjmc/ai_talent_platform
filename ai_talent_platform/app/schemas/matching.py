"""
模块：app/schemas/matching.py
职责：人岗匹配相关请求 / 响应结构。

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. MatchRequest        position_id、talent_ids(可空，空则全库检索)、top_k、
                       strategy(hard/soft/hybrid)、min_score
2. MatchItem           talent_id、talent_name、score(0-100)、
                       dimension_scores(技能/经验/学历/素质)、
                       highlights: list[str]、gaps: list[str]、suggestion
3. MatchResponse       position_id、position_title、matched_at、items: list[MatchItem]
4. MatchExplainRequest position_id、talent_id
5. MatchExplainResponse 匹配维度明细、评分依据、解释文本（AI 生成）
实现要点：
- 硬性条件（学历/年限/证书）先过滤，软性能力用向量相似度加权；
- 匹配过程与结果写入 matching_records 表，支持历史回溯；
- 解释文本由 AI 生成但必须引用真实字段值，防止幻觉（prompt 中约束）。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
