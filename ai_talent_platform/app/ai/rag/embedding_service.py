"""
模块：app/ai/rag/embedding_service.py
职责：文本向量化（批量、缓存、维度对齐）。

============================ 实现思路与代码要点 ============================
1. 通过 llm_manager.embed(texts) 调用向量模型（qwen text-embedding-v3 等）；
2. 批量处理：每批 25-100 条（API 限制），失败重试；
3. 缓存：相同文本 hash 的向量存 Redis，避免重复计费；
4. 维度必须与 Milvus collection 的 dim 一致，不一致直接报错（启动时校验）。
待实现清单：
    - [ ] embed_texts(texts) -> list[list[float]]
    - [ ] embed_query(text) -> list[float]
    - [ ] 缓存与批量
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
