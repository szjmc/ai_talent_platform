# AI 能力层分组开发计划（按天任务 + 每组代码清单）

> 版本：**v1.0.0** ｜ 制定依据：本仓库现有骨架（`app/ai/**`、`app/adapters/**` 全部为 TODO 桩）+ 架构组 5 个方向的分工
> 适用范围：RAG 组、Agent 组、Skills 组（架构组）、Vanna 组、Dify 组
> 配套文档：[分组代码实现规范](team_code_spec.md)（每组每个文件的方法签名、返回结构、验收命令）

---

## 一、组织与既有资产

### 1.1 分组

| 组别 | 方向 | 人力 | 说明 |
| --- | --- | --- | --- |
| 架构组 | 公共层 / 契约 / Skills 组件库 | 2 人 + 4 组长 | 已交付公共层（认证/用户/组织/异常/响应/Repository/健康检查）；后续负责 Skills 体系与跨组契约仲裁 |
| 1 组 | RAG 检索增强生成（Milvus + LangChain） | 3 人 | 知识库索引、混合检索、引用式问答 |
| 2 组 | AI Agent 多智能体（LangGraph） | 4 人 | 工作流引擎、6 个业务 Agent、会话与状态持久化 |
| 3 组 | Vanna.ai NL2SQL 数据分析 | 4 人 | 训练数据、SQL 安全、图表推断、数据权限 |
| 4 组 | Dify AI 应用中台 | 2 人 | 模型/Prompt/知识库运维统一封装、SSE 转发 |

### 1.2 已就绪（**不要重写，直接调用**）

| 能力 | 位置 | 组外需知 |
| --- | --- | --- |
| 统一响应 `success()/api_response()` | `app/core/response.py` | 所有 Router 返回值 |
| 业务异常体系（code 1001–5003） | `app/core/exceptions.py` | AI 组用 `AIError`(5002) / `ExternalServiceError`(5001) |
| 权限依赖 `require_permission` | `app/core/dependencies.py` | Router 必须挂 |
| 分页 `PageParams / build_page_result` | `app/core/pagination.py` | 列表接口 |
| 注册表 `Registry / agent_registry / skill_registry / llm_registry` | `app/patterns/registry.py` | **三组共用，禁止另起注册表** |
| 配置单例 `settings` | `app/config/settings.py` | 已有 `LLM_* / DIFY_* / MILVUS_* / REDIS_* / PGSQL_*`，新增配置只能加在这里 |
| 提示词仓库 `PROMPT_VERSION` + 模板 | `app/config/prompt_config.py` | 提示词禁止写死在业务代码 |
| 日志 `get_logger` + trace_id | `app/config/logging_config.py` | 禁止 `print` |
| MySQL 会话 / PgSQL 会话 / 事务 | `app/infrastructure/database/` | `get_db` / `pg_session` / `transaction` |
| 健康检查 `check_databases()` | `app/infrastructure/database/health.py` | AI 组启动自检 LLM/Milvus/Dify 时参考其返回结构 |
| Repository 基类 + 13 个业务仓库 | `app/repositories/` | AI 层**禁止直接写 SQL**，只调 Service |

### 1.3 本计划的三条硬约束

1. **AI 层不写 SQL、不碰 ORM 明细**：Agent / Skill / RAG / NL2SQL 一律通过 `app/services/*` 或 Repository 公开方法取数；
2. **权限来自上下文，不来自提示词**：所有过滤条件从 `app/ai/context.py` 的 `AIContext` 读取，`skill` 忽略 LLM 传入的 user/role 参数；
3. **外部依赖可在缺失时降级**：Milvus / Redis / Dify / Vanna 未部署时，服务必须能启动、接口返回明确错误码，不能 500 崩溃（与现有 `/health/ready` 的 `degraded` 语义一致）。

### 1.4 依赖开关（`.env`，各组本地必须能跑通无外部依赖的用例）

```bash
LLM_PROVIDER=qwen
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-xxx
MILVUS_ENABLED=true            # RAG 组
MILVUS_URI=http://127.0.0.1:19530
REDIS_ENABLED=true             # 2 组状态持久化 / 4 组 SSE 会话
REDIS_URL=redis://127.0.0.1:6379/0
DIFY_BASE_URL=http://127.0.0.1/v1   # 4 组
DIFY_API_KEY=app-xxx
PGSQL_ENABLED=true             # 3 组 NL2SQL 只读分析库
```

---

## 二、时间安排总表（4 周 / 20 个工作日）

| 阶段 | 日期块 | 里程碑 | 验收标准 |
| --- | --- | --- | --- |
| S0 契约冻结 | D1–D2 | 5 组接口与数据结构对齐 | `app/adapters/llm/base_llm.py`、`app/ai/context.py`、`app/patterns/registry.py` 三类契约定稿，各组可并行 |
| S1 通道打通 | D3–D5 | LLM 能调通、Milvus 能连通、Dify 能调通 | 各组「hello world」集成用例绿 |
| S2 单模块闭环 | D6–D12 | RAG 问答可用 / 简历解析可用 / NL2SQL 可用 | 每个方向至少 1 条端到端接口返回真实结果 |
| S3 编排与联动 | D13–D17 | LangGraph 工作流 + Skills 被 Agent 调用 | Agent 能调用 Skill、能走多节点工作流 |
| S4 加固交付 | D18–D20 | 压测、降级、文档、演示 | 见第九节验收清单 |

