# 01. 1 组（RAG 检索增强生成，3 人）— 20 个工作日计划

> 隶属：[AI 能力层与业务层分组开发总计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md) ｜ **必读边界红线**：[六大研发组职责边界与防重复开发总规范 §5.1](../group_boundaries_and_tasks.md#51-1-组rag-组-rag组)
> 人力：3 人（RAG-A 文档侧 / RAG-B 向量侧 / RAG-C 检索问答）

---

## 本组速览与严格边界（防重复必读）

- **组别**：1 组 `[RAG组]`（标签：`标签：[RAG组] [P0|P1]`）
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **本组独占核心边界（全平台唯一实现方，供其他组复用）**：
  1. **全平台唯一的 Milvus 连接与四集合适配器**：`app/infrastructure/vector/milvus.py` + `app/adapters/vector_store/milvus_adapter.py`（不仅支撑本组 `knowledge_vectors`，同时作为 `[业务组]` 读写 `talent_vectors / position_vectors / question_vectors` 的唯一网关，其他组严禁 `import pymilvus`）。
  2. **全平台唯一的文本向量化封装**：`app/ai/rag/embedding_service.py`（底层必须调用 `[架构组] llm_manager.embed`，附带 Redis/内存缓存）。
  3. **全平台唯一的多格式文档文本提取器**：`app/ai/rag/document_loader.py`（同时供本组制度索引、`[Agent组]` 简历解析工作流、`[业务组]` JD 解析复用）。
  4. **知识库业务全链路**：`document_splitter.py` → `retriever.py` → `rag_pipeline.py` → `knowledge_repository.py` → `knowledge_service.py` → `schemas/knowledge.py` → `knowledge_router.py`（`/api/v1/knowledge/*` 7 个接口）。
- **本组严禁重复开发清单**：
  - ❌ **严禁**新建或重写 `app/models/knowledge.py`（`KnowledgeDoc` / `KnowledgeChunk` ORM 模型已完整落地 ✅，直接使用）；
  - ❌ **严禁**修改或重写 `app/ai/skills/knowledge_skill.py`（`[架构组]` 已实现完毕 ✅；本组 `Retriever` 只需支持无参构造 `Retriever()` 并在 `db=None` 时用 `session_scope()`，实现 `hybrid_search(question, top_k=..., scope=...)` 即自动接通）；
  - ❌ **严禁**自写 OpenAI/DashScope HTTP 客户端调大模型或 Embedding（统一调 `app.ai.core.llm_manager.llm_manager`）；
  - ❌ **严禁**在检索结果为空时调用 LLM 自由作答（必须直接返回 `{"answer": "知识库中未找到依据", "citations": []}`）。

---

## 一、人员分工

| 角色 | 负责 |
| --- | --- |
| RAG-A（文档侧） | `document_loader` / `document_splitter` / 解析质量 |
| RAG-B（向量侧） | `embedding_service` / `adapters/vector_store/milvus_adapter` / 索引运维 |
| RAG-C（检索与问答） | `retriever`（混合检索+rerank）/ `rag_pipeline` / `knowledge_service` / `knowledge_router` |

## 二、每日任务（D1–D20，每行末尾是当日验收标准）

| 天 | RAG-A 文档 | RAG-B 向量 | RAG-C 检索问答 | 当日验收 |
| --- | --- | --- | --- | --- |
| D1 | 契约对齐：`Document{content, metadata{source,page,chunk_index}}` 结构签字 | Milvus Docker 起来，`pymilvus` 连通性脚本 | 阅读现有 `knowledge_service` 骨架，列出需要的 Service 方法 | 三人产出 1 页接口约定（进 PR 描述） |
| D2 | `load_txt` + `load_pdf`（pdfplumber），保留页码 | `BaseVectorStore` 抽象 + `MilvusAdapter.create_collection` | 与架构组确认 `embedding_service` 与 `BaseLLM.embed` 的关系（复用 LLM embed，不重复造） | `pytest tests/test_rag_loader.py` 通过 |
| D3 | `load_docx`（python-docx，按标题层级）、`load_excel`（openpyxl） | `upsert / search / delete / drop` 四方法 + 集合 schema（id/pk、vector、metadata JSON） | 混合检索设计：语义 top_k + 关键词（MySQL LIKE / 倒排） | 1000 条文本 upsert 后 search 返回正确 id |
| D4 | `load_image`（OCR，可延后）+ 统一 `load()` 分派 + 编码/乱码处理 | `talent_vectors / position_vectors / knowledge_vectors / question_vectors` 4 集合初始化脚本 | `semantic_search` 打通（query→embed→search→Document） | 4 个集合建好，`describe_collection` 截图进 PR |
| D5 | `split_text`（按 chunk_size/overlap，中文按句号/换行优先） | 索引重建策略（按 doc_id 先删后插，幂等） | `keyword_search` + RRF 融合打分 | 检索回归用例 10 条（含空结果） |
| D6 | `split_by_headings`（制度类文档按章节）+ 元数据补全 | `embedding_service.embed_texts` 批量（batch=16）+ 失败重试 | `hybrid_search` 接口 + 分数归一化 | 混合检索比纯语义 recall@5 提升记录 |
| D7 | 切片质量抽检 20 份文档（切片长度分布、跨页断裂） | 向量维度对齐校验（模型换版本时报警） | `rerank`（可选：LLM rerank 或 bge-reranker），未启用时降级为分数排序 | 抽检报告 |
| D8 | 与 3 组联调：`knowledge_skill.search_knowledge` 接真实 retriever | 增量索引（只处理 status=indexing 的文档） | `rag_pipeline.index_document(doc_id)`：加载→切片→向量化→写 Milvus→回写 `knowledge_chunks` | 上传→索引→可检索 闭环 |
| D9 | 解析失败可重试（记录 `index_error`） | Milvus 写入失败补偿（重试 + 死信表） | `rag_pipeline.ask(question, user)`：检索→拼 Prompt→LLM→citations | 问答接口返回引用 |
| D10 | 文档去重（file_hash 命中复用已索引 chunk） | 集合统计与容量巡检脚本 | 无命中话术：「知识库中未找到依据」+ 不调用 LLM 自由发挥 | 3 条无答案用例全部走兜底 |
| D11 | OCR 能力补齐（扫描件） | Milvus 与 MySQL `knowledge_chunks.milvus_pk` 一致性校验脚本 | `knowledge_service` 全方法 + `knowledge_router` 6 个接口 | 接口在 `/docs` 可见且带权限 |
| D12 | 集成报告 #1：解析成功率 / 切片质量 | 集成报告 #1：检索延迟 P95 | 集成报告 #1：问答准确率抽样 20 题 | 三方报告合并提交 |
| D13 | 权限范围切片（public/dept/role）：过滤发生在检索前 | 向量集合按权限过滤字段（expr）压测 | Prompt 注入防护（引用内容不作为指令） | 越权召回用例 = 0 |
| D14 | 大文件（>20MB）分片上传 | 索引任务异步化（后台任务/队列） | SSE 流式问答 | 流式返回首字节 < 2s |
| D15 | 多格式混合文档回归 | 检索性能优化（nprobe/ef 调参） | 问答缓存（相同问题 + 相同权限 → Redis） | P95 检索 < 300ms（10w 向量） |
| D16 | 文档管理接口联调（删除文档同时删向量） | 集合重建演练（全量重索引） | 与 4 组联调：Dify 知识库同步策略 | 删除无孤儿向量 |
| D17 | 文档补齐（检索参数说明 / FAQ） | 运维手册（扩容、备份、重建） | 与 2 组联调：`assistant_agent` 走 RAG | 端到端：`/agents/chat` 命中知识库 |
| D18 | 单测覆盖率 ≥ 70% | 单测覆盖率 ≥ 70% | 单测覆盖率 ≥ 70% | `pytest tests/test_rag_*.py` |
| D19 | 演示：上传制度文档 → 提问 → 引用 | 演示：向量库巡检 | 演示：多轮追问 | 演示脚本入 `scripts/demo_ai.py` |
| D20 | 终验 | 终验 | 终验 | 总计划 §九 验收清单 |

## 三、本组要写的代码（文件级）

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/ai/rag/document_loader.py` | `pdfplumber`、`python-docx`、`openpyxl` | `load(file_path)->list[Document]`，按扩展名分派；每页一个 Document，metadata 带 `source/page/type`；不支持类型抛 `FileError(5003)` |
| `app/ai/rag/document_splitter.py` | — | `split_text(text, chunk_size, overlap)`、`split_by_headings`、`split_document(doc)`；参数从 `settings` 或 `prompt_config` 读取；chunk 带 `chunk_index` |
| `app/ai/rag/embedding_service.py` | via `BaseLLM.embed` | `embed_texts(texts)` 批量+重试、`embed_query(text)`、进程内 LRU 缓存、维度一致性校验 |
| `app/adapters/vector_store/milvus_adapter.py` | `pymilvus` | `BaseVectorStore`（抽象）+ `MilvusAdapter`（`create_collection/upsert/search/delete/drop`），`@llm_registry` 同级的 `vector_registry` 或直接 `Registry("vector")`；`MILVUS_ENABLED=false` 时构造抛 `ExternalServiceError("Milvus 未启用")` |
| `app/ai/rag/retriever.py` | — | `semantic_search(query, top_k, expr)`、`keyword_search`、`hybrid_search`（RRF）、`rerank`（可关）；返回 `RetrievedChunk{content, source, page, score, doc_id}` |
| `app/ai/rag/rag_pipeline.py` | `langchain`（可选，纯 httpx 亦可） | `index_document(doc_id)`、`ask(question, user)`、`ask_stream()`；写 `knowledge_docs.status/chunk_count/indexed_at` |
| `app/services/knowledge_service.py` | — | 上传→建记录（indexing）→触发索引；`retrieve`、`ask`、`list/page`、`delete`（连带删向量）、`retrieve_eval` |
| `app/routers/knowledge_router.py` | — | 7 个接口（见 `docs/api_document.md` 第十五节），挂 `knowledge:read` / `knowledge:manage` |
| `app/models/knowledge.py`（✅ 已实现，只读复用） / `app/repositories/knowledge_repository.py`（🔧 本组实现） | — | 模型已落地严禁重建；在 `KnowledgeRepository(BaseRepository[KnowledgeDoc])` 中实现 `list_accessible_docs / save_chunks / search_chunks_by_keyword / update_index_status` |
| `tests/test_rag_loader.py`、`test_rag_splitter.py`、`test_retriever.py` | — | 无 Milvus 时用 fake store；测试不依赖外部服务 |
| `scripts/init_milvus.py` | `pymilvus` | 4 个集合建库建索引，幂等 |

---

## 四、本组依赖开关与第一个 PR

```bash
LLM_PROVIDER=qwen
LLM_API_KEY=sk-xxx
MILVUS_ENABLED=true
MILVUS_URI=http://127.0.0.1:19530
RAG_CHUNK_SIZE=512
RAG_CHUNK_OVERLAP=64
RAG_TOP_K=10
RAG_USE_RERANK=false        # 先跑通再开重排
```

**第一个 PR（D2–D3 即可开）**：`feat(rag): 文档加载与切片`
内容：`ai/rag/document_loader.py`、`ai/rag/document_splitter.py` + `tests/test_rag_loader.py`、`tests/test_rag_splitter.py`，预计 300 行（**不依赖 Milvus，可最快合并**）。

**对外交付点**：`rag_pipeline.ask()` 是 2 组 `assistant_agent` 的唯一入口，D9 前必须可用（可先返回 fake 数据让对方联调）。
