"""
模块：app/models/knowledge.py
职责：知识库文档元数据 + 切片元数据（向量本体存 Milvus）。

============================ 实现思路与代码要点 ============================
表设计：
1. knowledge_docs
   - title、category、source_type(file/text)、file_id、status(indexing/indexed/failed)、
     chunk_count、permission_scope(public/dept/role)、allowed_roles(JSON)、
     organization_id、embedding_model、index_error、indexed_at
2. knowledge_chunks
   - doc_id、chunk_index、content、token_count、milvus_pk(向量库主键)、page_no
关键思路：
- 关系库存「元数据 + 切片原文」，Milvus 只存「向量 + milvus_pk」；
  检索时先查 Milvus 拿 pk，再回表取原文，保证数据一致性；
- 权限字段必须落到元数据层，检索时按当前用户角色过滤（RAG 的权限隔离）。
待实现清单：
- [ ] 模型字段与索引（doc_id + chunk_index 唯一）
- [ ] KnowledgeRepository：list_by_permission、save_chunks、update_index_status
- [ ] KnowledgeService：上传-切片-向量化-入库流程（配合 ai/rag/*）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现知识库模型，并在 app/models/__init__.py 注册。