> 依赖关系（谁等谁）：
> `架构组 S0 的 BaseLLM / AIContext / Registry` → 全部组；
> `1 组 retriever + embedding` → `Skills 组 knowledge_skill` → `2 组 assistant_agent`；
> `2 组 graph_builder` → `matching / resume / analysis workflow`；
> `3 组 sql_validator + vanna_adapter` → `Skills 组 sql_skill` → `2 组 analytics 相关 Agent`；
> `4 组 dify_adapter` 独立，但 **SSE 输出格式**要与 `2 组 dispatch_stream` 对齐。

---

## 三、架构组（2 人 + 4 组长）— D1–D20

定位：**契约供给方 + Skills 组件库 + 集成验收**。架构组不做业务 Agent、不做 RAG 实现，只做「别人要用的东西」。

### 3.1 人员分工

| 角色 | 负责范围 |
| --- | --- |
| 架构 A（公共层） | `ai/context.py`、`ai/core/*`、`ai/utils/*`、`adapters/llm/base_llm.py` + 3 个 LLM 适配器 |
| 架构 B（集成） | `ai/skills/**`（Skills 体系）、`adapters/storage/**`、CI、契约仲裁、每日集成报告 |
| 4 位组长 | 各组方向评审、跨组接口签字、验收把关（每天 30 分钟站会 + 每周集成日） |

### 3.2 每日任务

