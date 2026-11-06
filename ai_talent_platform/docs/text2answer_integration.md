# 问答附件复用接入

附件原件未改动；项目内新增四个对应文件：routers/text2answer_router.py、services/response_answer_service.py、services/memory_service.py、adapters/llm/LLM_adapter.py。

调用链：POST /api/v1/text2answer → ResponseAnswerService → Retriever → BM25Index/EmbeddingService/MilvusAdapter → llm_manager → 现有DeepSeekAdapter → 保存既有MySQL会话消息 → 提交后ConversationMemory短期缓存。

切片没有复制到问答文件中。文件入库仍由KnowledgeService调用document_loader和document_splitter/split_semantic；问答仅检索既有切片，不重新切片。向量化与数据库连接均复用原有模块。LLM_adapter.py只是异步兼容门面，没有OpenAI SDK客户端、Ollama客户端或独立密钥配置。

接口：POST /api/v1/text2answer，question必填，session_id可选（返回的数字字符串），category/top_k/use_rerank可选；GET /api/v1/conversations分页列本人会话；GET /api/v1/conversations/{session_id}/messages支持limit和before_id。统一响应与knowledge:read权限，其他用户会话一律404。所有category与collection访问范围由现有Retriever权限过滤，不根据意图放宽权限。

复用ConversationMemory时明确绕过原有未绑定conversation_id的通用落库钩子；长期记忆由ConversationRepository写入既有conversations/chat_messages，短期缓存仅在提交后更新。不创建PostgreSQL表或其他表。历史消息与引用可追溯；只有当前用户可访问。无命中时说明资料不足，不回退到无依据的企业制度回答。附件中企业统计路由不直接复制；实时人数等统计应交Vanna/业务接口。

验证：scripts.smoke_text2answer使用真实HTTP路由、MySQL/MinIO/Milvus、DeepSeek测试合成文档；问答引用命中、多轮历史4条、跨用户读历史/续聊404、匿名401通过。一次清理遭遇MinIO502，随后针对本轮已知对象补清理。未执行数据库结构修改。未做压力测试或准确率验收。
