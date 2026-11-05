"""
模块：app/schemas/knowledge.py
职责：知识库相关请求 / 响应结构。

标签：[RAG组] [P1]（文档 / 切片 / 检索响应结构，被 knowledge_router 依赖）

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. KnowledgeDocCreate    title、category、permission_scope(public/dept/role)、
                         allowed_roles: list[str]、source_type(file/text)
2. KnowledgeDocResponse  id、title、category、chunk_count、status(indexing/indexed/failed)、
                         created_at
3. KnowledgeQuery        keyword、category、status
4. RetrieveRequest       question、top_k、score_threshold、use_rerank
5. RetrieveResult        chunks: list[{doc_id, doc_title, content, score, page}]
6. AskRequest / AskResponse  question + 历史对话 -> answer + citations: list[RetrieveResult]
实现要点：
- 上传文档 -> 切片 -> 向量化 -> 写 Milvus 是异步索引流程（状态机 indexing -> indexed）；
- 检索必须带权限过滤：按当前用户角色过滤 allowed_roles 与部门范围；
- 答案必须附带 citations（来源文档 + 片段），用于「无幻觉可溯源」验收。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
