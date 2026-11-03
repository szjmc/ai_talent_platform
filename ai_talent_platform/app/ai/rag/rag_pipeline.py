"""
模块：app/ai/rag/rag_pipeline.py
职责：RAG 完整流程编排（索引 + 问答）。

============================ 实现思路与代码要点 ============================
索引流程：
    upload -> document_loader -> document_splitter -> embedding_service
      -> vector_store.upsert -> 回写 knowledge_chunks / knowledge_docs 状态
问答流程：
    question -> retriever -> 组装 Prompt（含 citations 约束）
      -> llm.chat -> 答案 + 引用来源 -> 保存会话消息
约束：
- 检索结果为空时明确回答「知识库中未找到依据」，禁止模型自由发挥；
- 答案必须带 citations（doc_title / chunk / page）；
- Prompt 使用 config/prompt_config.ASSISTANT_PROMPT。
待实现清单：
    - [ ] index_document(doc_id)
    - [ ] ask(question, user) -> {answer, citations}
    - [ ] 与 knowledge_service 的接口约定
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
