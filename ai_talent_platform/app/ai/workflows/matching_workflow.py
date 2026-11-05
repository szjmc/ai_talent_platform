"""
模块：app/ai/workflows/matching_workflow.py
职责：人岗匹配工作流（岗位解析 -> 硬性过滤 -> 向量召回 -> 打分 -> 解释 -> 落库）。

标签：[Agent组] [P1]  依赖：[架构组] matching_score 技能（硬过滤 → 向量召回 → 加权 → AI 解释）

============================ 实现思路与代码要点 ============================
节点编排：
    load_position   读取岗位结构化要求
        ↓
    hard_filter     学历/年限/证书硬性过滤（Repository 查询）
        ↓
    vector_recall   Milvus 召回 top_k 候选人（含过滤条件）
        ↓
    score           综合打分（向量相似度 + 规则加权）
        ↓
    explain         matching_agent 生成解释（引用真实字段）
        ↓
    persist         MatchingService 写入 matching_records（batch_id 归组）
并行优化：
- explain 对候选人并行调用（Semaphore 限流，避免打爆模型配额）。
待实现清单：
    - [ ] 各节点与条件边
    - [ ] 并发控制与超时降级（解释失败时返回规则化说明）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
