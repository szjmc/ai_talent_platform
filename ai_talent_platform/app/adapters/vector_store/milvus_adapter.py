"""
模块：app/adapters/vector_store/milvus_adapter.py
职责：Milvus 向量库适配（统一向量库接口的实现）。

标签：[RAG组] [P0]  依赖：[RAG组] infrastructure/vector/milvus.py（insert/search/delete，带权限 expr）

============================ 实现思路与代码要点 ============================
职责边界（三处不要混淆）：
    infrastructure/vector/milvus.py  -> 只负责「建立 Milvus 客户端连接」
    adapters/vector_store/milvus_adapter.py -> 把客户端封装成平台统一的向量库接口
    ai/rag/*                        -> RAG 业务流程（切片、检索、重排、组装上下文）

统一接口（建议 base_vector_store.py）：
    create_collection(name, dim)              建集合（含索引）
    upsert(collection, ids, vectors, metadata) 写入 / 更新
    search(collection, vector, top_k, expr)    相似度检索（expr 为过滤条件）
    delete(collection, ids | expr)             删除
    drop(collection)                           删除集合
实现要点：
- collection 规划：talent_vectors(dim=embedding 维度)、position_vectors、
  knowledge_vectors；metadata 中存 talent_id/doc_id/chunk_id 等业务主键；
- 权限过滤放到 expr（如 role in ['hr'] 与 org_id == x），避免越权召回；
- 批量写入按 1000 条一批（utils/helpers.chunked）；
- 检索耗时需打点（目标 ≤500ms）。
待实现清单：
    - [ ] BaseVectorStore 抽象类
    - [ ] MilvusAdapter（依赖 pymilvus）
    - [ ] 注册到 patterns.registry（"milvus"）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