| 天 | 架构 A（公共层/模型底座） | 架构 B（Skills/集成） | 当日交付物 |
| --- | --- | --- | --- |
| D1 | `ai/context.py`：`AIContext` 数据类 + `ContextVar` + `set/get/clear_context`；从 `User` 映射 roles/org | `ai/skills/base.py` 新增：`BaseSkill` 抽象（`name`/`description`/`parameters`(JSON Schema)/`execute`）+ `@skill_registry.register` 约定 | 两份契约文件 + 契约评审会（4 组长签字） |
| D2 | `adapters/llm/base_llm.py`：`ChatMessage`/`LLMResult`/`BaseLLM` 四个方法；`ai/utils/retry_handler.py`：`ai_retry` + `SimpleCircuitBreaker` | `ai/utils/output_parser.py`：`parse_json`（去 ```json 围栏、截取首尾大括号）+ `parse_model`（Pydantic 校验） | 单测：`tests/test_ai_utils.py`（解析容错 6 个用例） |
| D3 | `adapters/llm/openai_adapter.py`（兼容 DashScope compatible-mode）：`chat/stream/embed` | `ai/core/token_manager.py`：`record/usage_today/check_quota`（先进程内 dict，Redis 就绪后切） | 真实调用 1 次 qwen-plus 成功，日志有 prompt/completion tokens |
| D4 | `adapters/llm/qwen_adapter.py` + `ollama_adapter.py`；`llm_registry` 注册 | `ai/core/llm_manager.py`：单例 + `get_llm(purpose)` + 统一埋点 | `LLMManager().chat()` 三 provider 均可切换（ollama 缺省跳过） |
| D5 | `ai/core/model_selector.py`：`MODEL_BY_PURPOSE` + 降级链 | `ai/skills/registry_bootstrap.py` 新增：启动时导入全部 skill 模块完成注册 | `/api/v1/system/info` 增加 `ai_ready: {llm, skills, milvus}` 字段 |
| D6 | `ai/agents/base_agent.py`：`run()` 主流程（build→chat→parse→persist→埋点） | Skills：`knowledge_skill.search_knowledge`（依赖 1 组 retriever，先用 fake 实现占位打桩） | `BaseAgent` 单测（mock LLM） |
| D7 | `ai/agent.py`：`dispatch / dispatch_stream` + `AGENT_TYPES` | Skills：`matching_skill`（3 个方法） | 调度器单测（未注册 type → 404/3001） |
| D8 | 与 2 组联调：`dispatch` 打通 `assistant` agent | Skills：`resume_skill`（`parse_resume`/`extract_skills`/`dedupe_resume`） | 端到端：`POST /agents/tasks` 返回 200 |
| D9 | Skills 的 JSON Schema 工具描述（供 LLM function-calling） | `adapters/storage/local_storage_adapter.py` + `BaseStorage` | 工具 schema 导出为 `docs/skills_schema.json` |
| D10 | `ai/memory/conversation_memory.py`（短期窗口 + 摘要触发） | Skills：`sql_skill.query_database`（依赖 3 组校验器） | 多轮对话记忆单测 |
| D11 | `ai/memory/state_manager.py`（Redis / MySQL 双实现，可降级内存） | Skills 全量注册联调 + `/skills` 列表接口（`GET /api/v1/system/skills`） | 前端/其他组可查询技能清单 |
| D12 | 全组代码评审第一轮（契约一致性） | 集成报告 #1（各组进度 + 阻塞项） | 评审记录 + 阻塞项清零计划 |
| D13 | `ai/prompts/*` 补齐版本号 + 渲染函数（与 `prompt_config` 合并去重） | `tests/test_ai_contract.py`：跨组契约测试（响应结构/错误码） | 契约测试进入 CI |
| D14 | 性能与成本：token 配额、超时、并发上限 | Milvus 集合初始化脚本 `scripts/init_milvus.py`（配合 1 组） | 建集合脚本可重复执行 |
| D15 | 全链路 trace：AI 调用埋点进审计 | `scripts/train_vanna.py` 与 3 组共同验收 | 训练数据入库脚本 |
| D16 | 降级演练：LLM 不可用 / Milvus 不可用 | 降级演练记录 | 接口返回 5001/5002 而非 500 |
| D17 | 安全评审：提示词注入、越权检索、SQL 逃逸 | 安全评审报告 | 高危项 = 0 |
| D18 | 文档：`公共层使用手册` 增加 AI 章节 | `docs/team_code_spec.md` 校对 | 文档评审通过 |
| D19 | 演示脚本 `scripts/demo_ai.py`（5 个方向各 1 条链路） | 演示数据准备 | 一键演示可跑 |
| D20 | 终验 + 交付 | 终验报告 | 第九节清单全绿 |

### 3.3 架构组要写的代码（文件级）

| 文件 | 动作 | 关键内容 |
| --- | --- | --- |
| `app/ai/context.py` | 实现 | `AIContext(user_id, roles, organization_id, trace_id, conversation_id, extra)`、`current_ai_context: ContextVar`、`set_context(user, **extra)`、`get_context()`（未设置返回匿名上下文，不抛异常）、`clear_context()` |
| `app/ai/utils/retry_handler.py` | 实现 | `ai_retry(max_attempts=3, base_delay=0.5, retry_on=(TimeoutError, ExternalServiceError, AIError))` 指数退避 + `SimpleCircuitBreaker(fail_threshold=5, reset_seconds=30)` |
| `app/ai/utils/output_parser.py` | 实现 | `parse_json`（去围栏 / 截取 `{...}` / 单引号修复）、`parse_model(text, schema)`，失败抛 `AIError(5002)` |
| `app/adapters/llm/base_llm.py` | 实现 | `ChatMessage(role, content)`、`LLMResult(content, model, prompt_tokens, completion_tokens, elapsed_ms, raw)`、`BaseLLM.chat/generate/stream/embed` |
| `app/adapters/llm/{openai,qwen,ollama}_adapter.py` | 实现 | httpx.AsyncClient + 统一重试 + `@llm_registry.register("openai"/"qwen"/"ollama")` |
| `app/ai/core/llm_manager.py` | 实现 | 单例、`get_llm(purpose: str)` 走 model_selector、`chat/embed` 封装埋点 |
| `app/ai/core/model_selector.py` | 实现 | `MODEL_BY_PURPOSE = {"resume": ..., "matching": ..., "sql": ..., "chat": ...}`、`select_model`、降级链 |
| `app/ai/core/token_manager.py` | 实现 | `record(user_id, model, pt, ct, purpose)`、`usage_today`、`check_quota`（超限抛 `BusinessRuleError`） |
| `app/ai/skills/base.py` | **新增** | `BaseSkill` + 工具 schema 生成（`to_openai_tool()`） |
| `app/ai/skills/{knowledge,matching,resume,sql}_skill.py` | 实现 | 骨架与本组 Skill 规则见 `team_code_spec.md` 第 1.7 节 |
| `app/ai/skills/registry_bootstrap.py` | **新增** | 导入即注册，`bootstrap_skills()` 幂等 |
| `app/ai/agents/base_agent.py` | 实现 | `run()`：build_prompt → llm.chat（重试）→ parse_output → persist → 记录 token/耗时；失败返回结构化错误 |
| `app/ai/agent.py` | 实现 | `AGENT_TYPES`、`dispatch(agent_type, payload, db)`、`dispatch_stream()` |
| `app/ai/memory/{conversation_memory,state_manager}.py` | 实现 | 窗口记忆 + 摘要；状态 JSON 序列化（datetime→isoformat） |
| `app/adapters/storage/local_storage_adapter.py` | 实现 | `save/read/delete/exists/url` + 路径穿越防护 |
| `scripts/init_milvus.py`、`scripts/demo_ai.py` | **新增** | 集合初始化；一键演示 |

---

## 四、1 组：RAG 检索增强生成（Milvus + LangChain，3 人）— D1–D20

### 4.1 人员分工

| 角色 | 负责 |
| --- | --- |
| RAG-A（文档侧） | `document_loader` / `document_splitter` / 解析质量 |
| RAG-B（向量侧） | `embedding_service` / `adapters/vector_store/milvus_adapter` / 索引运维 |
| RAG-C（检索与问答） | `retriever`（混合检索+rerank）/ `rag_pipeline` / `knowledge_service` / `knowledge_router` |

### 4.2 每日任务

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
| D20 | 终验 | 终验 | 终验 | 第九节清单 |

### 4.3 1 组要写的代码

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
| `app/models/knowledge.py` / `app/repositories/knowledge_repository.py` | — | 现有骨架补全 `knowledge_docs` / `knowledge_chunks` 字段与查询 |
| `tests/test_rag_loader.py`、`test_rag_splitter.py`、`test_retriever.py` | — | 无 Milvus 时用 fake store；测试不依赖外部服务 |
| `scripts/init_milvus.py` | `pymilvus` | 4 个集合建库建索引，幂等 |

---

## 五、2 组：AI Agent 多智能体（LangGraph，4 人）— D1–D20

### 5.1 人员分工

| 角色 | 负责 |
| --- | --- |
| AG-A（引擎） | `workflows/graph_builder.py`、`memory/state_manager.py`、checkpointer |
| AG-B（工作流） | `resume_workflow`、`matching_workflow`、`analysis_workflow` |
| AG-C（Agent 1） | `base_agent` 落地 + `resume_agent`、`talent_agent` |
| AG-D（Agent 2） | `matching_agent`、`assessment_agent`、`training_agent`、`assistant_agent` |

### 5.2 每日任务

| 天 | AG-A 引擎 | AG-B 工作流 | AG-C Agent | AG-D Agent | 当日验收 |
| --- | --- | --- | --- | --- | --- |
| D1 | `WorkflowState` TypedDict 规范（各工作流统一 errors/retries/status） | 画出 3 条工作流的节点图（与架构组评审） | 与架构组对齐 `BaseAgent` 契约 | 与架构组对齐 `BaseAgent` 契约 | 节点图 + state 定义签字 |
| D2 | `build_graph()` 封装（nodes/edges/entry_point/conditional_edges） | `resume_workflow` 节点骨架（extract→clean→llm→validate→persist） | `resume_agent.build_prompt`（读文件→文本→Prompt） | `assistant_agent` 骨架（依赖 1 组 RAG，先 fake） | `build_graph` 单测：3 节点直连跑通 |
| D3 | 条件边 + 重试节点工具（`route_by_status`） | resume_workflow 条件边（validate 失败→retry ≤2 次） | `resume_agent.parse_output`（JSON 容错 + Pydantic） | `talent_agent.build_prompt`（人才档案→画像 Prompt） | 简历文本 → 结构化 JSON 成功 10 条 |
| D4 | `run_workflow(graph, initial_state, thread_id)` | resume_workflow 全链路跑通（mock LLM） | `resume_agent.persist`（写 `resumes.parsed_data`、`talents` 主档） | `talent_agent.parse_output/persist`（写 `talent_portraits`） | 简历落库 + 画像落库 |
| D5 | checkpointer 装配（Memory → Redis → MySQL 三级降级） | matching_workflow：岗位解析→硬性过滤→向量召回 | `matching_agent` 骨架 | `assessment_agent` 骨架 | 断点续跑演示：中断后从 checkpoint 恢复 |
| D6 | `state_manager` save/load/delete + 序列化 | matching_workflow：打分→解释→落库（`matching_records`） | `resume_agent` 接真 LLM（qwen-plus）验证 | `assessment_agent`：题目生成 + 结果研判 | 真 LLM 简历解析耗时/准确率记录 |
| D7 | 并发控制（`asyncio.Semaphore`）+ 超时降级 | matching_workflow 解释失败→规则化说明 | `talent_agent` 接真 LLM | `training_agent`（课程/计划推荐） | 匹配 100 对批量跑通，失败率 < 5% |
| D8 | 人工介入节点（`interrupt`）设计评审 | analysis_workflow：人才研判 / 团队结构 / 决策建议三节点 | `resume_agent` 边界用例（扫描件、空简历） | `assistant_agent` 接 1 组 RAG 真链路 | 问答带 citations |
| D9 | LangGraph 子图（Agent 即节点）封装 | analysis_workflow 与 `analytics_service` 对接 | Agent 注册到 `agent_registry` 全量 | Agent 注册补齐 + 单测（mock LLM 固定 JSON） | `registry.keys()` 6 个 agent |
| D10 | 与架构组联调 `ai/agent.py dispatch` | resume/matching 工作流串联（简历→人才→匹配） | `dispatch("resume")` 端到端 | `dispatch("assistant")` 端到端 | `POST /agents/tasks` 可用 |
| D11 | `dispatch_stream`（SSE，逐 token） | analysis_workflow 输出报告结构（JSON → 前端卡片） | `resume_agent` 性能优化（长简历分段） | `matching_agent` + `matching_skill` 复用 | SSE 首字节 < 2s |
| D12 | 集成报告 #1：工作流成功率 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 报告合并 |
| D13 | 会话记忆接入工作流 | 工作流幂等（同一请求重复执行不重复落库） | Agent 失败兜底（返回 5002 + 可重试标识） | Agent 权限校验（谁能触发哪个 Agent） | 幂等用例 + 越权用例 |
| D14 | 多智能体协作（Supervisor 路由：assistant→talent→matching） | 协作流程节点 | 参与协作节点 | 参与协作节点 | 一句话触发跨 Agent 任务 |
| D15 | 性能：图执行耗时埋点 | 批量匹配并发（限流 5） | token 成本统计接入 | token 成本统计接入 | 单次简历解析成本记录 |
| D16 | 降级演练（LLM 超时 / 子 Agent 挂） | 降级演练 | 降级演练 | 降级演练 | 无 500，全为业务错误码 |
| D17 | 文档：工作流如何新增节点 | 文档：三条工作流说明 | Agent 新增指南 | Agent 新增指南 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | `pytest tests/test_workflows.py` |
| D19 | 演示：简历→画像→匹配一键跑 | 演示：分析报告 | 演示：Agent 对话 | 演示：测评 | 演示脚本 |
| D20 | 终验 | 终验 | 终验 | 终验 | 第九节清单 |

### 5.3 2 组要写的代码

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/ai/workflows/graph_builder.py` | `langgraph` | `WorkflowState`、`build_graph(nodes, edges, entry_point, conditional_edges)`、`run_workflow(graph, state, thread_id)`、条件边/循环/重试工具 |
| `app/ai/workflows/resume_workflow.py` | `langgraph` | 节点：`extract_text → clean → llm_structure → validate → persist`；条件边 `validate` 失败→`llm_structure`（≤2 次）→`fail` |
| `app/ai/workflows/matching_workflow.py` | `langgraph` | 节点：`parse_position → hard_filter → vector_recall → score → explain → persist`；解释失败走规则化说明 |
| `app/ai/workflows/analysis_workflow.py` | `langgraph` | 节点：`collect_metrics → talent_review → team_structure → recommendation`；调 `analytics_service` |
| `app/ai/memory/state_manager.py` | `redis`（可选） | `save/load/delete`，Redis 不可用降级 MySQL JSON 列/进程内 dict |
| `app/ai/memory/conversation_memory.py` | `redis` | `append/get_recent(limit=10)/clear/summarize_if_needed` |
| `app/ai/agents/base_agent.py` | — | （与架构组共同）`run()` 骨架 |
| `app/ai/agents/{resume,talent,matching,assessment,training,assistant}_agent.py` | — | 各自 `build_prompt/parse_output/persist` + `@agent_registry.register("xxx")` |
| `app/ai/agent.py` | — | （与架构组共同）`dispatch/dispatch_stream` |
| `app/services/{resume,talent,matching,assessment,training,agent}_service.py` | — | 补全 Service 方法（Agent 只调这些） |
| `app/routers/agent_router.py` | — | 6 个接口：`POST /agents/chat`、`POST /agents/chat/stream`(SSE)、`GET /agents/conversations`、`GET /agents/conversations/{id}/messages`、`POST /agents/tasks`、`GET /agents/tasks/{task_id}` |
| `tests/test_workflows.py`、`test_agents.py` | — | mock LLM；记忆/状态用内存 fake 或 Redis（**平台不使用 SQLite，测试也不引入**） |

---

## 六、3 组：Vanna.ai NL2SQL（4 人）— D1–D20

### 6.1 人员分工

| 角色 | 负责 |
| --- | --- |
| NL-A（训练） | `scripts/train_vanna.py`、DDL/术语/示例 SQL 语料、Vanna 服务化 |
| NL-B（安全） | `sql_validator`（语法、白名单表、只读、LIMIT、数据范围注入） |
| NL-C（适配与图表） | `vanna_adapter.ask`、图表推断、insight 生成 |
| NL-D（业务接口） | `analytics_service`、`analytics_router`、导出、与 PgSQL 宽表 |

### 6.2 每日任务

| 天 | NL-A 训练 | NL-B 安全 | NL-C 适配/图表 | NL-D 接口/宽表 | 当日验收 |
| --- | --- | --- | --- | --- | --- |
| D1 | 盘点分析场景 20 条（人员结构、流失率、培训完成率…） | 列出可查表白名单：分析库 6 表（`fact_talent_snapshot` / `fact_assessment_history` / `fact_training_track` / `fact_behavior_event` / `dim_organization` / `dim_talent_wide`） | Vanna/模型选型确认（Vanna + qwen 或 OpenAI 兼容） | 与 1/2 组对齐 `analytics` 接口契约 | 场景清单 + 白名单签字 |
| D2 | DDL 语料准备（PgSQL 6 张宽表） | `validate_select_sql`：仅 SELECT、无子查询套 DDL、拒绝 `;` 多语句 | `vanna_adapter` 初始化（配置/训练数据加载） | PgSQL 宽表 6 张建表（配合数据库文档第七节） | 校验器单测 15 条（含注入样例） |
| D3 | 示例 SQL 语料 40 条 | `inject_limit`、`inject_data_scope`（部门/角色过滤） | `generate_sql(question, context)` 跑通 | 宽表 ETL 脚本 1（人员结构） | 单表问数可生成正确 SQL |
| D4 | 业务术语词典（「骨干」「流失率」「达标率」） | 只读连接（PgSQL 只读账号）验证 | SQL → 执行 → columns/rows | 宽表 ETL 2（培训） | 端到端：问题→SQL→数据 |
| D5 | 训练 1 轮（DDL+SQL+术语），记录准确率基线 | 表名/字段白名单从 information_schema 动态取 | 图表类型推断（时间→折线、枚举→柱状、占比→饼图） | 宽表 ETL 3（测评） | 20 条场景准确率 ≥ 60% |
| D6 | 错误 SQL 收集 + 修正（few-shot 回灌） | `validate` 覆盖率与漏测补齐 | `insight` 生成（LLM 解读数据） | 宽表 ETL 4（匹配） | 准确率 ≥ 75% |
| D7 | Vanna 训练持久化（chromadb/pgvector 存储位置评审） | 行级权限：非管理员强制带 `organization_id` 条件 | 图表配置输出规范 `{type,x_field,y_field,title,series}` | 宽表 ETL 5–6（流失/人才库） | 图表配置被前端 Demo 渲染 |
| D8 | 与 Skills 联调：`sql_skill.query_database` | 与架构组安全评审（SQL 逃逸） | `VannaAdapter.ask(question, user_scope) -> NL2SQLResponse` | `analytics_service` 方法骨架 | `sql_skill` 可用 |
| D9 | 训练数据版本化（v1 快照） | 超时/大查询拦截（扫描行数上限） | 多轮追问（上下文表结构） | `analytics_router`：`overview/chat/chart/export` | 接口 `/docs` 可见 |
| D10 | 准确率 ≥ 85% 冲刺 | 审计：每次 NL2SQL 记录 question/sql/耗时/用户 | 结果缓存（同问同权限 5 分钟） | 导出（Excel/CSV，异步任务） | 审计记录可查 |
| D11 | 补充行业口径（HR 指标定义） | 恶意问法演练 20 条（删库/拖库/越权） | 失败兜底：SQL 生成失败 → 提示 + 推荐相近问题 | `analytics_service` 全方法 | 恶意问法 0 命中 |
| D12 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 报告合并 |
| D13 | 增量训练流程文档 | 校验器 fuzz 测试 | 图表推断边界（空数据/单列） | 看板接口（预置 6 个） | 空数据不报 500 |
| D14 | 训练评估集（50 题）固化 | 性能：校验开销 < 10ms | Vanna 并发（Semaphore=5） | 大查询异步化（任务号轮询） | P95 问数 < 8s |
| D15 | 准确率报告 | 审计与告警（异常 SQL 告警） | insight 质量抽检 20 条 | 导出限流 | 报告 |
| D16 | 与 4 组联调：Dify 也可做 NL2SQL 的边界划分 | 降级：Vanna 不可用时直接 LLM 生成 SQL 仍过校验器 | 降级演练 | 降级演练 | 无 500 |
| D17 | 文档：如何新增训练语料 | 文档：安全管理说明 | 文档：图表规范 | 文档：宽表口径 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | `pytest tests/test_nl2sql.py` |
| D19 | 演示：自然语言问数 | 演示：注入被拦截 | 演示：图表 + 解读 | 演示：导出 | 演示脚本 |
| D20 | 终验 | 终验 | 终验 | 终验 | 第九节清单 |

### 6.3 3 组要写的代码

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/adapters/nl2sql/sql_validator.py` | `sqlglot` | `validate_select_sql(sql, allowed_tables)->str`、`inject_limit(sql, max_rows=1000)`、`inject_data_scope(sql, scope_sql)`；禁止多语句/DDL/DML/系统表 |
| `app/adapters/nl2sql/vanna_adapter.py` | `vanna` | `ask(question, user_scope)->NL2SQLResponse{sql, columns, rows, chart, insight, elapsed_ms}`；内部固定顺序：generate → validate → execute(只读 PgSQL) → chart → insight |
| `scripts/train_vanna.py` | `vanna` | 导入 DDL（分析库 6 表）/ 指标口径文档 / 示例 SQL / 业务术语；`--reset` 重建 |
| `app/services/analytics_service.py` | — | `overview()`、`nl2sql_ask()`、`chart_config()`、`export()`、`dashboard()`；调 Service 层而非直连 SQL |
| `app/routers/analytics_router.py` | — | 6 个接口：`GET /analytics/overview`、`GET /analytics/distribution`、`GET /analytics/trend`、`POST /analytics/nl2sql`、`POST /analytics/report`、`GET /analytics/reports`，挂 `analytics:read` / `analytics:export` |
| PgSQL 宽表 DDL + 模型 | — | 6 张分析宽表（见 `docs/database_design.md` 第七节），只读账号 |
| `app/ai/skills/sql_skill.py` | — | （与架构组共建）`query_database(question)`，内部捕获 `SQLValidationError` → `BusinessRuleError` |
| `tests/test_nl2sql.py` | — | 校验器 30 条 + 适配器 mock 10 条 |

---

## 七、4 组：Dify AI 应用中台（2 人）— D1–D20

### 7.1 人员分工

| 角色 | 负责 |
| --- | --- |
| DF-A（适配器） | `dify_adapter`（chat/workflow/dataset）、SSE 解析、重试 |
| DF-B（集成与运维） | Dify 部署、应用/工作流配置、Prompt 同步、与其余 4 组对接 |

### 7.2 每日任务

| 天 | DF-A 适配器 | DF-B 集成/运维 | 当日验收 |
| --- | --- | --- | --- |
| D1 | 读 Dify API 文档，确认 4 个能力签名 | Docker 部署 Dify（api/worker/web/db/redis），记录版本 | Dify 控制台可访问，健康检查绿 |
| D2 | `__init__`（httpx.AsyncClient、Bearer、超时、trace 头） | 建 2 个应用：通用问答 / 简历解析；导出 DSL 备份 | 单测：走 MockTransport 返回固定 JSON |
| D3 | `chat(app_id, query, conversation_id, user)` | 配置模型供应商（通义/OpenAI 兼容），额度与并发 | 真实对话返回答案 + conversation_id |
| D4 | `run_workflow(workflow_id, inputs, user)`（阻塞） | 建工作流 1：简历解析（输入文件 URL → 结构化 JSON） | 工作流跑通并回传 JSON |
| D5 | SSE 解析（`stream` 逐行 `data:`，事件类型 `message/workflow_finished/error`） | 断线重连与 Dify 侧 conversation 复用 | 流式输出逐 token 到前端 |
| D6 | `upload_document / list_documents(dataset_id)` | 建知识库 dataset，导入 20 份制度文档 | 文档列表可查、可删除 |
| D7 | 错误映射（Dify 4xx/5xx → `ExternalServiceError`，附 request_id） | 与 1 组对齐：Dify 知识库 vs 自建 Milvus 的边界（谁做索引） | 边界决策写入文档 |
| D8 | 回调/日志查询接口（Dify logs） | 与 2 组对齐：Dify 工作流 vs LangGraph 的边界 | 边界决策签字 |
| D9 | 统一入口 `DifyClient` 注册到 `Registry("dify")` | Prompt 同步：`prompt_config` ←→ Dify Prompt（单向覆盖） | `prompt_config` 支持远端覆盖 |
| D10 | 并发与限流（Semaphore + 429 退避） | 配额监控（按应用统计调用量） | 压测 50 QPS 不雪崩 |
| D11 | 与 `agent_router` 的 SSE 转发打通 | 演示应用 3 个（问答/解析/分析）上线 | `/agents/chat/stream` 走 Dify 可用 |
| D12 | 集成报告 #1 | 集成报告 #1 | 报告 |
| D13 | 健康检查 `check_dify()`（供 `/health/ready` 扩展） | Dify 备份/恢复演练 | `/health/ready` 含 dify 状态 |
| D14 | 重试与熔断（复用 `ai/utils/retry_handler`） | 日志对齐（trace_id → Dify request_id） | 排障演练 15 分钟定位问题 |
| D15 | 文件上传大文件分片 | 知识库增量同步脚本 | 同步脚本幂等 |
| D16 | 降级：Dify 不可用 → 回退本地 Agent/RAG | 降级演练 | 无 500 |
| D17 | 文档：Dify 应用如何接新 Agent | 文档：运维手册 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | CI 加入 Dify Mock 用例 | `pytest tests/test_dify.py` |
| D19 | 演示 | 演示 | 演示脚本 |
| D20 | 终验 | 终验 | 第九节清单 |

### 7.3 4 组要写的代码

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/adapters/dify/dify_adapter.py` | `httpx` | `DifyAdapter(base_url, api_key)`；`chat()`、`chat_stream()`、`run_workflow()`、`run_workflow_stream()`、`upload_document()`、`list_documents()`、`delete_document()`；统一 `_request()` 错误映射与重试 |
| `app/adapters/dify/sse.py` | **新增** | Dify SSE 事件解析器（`event/data` 行），产出 `async Iterator[dict]` |
| `app/config/settings.py` | 修改 | 补 `DIFY_APP_ID_CHAT/DIFY_APP_ID_RESUME/DIFY_DATASET_ID/DIFY_TIMEOUT/DIFY_MAX_RETRIES` |
| `app/infrastructure/database/health.py` 或 `app/adapters/dify/health.py` | **新增** | `check_dify()` 返回与 `check_mysql` 同构的 `{status, reason, message, elapsed_ms}` |
| `app/routers/agent_router.py` | 共用 | SSE 转发（与 2 组同文件，PR 需 2 组 review） |
| `scripts/sync_dify_kb.py` | **新增** | 本地文档 → Dify dataset 同步 |
| `tests/test_dify.py` | — | `httpx.MockTransport` 全量 mock，不打真网 |

---

## 八、跨组契约（S0 冻结，改动需架构组签字）

```python
# 1) LLM 契约 —— app/adapters/llm/base_llm.py
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

class LLMResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int
    raw: dict | None

class BaseLLM(ABC):
    async def chat(self, messages: list[ChatMessage], *, temperature: float = 0.3,
                   max_tokens: int | None = None) -> LLMResult: ...
    async def generate(self, prompt: str, **kw) -> LLMResult: ...
    async def stream(self, messages: list[ChatMessage], **kw) -> AsyncIterator[str]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

# 2) 上下文契约 —— app/ai/context.py
@dataclass
class AIContext:
    user_id: int | None
    roles: list[str]
    organization_id: int | None
    trace_id: str
    conversation_id: str | None
    extra: dict

def set_context(user, **extra) -> AIContext: ...
def get_context() -> AIContext: ...          # 未设置返回匿名，不抛异常
def clear_context() -> None: ...

# 3) Skill 契约 —— app/ai/skills/base.py
class BaseSkill(ABC):
    name: str
    description: str
    parameters: dict                         # JSON Schema
    async def execute(self, **kwargs) -> Any: ...
    def to_openai_tool(self) -> dict: ...    # {"type": "function", "function": {...}}

# 4) Agent 契约 —— app/ai/agents/base_agent.py
class BaseAgent(ABC):
    name: str
    system_prompt: str
    def __init__(self, db: AsyncSession, context: AIContext | None = None): ...
    async def build_prompt(self, payload: dict) -> list[ChatMessage]: ...
    async def parse_output(self, raw: str) -> dict: ...
    async def persist(self, result: dict, payload: dict) -> Any: ...
    async def run(self, payload: dict) -> dict: ...

# 5) 向量库契约 —— app/adapters/vector_store/milvus_adapter.py
class BaseVectorStore(ABC):
    async def create_collection(self, name: str, dim: int) -> None: ...
    async def upsert(self, collection: str, ids: list[str],
                     vectors: list[list[float]], metadata: list[dict]) -> None: ...
    async def search(self, collection: str, vector: list[float],
                     top_k: int = 10, expr: str | None = None) -> list[dict]: ...
    async def delete(self, collection: str, ids: list[str] | None = None,
                     expr: str | None = None) -> None: ...
    async def drop(self, collection: str) -> None: ...

# 6) NL2SQL 契约 —— app/adapters/nl2sql/vanna_adapter.py
class NL2SQLResponse:
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    chart: dict | None                       # {type, x_field, y_field, title, series}
    insight: str | None
    elapsed_ms: int
    cached: bool
```

**SSE 统一格式**（2 组 / 4 组 / 1 组流式问答共用）：

```
event: message
data: {"delta": "根据《职级晋升管理办法》"}

event: citation
data: {"doc_id": 3, "doc_title": "职级晋升管理办法", "page": 2}

event: done
data: {"conversation_id": "c-20261103-0001", "elapsed_ms": 1830}

event: error
data: {"code": 5002, "message": "模型调用超时，请重试"}
```

---

## 九、统一验收清单（D20 每组逐项打勾）

- [ ] 新增/修改文件都在自己组的分工表内，跨组文件已由对应组 review；
- [ ] 每个文件开头有中文模块注释（职责 / 思路 / 对外方法 / 待办）；
- [ ] 无裸 SQL、无硬编码密钥与地址、无 `print`、无 `raise Exception`；
- [ ] 外部依赖（LLM / Milvus / Redis / Dify / Vanna）关闭时服务可启动，接口返回 5001/5002 而非 500；
- [ ] 接口已挂 `require_permission`，越权返回 403/2004；
- [ ] 所有列表接口统一分页，`page_size` 上限 200；
- [ ] `pytest -q` 全绿（无 MySQL/Milvus 环境应 skip 而非 fail）；
- [ ] `/docs` 中接口有中文 summary、示例、错误说明；
- [ ] 关键路径有 trace_id 与耗时日志，可定位到具体组；
- [ ] 演示脚本 `scripts/demo_ai.py` 中本组链路可一键跑通；
- [ ] 文档已更新（本计划 + `team_code_spec.md` + `公共层使用手册` AI 章节）。

## 十、风险与预案

| 风险 | 影响 | 预案 |
| --- | --- | --- |
| 大模型额度/限流 | 各组联调阻塞 | 架构组 D3 提供 `ollama_adapter` 本地兜底 + mock LLM 测试夹具 |
| Milvus 资源不足 | RAG 无法演示 | 降级为内存向量检索（`MILVUS_ENABLED=false` 时走 numpy 余弦） |
| Vanna 准确率不达标 | NL2SQL 体验差 | 保留「LLM 直出 + 校验器」备用通路；扩大 few-shot |
| LangGraph 版本 API 变动 | 工作流重写 | `graph_builder` 做薄封装，业务工作流不直接 import langgraph 内部 API |
| Dify 与自建能力重叠 | 重复建设 | D1–D2 明确边界并签字（Dify 管 Prompt/低代码发布，自建管业务 Agent 与 RAG 精度） |
| 5 组并行改同一文件 | 冲突频繁 | 文件级分工表 + 每日站会 + 跨组文件 PR 强制 review |

---

## 十一、每日站会与产出节奏

| 时间 | 内容 | 产出 |
| --- | --- | --- |
| 每日 09:30（15 min） | 昨天完成 / 今天计划 / 阻塞项 | 阻塞项当天指派责任人 |
| 每周五 16:00 | 集成日：合并、跑全量测试、更新 `docs/team_code_spec.md` 状态列 | 集成报告 |
| D12 / D20 | 大节点验收 | 报告 + 演示 |

**Git 约定**（沿用 `docs/development.md`）：分支 `feature/<组>-<模块>`；提交 `feat(rag): 新增混合检索`；PR 必须写「改了哪些文件 / 动了哪些表 / 是否需要其他人同步改 Schema」。
