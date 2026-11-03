"""
模块：app/ai/rag/retriever.py
职责：向量检索 + 关键词检索 + 重排（混合检索）。

============================ 实现思路与代码要点 ============================
流程：
    问题向量化 -> Milvus top_k 检索（带权限 expr 过滤）
      -> 与关键词检索结果融合（RRF 倒数排名融合）
      -> 可选 rerank 模型重排
      -> 返回 top_n chunk（含 doc_id / title / page / score）
要点：
- 权限过滤在检索阶段完成（expr 中限定 allowed_roles / org_id）；
- score_threshold 可配置，低于阈值的片段不进入上下文，减少幻觉；
- 检索耗时打点，目标 ≤500ms。
待实现清单：
    - [ ] semantic_search / keyword_search / hybrid_search
    - [ ] rerank 接入（可选）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
