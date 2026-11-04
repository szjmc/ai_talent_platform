# 六大研发组职责边界与防重复开发总规范

> **文档版本**：v2.0.0（强制执行版） ｜ **更新日期**：2026-11-04
> **适用团队**：`[架构组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]`、`[业务组]`
> **核心目标**：**一个功能全平台只写一次、一个文件有且仅有一个主责组、已完成代码严禁二次重写、跨组能力一律走公开契约调用**。
> **配套文档**：[需求规格说明书](requirements_specification.md) ｜ [系统总体与详细设计文档](system_design_document.md) ｜ [代码标签与优先级总表](plan/05-代码标签与优先级总表.md) ｜ [API 接口文档](api_document.md)

---

## 目录

1. [全局防重复开发十条铁律（PR 一票否决红线）](#一全局防重复开发十条铁律pr-一票否决红线)
2. [已落地冻结资产总表（直接调用，严禁任何组重写！）](#二已落地冻结资产总表直接调用严禁任何组重写)
3. [九大高频跨组重叠场景（Hotspots）严格切分裁定](#三九大高频跨组重叠场景hotspots严格切分裁定)
4. [四库（MySQL / PgSQL / Milvus / Redis）按组读写权限边界矩阵](#四四库mysql--pgsql--milvus--redis按组读写权限边界矩阵)
5. [六大研发组逐组详细代码开发说明书](#五六大研发组逐组详细代码开发说明书)
   - [5.0 架构组 `[架构组]`（已全部交付冻结 ✅）](#50-架构组-架构组已全部交付冻结-)
   - [5.1 1 组：RAG 组 `[RAG组]`](#51-1-组rag-组-rag组)
   - [5.2 2 组：Agent 组 `[Agent组]`](#52-2-组agent-组-agent组)
   - [5.3 3 组：Vanna 组 `[Vanna组]`](#53-3-组vanna-组-vanna组)
   - [5.4 4 组：Dify 组 `[Dify组]`](#54-4-组dify-组-dify组)
   - [5.5 5 组：业务组 `[业务组]`](#55-5-组业务组-业务组)
6. [新增文件与跨组变更合规守门规则](#六新增文件与跨组变更合规守门规则)

---

## 一、全局防重复开发十条铁律（PR 一票否决红线）

1. **禁止重写大模型调用客户端**：全平台只有 `[架构组]` 的 `app/ai/core/llm_manager.py`（`llm_manager.chat / stream / embed`）允许调用大模型 API。其余 5 组**严禁**自己 `httpx.post("/chat/completions")`、自己 `import openai` 或 `import dashscope`。
2. **禁止重写向量库客户端与 Embedding**：全平台只有 `[RAG组]` 的 `MilvusAdapter` 允许 `import pymilvus`，只有 `EmbeddingService` 允许封装文本向量化与缓存。`[业务组]` 和 `[Agent组]` 做人才/岗位/题库向量检索或查重时，**必须调用 `[RAG组]` 的 `EmbeddingService` + `MilvusAdapter`**。
3. **禁止重写文档文本提取器**：PDF、Word、Excel、TXT、Markdown、扫描件的文本提取统一由 `[RAG组]` 在 `app/ai/rag/document_loader.py` 实现一次。`[Agent组]` 的简历解析工作流与 `[业务组]` 的 JD 解析**必须直接调用 `document_loader.load(file_path)`**，严禁另写第二套 `pdfplumber` / `python-docx` 解析代码。
4. **禁止重写人岗匹配打分公式**：四维加权打分算法（`compute_match_score`）与三大匹配策略（`HardFilterStrategy / SoftMatchStrategy / HybridStrategy`）**已由 `[架构组]` 在 `app/ai/skills/matching_skill.py` 与 `app/patterns/strategy.py` 完整实现**。`[业务组]` 的 `MatchingService` 和 `[Agent组]` 的 `matching_workflow` **必须直接调用**，严禁各写一套打分逻辑导致分数不一致！
5. **禁止重写技能标签抽取、输出 JSON 解析、重试熔断、会话记忆与状态管理**：`extract_skills`（`resume_skill.py`）、`parse_json / parse_model`（`output_parser.py`）、`ai_retry / SimpleCircuitBreaker`（`retry_handler.py`）、`ConversationMemory`（`conversation_memory.py`）、`StateManager`（`state_manager.py`）**全部已由 `[架构组]` 实现完毕**，其余各组只准 `import` 使用，严禁覆盖或另建同类工具！
6. **禁止重写通用 CRUD 与分页/响应/权限**：所有 MySQL 仓储必须继承 `BaseRepository`（自带 17 个异步 CRUD/分页/软删/乐观锁方法），禁止在子 Repository 重复写 `get_by_id`、`create`、`paginate`；所有接口返回必须用 `app.core.response.success`，异常必须抛 `BusinessException` 子类。
7. **禁止重写物理文件读写与哈希工具**：磁盘读写统一调用 `[架构组]` 的 `get_storage()`（`app/adapters/storage/base_storage.py`），文件名清洗与 SHA-256 统一调用 `app/utils/file_utils.py`，严禁业务代码直接 `open(..., "wb")` 或 `os.remove()`。
8. **禁止 AI 层直触数据库与 ORM 明细**：`[Agent组]`、`[RAG组]`（除自身 `knowledge_repository` 外）、`[Dify组]` **严禁编写 SQL 或直接查询其他域的 ORM 表**，取数与落库一律通过对应模块的公开 `Service` 方法或 `[架构组]` 已注册的 8 个 `Skills`。
9. **禁止多头建设 NL2SQL**：全平台自然语言转 SQL 与 PgSQL 分析库查询由 `[Vanna组]` 的 `sql_validator.py` + `vanna_adapter.py` **唯一实现**；Agent 需要查统计数据时只能调 `sql_query` 技能或 `AnalyticsService`，Dify 应用需要查数据时只能调平台 `/api/v1/analytics/nl2sql` 接口。
10. **禁止无标签新增文件或私改他组文件**：每个代码文件头部已有明确 `标签：[xx组] [Px]`，严禁跨组私自修改非本组文件；如需新增 `.py` 文件，模块 docstring 必须包含合法 `标签：[本组] [P0|P1|P2]` 并通过 `pytest tests/test_code_tags.py -q`。

---

## 二、已落地冻结资产总表（直接调用，严禁任何组重写！）

在动手写任何一行代码前，请先查阅下表——**以下能力均已 100% 编码落地并通过测试**：

| 能力类别 | 已实现文件路径 | 暴露的核心类 / 函数（直接 `import` 使用） | 哪些组必须直接复用 |
| --- | --- | --- | --- |
| 响应与分页 | `app/core/response.py`、`app/core/pagination.py` | `success()`, `PageParams`, `PageResult`, `build_page_result()` | 全部组（Router / Service） |
| 异常体系 | `app/core/exceptions.py` | `ParamError(1001)`, `BusinessRuleError(1002)`, `NotFoundError(3001)`, `DuplicateError(3002)`, `DataConflictError(3003)`, `ExternalServiceError(5001)`, `AIError(5002)`, `FileError(5003)` | 全部组 |
| 认证与 RBAC | `app/core/dependencies.py`、`app/core/permissions.py` | `get_db`, `get_current_user`, `require_permission`, `PermissionEnum`, `get_data_scope`, `check_data_scope` | 全部组 |
| MySQL 会话与事务 | `app/infrastructure/database/session.py`、`transaction.py` | `get_db()`（Router 注入）、`session_scope()`（后台/脚本/Skill）、`transaction()`（嵌套事务+死锁重试） | `[业务组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]` |
| PgSQL 会话与 6 表 ORM | `app/infrastructure/database/postgres.py`、`app/models/analytics.py`、`analytics_schema.py` | `pg_session()`（只读查询）、`get_pg_session_factory()`（ETL 写入）、`DimOrganization`, `DimTalentWide`, `Fact*` | `[Vanna组]` |
| Redis 异步缓存 | `app/infrastructure/cache/redis.py` | `get_json(key)`, `set_json(key, val, ttl)`, `delete_keys(pattern)`, `incr_with_ttl(key, ttl)` | 全部组 |
| 25 张业务表 ORM | `app/models/{user,role,organization,position,talent,resume,matching,assessment,training,knowledge,conversation,file,audit}.py` | 全部 25 个 SQLAlchemy ORM 类（已注册进 `app/models/__init__.py`） | `[业务组]`、`[RAG组]`、`[Agent组]`（**严禁重建 Model**） |
| 通用 CRUD 基类 | `app/repositories/base.py` | `BaseRepository`（`get_by_id`, `get_by_id_or_404`, `paginate`, `list`, `create`, `create_many`, `update`, `delete`, `exists`, `count`, `check_version`） | `[业务组]`、`[RAG组]`、`[Agent组]` |
| 已完成 3 大业务模块 | `app/{repositories,schemas,services,routers}/{auth,user,organization}*` | 登录鉴权、用户管理、组织树与部门防环移动（**作为 `[业务组]` 标准参考样板**） | `[业务组]` 维护，其余组只读调用 |
| AI 安全上下文 | `app/ai/context.py` | `AIContext`, `set_context()`, `get_context()`, `clear_context()`, `context_scope()` | 全部 AI 组 + `[业务组]` |
| 大模型统一门面 | `app/ai/core/llm_manager.py`、`token_manager.py` | `llm_manager.chat()`, `llm_manager.stream()`, `llm_manager.embed()`, `TokenManager` | `[RAG组]`、`[Agent组]`、`[Vanna组]` |
| AI JSON 解析与重试 | `app/ai/utils/output_parser.py`、`retry_handler.py` | `parse_json()`, `parse_model()`, `ai_retry()`, `SimpleCircuitBreaker` | `[Agent组]`、`[RAG组]`、`[Vanna组]`、`[Dify组]` |
| Prompt 版本仓库 | `app/config/prompt_config.py`、`app/ai/prompts/*_prompt.py` | `prompt_registry.get(name)`, `prompt_registry.render(name, **kw)` | `[Agent组]`、`[Dify组]` |
| 8 大标准 Skills | `app/ai/skills/{base,knowledge_skill,matching_skill,resume_skill,sql_skill,registry_bootstrap}.py` | `get_skill()`, `list_tools()`, `compute_match_score()`, `extract_skills()`, `search_knowledge()`, `query_database()` | `[Agent组]`、`[业务组]` |
| 人岗匹配三策略 | `app/patterns/strategy.py` | `MatchStrategyFactory.create("hard" \| "soft" \| "hybrid")`, `HardFilterStrategy`, `SoftMatchStrategy`, `HybridStrategy` | `[业务组]`、`[Agent组]` |
| Agent 基类与调度 | `app/ai/agents/base_agent.py`、`app/ai/agent.py` | `BaseAgent`, `@register_agent`, `dispatch()`, `dispatch_stream()`, `AGENT_TYPES` | `[Agent组]`、`[业务组]` |
| 会话记忆与图状态 | `app/ai/memory/conversation_memory.py`、`state_manager.py` | `ConversationMemory`, `StateManager`, `state_manager`, `to_jsonable()` | `[Agent组]` |
| 文件存储与文件工具 | `app/adapters/storage/base_storage.py`、`app/utils/file_utils.py` | `get_storage()`, `safe_filename()`, `sha256_bytes()`, `validate_extension()` | `[业务组]`、`[RAG组]`、`[Vanna组]` |
| 进程内异步事件总线 | `app/infrastructure/messaging/event_bus.py` | `event_bus.subscribe()`, `event_bus.publish()`, `event_bus.publish_await()` | `[业务组]`（审计解耦） |

---

## 三、九大高频跨组重叠场景（Hotspots）严格切分裁定

| 重叠场景 | 唯一实现归属（谁写核心逻辑） | 协同调用方（怎么调） | 严禁越界红线（绝对不允许出现的情况） |
| --- | --- | --- | --- |
| **① 人岗匹配打分与排序** | `[架构组]` 已实现公式 `compute_match_score` 与 `MatchStrategyFactory`；`[业务组]` `MatchingService` 负责候选池取数、落库 `matching_records` 与批次接口 | `[Agent组]` `matching_agent` 只负责生成 AI 文字解释；`matching_workflow` 编排调用 `MatchingService` + `MatchingAgent` | 严禁 `[业务组]` 或 `[Agent组]` 再写一套加权打分公式或硬性条件判断函数 |
| **② 简历上传、文件解析、打标与查重** | `[业务组]` `ResumeService` 管上传秒传、状态机、落库、绑定人才、向量查重编排；`[RAG组]` `document_loader` 管文件转文本；`[Agent组]` `resume_workflow` + `resume_agent` 管 LLM 结构化抽取 | `resume_workflow` 调 `document_loader.load()` 取文本、调 `[架构组] extract_skills()` 补标签、调 `ResumeService` 写库 | 严禁 `[Agent组]` 自己写 PDF/Word 解析器；严禁 `[业务组]` 在 `ResumeService` 里直接拼 Prompt 调 LLM |
| **③ Milvus 4 大向量集合操作** | `[RAG组]` 独占实现 `infrastructure/vector/milvus.py`、`MilvusAdapter` 与 `EmbeddingService` | `[RAG组]` 写/查 `knowledge_vectors`；`[业务组]` 在 `TalentService / PositionService / AssessmentService` 中调用 `EmbeddingService` + `MilvusAdapter` 维护其余 3 个集合 | 严禁 `[业务组]` 或 `[Agent组]` 直接 `import pymilvus` 或自建向量连接池 |
| **④ 制度知识问答 vs 智能体助手问答 vs Dify 知识库** | `[RAG组]` 独占实现 `/api/v1/knowledge/*` 与 `RAGPipeline`（单领域严谨制度问答）；`[Agent组]` 实现 `/api/v1/agents/chat*` 与 `AssistantAgent`（多技能综合路由助手） | `AssistantAgent` 遇到制度问题时调用 `knowledge_search` 技能（底层走 `[RAG组] Retriever`）；`[Dify组]` 仅做单向同步脚本把本地文档镜像至 Dify Dataset | 严禁 `[Agent组]` 直查 `knowledge_chunks` 表；严禁 `[Dify组]` 接管或重写 `/api/v1/knowledge/*` |
| **⑤ 自然语言问数 NL2SQL vs 统计看板 vs 决策工作流** | `[Vanna组]` 独占实现 `sql_validator`、`vanna_adapter`、`AnalyticsService`、`/api/v1/analytics/*` 与 PgSQL ETL | `[Agent组]` 的 `analysis_workflow`（人才数据决策工作流）通过调用 `sql_query` 技能与 `AnalyticsService` 获取统计结果，再用 LLM 生成管理汇报建议 | 严禁 `[Agent组]` 或 `[Dify组]` 自行生成 SQL 或直连 `pg_session()` |
| **⑥ Dify 中台 vs 本地 Agent / SSE 路由** | `[Dify组]` 只写 `app/adapters/dify/*` 与同步脚本；`[Agent组]` 拥有 `agent_router.py` 与 `agent_service.py` 主体 | 在 `AgentService` 内部预留唯一配置开关分支：配了 `DIFY_APP_ID_*` 调 `DifyAdapter`，未配走本地 `dispatch / dispatch_stream` | 严禁 `[Dify组]` 新建 `/api/v1/dify/*` 重复业务路由，严禁改写 `BaseAgent` |
| **⑦ 人才画像 / 测评判分 / 培训推荐的“规则 vs AI”切分** | `[业务组]` 负责主档 CRUD、客观题（单选/多选）规则判分、防超时/防刷课校验与 REST API；`[Agent组]` 负责 `talent_agent`、`assessment_agent`、`training_agent` 的大模型推理生成 | `[业务组]` Service 完成确定性事务后，通过 `await dispatch("talent" \| "assessment" \| "training", payload, db=self.db)` 触发对应 Agent | 严禁 `[Agent组]` 写客观题计分或学习进度校验；严禁 `[业务组]` 在 Service 里手写 Prompt 调大模型 |
| **⑧ 多轮对话记忆 vs 会话历史持久化** | `[架构组]` 已实现 `ConversationMemory`（Redis 窗口+摘要+自动回调仓储）；`[Agent组]` 仅实现 `ConversationRepository` | `AgentService` 调 `ConversationMemory` 管理上下文，调 `ConversationRepository` 提供会话列表/历史分页查询接口 | 严禁 `[Agent组]` 重写或修改 `app/ai/memory/conversation_memory.py` 与 `state_manager.py` |
| **⑨ 物理文件存储与全局审计日志** | `[架构组]` 已实现物理存储 `get_storage()` 与事件总线 `EventBus`；`[业务组]` 实现 `FileService`（`files` 元数据+鉴权下载）与 `AuditService`（`audit_logs` 入库+查询） | 其他组需要存业务导出文件（如 `[Vanna组]` 导出 PDF/Word 报告）统一调 `FileService`；记审计统一调 `AuditService.log()` 或发 `EventBus` 事件 | 严禁其他组直写 `files` 表或 `audit_logs` 表 |

---

## 四、四库（MySQL / PgSQL / Milvus / Redis）按组读写权限边界矩阵

> 图例：`R/W` = 唯一允许通过本组 Repository/Adapter 读写的归属组 ｜ `Via Service/Skill` = 仅允许通过主责组公开方法间接访问 ｜ `❌` = 严禁直接触碰

| 存储库与对象 | `[架构组]` | `[RAG组]` | `[Agent组]` | `[Vanna组]` | `[Dify组]` | `[业务组]` |
| --- | --- | --- | --- | --- | --- | --- |
| **MySQL**：`users / roles / user_roles / organizations` | 只读（鉴权注入） | Via `AIContext` | Via `UserService` | ETL 只读抽取 | ❌ | **R/W（✅已实现）** |
| **MySQL**：`positions / position_requirements` | Via Skill 惰性调 | ❌ | Via `PositionService` / Skill | ETL 只读抽取 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`talents / talent_portraits` | ❌ | ❌ | Via `TalentService` | ETL 只读抽取 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`files / resumes / resume_experiences` | Via Skill 惰性调 | Via `FileService` 读原文件 | Via `ResumeService` / Skill | ❌ | ❌ | **R/W（唯一归属）** |
| **MySQL**：`matching_records` | Via Skill 惰性调 | ❌ | Via `MatchingService` / Skill | ETL 只读抽取 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`assessments / *_questions / *_answers / *_results` | ❌ | ❌ | Via `AssessmentService` | ETL 只读抽取 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`courses / training_plans / plan_courses / learning_records` | ❌ | ❌ | Via `TrainingService` | ETL 只读抽取 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`audit_logs` | ❌ | Via `EventBus` | Via `EventBus` | Via `EventBus` + ETL 只读 | ❌ | **R/W（唯一归属）** |
| **MySQL**：`knowledge_docs / knowledge_chunks` | Via `knowledge_search` | **R/W（唯一归属）** | Via `knowledge_search` 技能 | ❌ | 只读同步脚本 | ❌ |
| **MySQL**：`conversations / chat_messages` | `ConversationMemory` 回调 | ❌ | **R/W（唯一归属）** | ❌ | ❌ | ❌ |
| **PgSQL**：6 张分析表（`dim_*` / `fact_*`） | 引擎与建表已交付 | ❌ | Via `sql_query` / `AnalyticsService` | **ETL 写 + 查询只读（唯一归属）** | ❌ | ❌ |
| **Milvus**：`knowledge_vectors` | 初始化脚本已交付 | **R/W（唯一归属）** | Via `knowledge_search` | ❌ | ❌ | ❌ |
| **Milvus**：`talent_vectors / position_vectors / question_vectors` | 初始化脚本已交付 | **提供 `MilvusAdapter` 底层能力** | Via 业务 Service / Skill | ❌ | ❌ | **业务触发 R/W（调 `MilvusAdapter`）** |
| **Redis**：各业务命名空间 | `ai:token:*` / `chat:*` / `wf:*` | `rag:*` | 复用架构组 `Memory/State` | `nl2sql:*` | ❌ | `org:*` / `talent:*` |

---

## 五、六大研发组逐组详细代码开发说明书

---

### 5.0 架构组 `[架构组]`（已全部交付冻结 ✅）

- **当前状态**：本组负责的公共层、配置中心、四库连接池、LLM 适配器与管理器、AI 上下文、输出解析与重试熔断、8 大 Skills 组件、`BaseAgent` 与 `dispatch`、`ConversationMemory` 与 `StateManager`、`MatchStrategyFactory`、`BaseStorage`、`EventBus` 及运维/守门测试共 **100 个文件已全部实现并通过验收**。
- **本组后续职责边界**：
  1. **契约守护与统一配置变更**：维护 `app/config/settings.py`、`.env.example`、`requirements.txt`、`app/routers/router.py`（各组完成模块后提 PR 将 `_STUB_MODULES` 占位替换为真实 router，由架构组合并）。
  2. **跨组联调与守门**：维护 `tests/conftest.py`、`tests/test_code_tags.py`、`scripts/demo_ai.py`，执行跨组 PR Code Review（重点拦截越层 SQL、重复造轮子、越权取参）。
- **本组严禁事项**：架构组不做具体业务 CRUD、不写具体 RAG 切片检索实现、不写具体 LangGraph 业务节点、不写 Vanna SQL 校验实现。

---

### 5.1 1 组：RAG 组 `[RAG组]`

#### A. 本组核心定位与严格边界

- **本组要做的事**：① 全平台唯一的 Milvus 连接与向量库适配器（`milvus.py` + `milvus_adapter.py`）；② 全平台唯一的文本向量化封装（`embedding_service.py`）；③ 全平台唯一的多格式文档加载与切分器（`document_loader.py` + `document_splitter.py`）；④ 混合检索器与无幻觉知识问答全链路（`retriever.py` + `rag_pipeline.py` + `knowledge_repository.py` + `knowledge_service.py` + `schemas/knowledge.py` + `knowledge_router.py`）。
- **本组严禁做的事（防重复红线）**：
  - ❌ **严禁**新建或修改 `app/models/knowledge.py`（`KnowledgeDoc` 与 `KnowledgeChunk` ORM 模型已完整定义，直接使用）；
  - ❌ **严禁**修改 `app/ai/skills/knowledge_skill.py`（架构组已实现完毕，只要本组 `Retriever.hybrid_search` 按约定签名实现即自动接通）；
  - ❌ **严禁**自己写 HTTP 客户端调用 OpenAI/DashScope `/embeddings` 或 `/chat/completions`（必须调用 `app.ai.core.llm_manager.llm_manager`）；
  - ❌ **严禁**在代码中直接读写本地磁盘路径而不经过 `get_storage()` / `FileService`。

#### B. 本组逐文件详细开发清单（共 11 个待开发代码文件 + 单测）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/infrastructure/vector/milvus.py`<br>`[RAG组] [P0]` | - `connect_milvus() -> None`<br>- `disconnect_milvus() -> None`<br>- `check_milvus() -> dict[str, Any]`<br>- `get_collection(name: str)` | 封装 `pymilvus.connections` 惰性连接；`settings.MILVUS_ENABLED=False` 或未装 `pymilvus` 时 `check_milvus()` 返回 `{"status": "disabled"\|"down", "reason": ..., "message": ...}`（对齐 `health.py` 结构），绝不抛未捕获异常阻塞应用启动。 |
| 2 | `app/adapters/vector_store/milvus_adapter.py`<br>`[RAG组] [P0]` | - `class BaseVectorStore(ABC)`<br>- `class MilvusAdapter(BaseVectorStore)`：<br>  - `async def ensure_collections(self, dim: int \| None = None) -> None`<br>  - `async def upsert(self, collection: str, ids: list[int], vectors: list[list[float]], scalars: list[dict[str, Any]]) -> int`<br>  - `async def search(self, collection: str, vector: list[float], *, top_k: int = 10, expr: str \| None = None, output_fields: list[str] \| None = None) -> list[dict[str, Any]]`<br>  - `async def delete(self, collection: str, *, ids: list[int] \| None = None, expr: str \| None = None) -> int` | **全平台 4 个集合（`knowledge_vectors` / `talent_vectors` / `position_vectors` / `question_vectors`）的唯一读写网关**（集合 Schema 严格复用 `scripts/init_milvus.py` 中的 `COLLECTIONS` 定义，主键均为 `INT64`）。由于 `pymilvus` 部分调用为同步阻塞，内部使用 `asyncio.to_thread` 包装以保持 FastAPI 事件循环非阻塞；`MILVUS_ENABLED=False` 时抛 `ExternalServiceError("Milvus 向量库未启用")`。 |
| 3 | `app/ai/rag/embedding_service.py`<br>`[RAG组] [P0]` | - `class EmbeddingService`：<br>  - `async def embed_texts(self, texts: list[str], *, batch_size: int = 16) -> list[list[float]]`<br>  - `async def embed_query(self, query: str) -> list[float]` | 内部统一调用 `await llm_manager.embed(batch)`；对 `embed_query` 使用 Redis（Key `rag:emb:{model}:{sha256}`）+ 进程内有界字典缓存；校验返回向量维度与 `settings.EMBEDDING_DIM` 一致。同时供 `[业务组]` 生成人才/岗位/试题向量调用。 |
| 4 | `app/ai/rag/document_loader.py`<br>`[RAG组] [P1]` | - `@dataclass class LoadedDocument: content: str; metadata: dict[str, Any]`<br>- `def load(file_path: str \| Path) -> list[LoadedDocument]`<br>- `def load_bytes(data: bytes, filename: str) -> list[LoadedDocument]` | 支持 `.pdf`（`pdfplumber` 逐页提取，`metadata["page"]=页码`）、`.docx`（`python-docx`）、`.xlsx`（`openpyxl`）、`.txt`/`.md`（`utf-8 → gbk` 编码兜底）。不支持扩展名抛 `FileError("不支持的文档格式")`。**同时作为 `[Agent组]` 简历解析的唯一文本提取入口**。 |
| 5 | `app/ai/rag/document_splitter.py`<br>`[RAG组] [P1]` | - `def split_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]`<br>- `def split_by_headings(text: str) -> list[tuple[str, str]]`<br>- `def split_documents(docs: list[LoadedDocument], chunk_size: int = 512, overlap: int = 64) -> list[LoadedDocument]` | 中文优先按 `\n\n`、句末标点 `。！？；` 切分，避免截断完整句子；制度类文档识别 `^第[一二三四五六七八九十百]+[章节条]` 与 Markdown `#` 标题写入 `metadata["heading"]` 与 `metadata["chunk_index"]`。 |
| 6 | `app/repositories/knowledge_repository.py`<br>`[RAG组] [P0]` | `class KnowledgeRepository(BaseRepository[KnowledgeDoc])`：<br>- `async def get_by_hash(self, db, file_hash: str) -> KnowledgeDoc \| None`<br>- `async def list_accessible_docs(self, db, page: PageParams, *, scope: dict, category: str \| None = None, status: str \| None = None, keyword: str \| None = None)`<br>- `async def save_chunks(self, db, doc_id: int, chunks: list[dict]) -> list[KnowledgeChunk]`<br>- `async def get_chunks_by_ids(self, db, chunk_ids: list[int]) -> list[KnowledgeChunk]`<br>- `async def search_chunks_by_keyword(self, db, query: str, *, scope: dict, top_k: int = 10) -> list[tuple[KnowledgeChunk, KnowledgeDoc]]`<br>- `async def delete_chunks_by_doc(self, db, doc_id: int) -> int`<br>- `async def update_index_status(self, db, doc_id: int, *, status: str, chunk_count: int = 0, error: str \| None = None)` | 继承 `BaseRepository[KnowledgeDoc]`，操作 `KnowledgeDoc` 与 `KnowledgeChunk` 两张表；`search_chunks_by_keyword` 只检索 `KnowledgeDoc.status == "indexed"` 且 `is_deleted == False` 且满足当前用户 `permission_scope`（`public` / 本部门 `dept` / 命中 `allowed_roles` 的 `role`）的切片。 |
| 7 | `app/ai/rag/retriever.py`<br>`[RAG组] [P0]` | - `@dataclass class RetrievedChunk: content: str; doc_id: int; doc_title: str; page: int \| None; chunk_index: int; milvus_pk: str \| None; score: float`<br>- `class Retriever`：<br>  - `def __init__(self, db: AsyncSession \| None = None, store: MilvusAdapter \| None = None, embedder: EmbeddingService \| None = None)`<br>  - `async def semantic_search(self, question: str, *, top_k: int = 10, scope: dict \| AIContext \| None = None) -> list[RetrievedChunk]`<br>  - `async def keyword_search(self, question: str, *, top_k: int = 10, scope: dict \| AIContext \| None = None) -> list[RetrievedChunk]`<br>  - `async def hybrid_search(self, question: str, *, top_k: int = 10, scope: dict \| AIContext \| None = None, use_rerank: bool = False) -> list[RetrievedChunk]` | **关键契约对齐**：`app/ai/skills/knowledge_skill.py` 会无参实例化 `Retriever()` 并调用 `await retriever.hybrid_search(question, top_k=top_k, scope=scope)`（其中 `scope` 为包含 `user_id, roles, organization_id, data_scope, category` 的字典；若 `scope is None` 则内部调 `get_context()`）；当 `self.db is None` 时内部使用 `async with session_scope() as db:` 回表取 `doc_title`。构造 Milvus 前置过滤 `expr` 保障零越权召回，采用 RRF（`1/(60+rank)`）融合语义与关键词结果。 |
| 8 | `app/ai/rag/rag_pipeline.py`<br>`[RAG组] [P1]` | `class RAGPipeline`：<br>- `async def index_document(self, db: AsyncSession, doc_id: int) -> dict[str, Any]`<br>- `async def ask(self, db: AsyncSession, question: str, *, top_k: int = 5, conversation_id: str \| None = None) -> dict[str, Any]`<br>- `async def ask_stream(self, db: AsyncSession, question: str, *, top_k: int = 5) -> AsyncIterator[dict[str, Any]]` | `index_document` 完成加载→切分→Embedding→写 `knowledge_chunks`→写 `knowledge_vectors`→更新文档状态（异常捕获写 `index_error`，置 `status="failed"`）。`ask` 在检索为空时**禁止调 LLM**，直接返回 `{"answer": "知识库中未找到依据", "citations": []}`；有命中时带编号调用 `llm_manager.chat(..., purpose="chat")` 返回答案与 `citations`。 |
| 9 | `app/schemas/knowledge.py`<br>`[RAG组] [P1]` | `KnowledgeTextCreate`、`KnowledgeDocResponse`、`KnowledgeRetrieveRequest`、`KnowledgeRetrieveResponse`、`KnowledgeAskRequest`、`KnowledgeAskResponse`、`CitationItem`、`RetrieveEvalResponse` | 字段严格对齐 `docs/api_document.md` 第十五节。 |
| 10 | `app/services/knowledge_service.py`<br>`[RAG组] [P0]` | `class KnowledgeService`：<br>- `create_text_doc`、`upload_document`、`index_document`、`page_list`、`delete_document`、`retrieve`、`ask`、`retrieve_eval` | `delete_document` 校验 `status != "indexing"`（否则抛 `BusinessRuleError(1002)`），软删数据库记录同时调 `MilvusAdapter.delete("knowledge_vectors", expr=f"doc_id == {doc_id}")` 彻底清理向量。 |
| 11 | `app/routers/knowledge_router.py`<br>`[RAG组] [P1]` | 实现 `/knowledge` 下全部 7 个接口 | 挂 `PermissionEnum.KNOWLEDGE_READ` / `KNOWLEDGE_MANAGE`，完成后提 PR 在 `app/routers/router.py` 替换 `"knowledge"` 占位路由。 |

---

### 5.2 2 组：Agent 组 `[Agent组]`

#### A. 本组核心定位与严格边界

- **⚠️ 最高优先级防重复警告（必读！）**：
  旧版草稿曾将部分底座文件列在 2 组名下，现已由 `[架构组]` **全部开发完毕并通过单测**，2 组**绝对禁止重写或覆盖以下文件**：
  - ✅ `app/ai/agents/base_agent.py`（`BaseAgent`、`@register_agent` 已完成，直接继承！）
  - ✅ `app/ai/agent.py`（`dispatch`、`dispatch_stream`、`AGENT_TYPES` 已完成，直接调用！）
  - ✅ `app/ai/memory/conversation_memory.py`（`ConversationMemory` 已完成，直接使用！）
  - ✅ `app/ai/memory/state_manager.py`（`StateManager`、`state_manager` 已完成，直接使用！）
  - ✅ `app/models/conversation.py`（`Conversation`、`ChatMessage` ORM 已完成，直接使用！）
  - ✅ `app/services/{resume,talent,position,matching,assessment,training}_service.py`（**全部归属 `[业务组]`！2 组只可调用其公开方法，严禁直接编写这些业务 Service！**）
- **本组真正要做的核心代码**：① LangGraph 通用图构建器 `graph_builder.py`；② 3 条有状态业务工作流（`resume_workflow.py`、`matching_workflow.py`、`analysis_workflow.py`）；③ 6 个具体智能体子类（`assistant / resume / talent / matching / assessment / training`）；④ 会话仓储 `conversation_repository.py`；⑤ 智能体调度业务服务 `agent_service.py` 与路由 `agent_router.py`。

#### B. 本组逐文件详细开发清单（共 13 个待开发代码文件 + Schema + 单测）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/ai/workflows/graph_builder.py`<br>`[Agent组] [P0]` | - `class WorkflowState(TypedDict, total=False): thread_id: str; payload: dict; data: dict; errors: list[str]; retries: int; steps: int; status: str`<br>- `def build_graph(nodes: dict[str, Callable], edges: list[tuple[str, str]], *, entry_point: str, conditional_edges: list[tuple[str, Callable, dict[str, str]]] \| None = None)`<br>- `async def run_workflow(graph, initial_state: WorkflowState, *, thread_id: str \| None = None, max_steps: int \| None = None) -> WorkflowState`<br>- `def route_by_status(state: WorkflowState) -> str` | 封装 LangGraph `StateGraph`（若运行环境未安装 `langgraph`，提供轻量级兼容有向图状态机执行器降级，确保离线单测 100% 可跑）；每执行一个节点自动调用 `[架构组] state_manager.save(thread_id, state)` 实现断点持久化，并受 `settings.AGENT_MAX_STEPS` 防死循环保护。 |
| 2 | `app/ai/workflows/resume_workflow.py`<br>`[Agent组] [P0]` | - `async def run_resume_workflow(resume_id: int, file_path: str, *, db: AsyncSession \| None = None, force: bool = False) -> dict[str, Any]` | 5 节点有状态图：`extract`（调 `[RAG组] document_loader.load`）→ `clean` → `structure`（调 `ResumeAgent`）→ `validate`（校验结构化字段，失败且 `retries < 2` 条件边跳回 `structure`，成功叠加 `[架构组] extract_skills`）→ `persist`（调 `[业务组] ResumeService.save_parse_result`，按 `resume_id` 幂等覆盖）。 |
| 3 | `app/ai/workflows/matching_workflow.py`<br>`[Agent组] [P1]` | - `async def run_matching_workflow(position_id: int, *, strategy: str = "hybrid", top_k: int = 20, min_score: float = 60.0, with_ai_explain: bool = True, db: AsyncSession \| None = None) -> dict[str, Any]` | 节点串联：读取岗位（`PositionService`）→ 候选池硬过滤与向量召回打分（复用 `[业务组] MatchingService` + `[架构组] MatchStrategyFactory`）→ Top 候选人并发限流（`Semaphore(5)`）调 `MatchingAgent` 补齐 AI 解释（失败自动退化规则文案）→ 幂等回写批次结果。 |
| 4 | `app/ai/workflows/analysis_workflow.py`<br>`[Agent组] [P2]` | - `async def run_analysis_workflow(topic: str, *, organization_id: int \| None = None, db: AsyncSession \| None = None) -> dict[str, Any]` | **落地需求文档“5. 人才数据决策 Agent”**：节点 `collect_metrics`（调用 `[Vanna组] AnalyticsService.overview/distribution/trend` 及 `[架构组] query_database` 技能，**严禁直连 PgSQL**）→ `talent_review` → `team_structure` → `recommendation`（汇总生成管理层汇报洞察、风险预警与招聘/培养建议）。 |
| 5 | `app/ai/agents/resume_agent.py`<br>`[Agent组] [P0]` | `@register_agent("resume")`<br>`class ResumeAgent(BaseAgent)`（`purpose="resume"`, `prompt_name="resume_parse"`） | 继承 `BaseAgent`：`build_prompt` 截断前 12000 字符简历文本渲染模板；`parse_output` 用 `parse_json` 校验输出包含 `basic_info / education / work_experience / projects / skills / work_years`；`persist` 若传入 `resume_id` 则调 `ResumeService` 回写。 |
| 6 | `app/ai/agents/talent_agent.py`<br>`[Agent组] [P1]` | `@register_agent("talent")`<br>`class TalentAgent(BaseAgent)`（`purpose="chat"`） | 从 `TalentService` 读取人才主档、履历、测评与培训摘要，让 LLM 生成 `{dimensions, labels, strengths, weaknesses, suggested_positions, potential_level, summary}`，`persist` 调 `TalentService.save_portrait(...)` 版本化落库。 |
| 7 | `app/ai/agents/matching_agent.py`<br>`[Agent组] [P1]` | `@register_agent("matching")`<br>`class MatchingAgent(BaseAgent)`（`purpose="matching"`, `prompt_name="matching_evaluate"`） | 输入岗位要求、人才摘要及 `compute_match_score` 算出的确定性四维分数，生成引用真实字段的 `{highlights, gaps, suggestion, explanation}`（**禁止让 LLM 篡改规则算出的确定性总分**）。 |
| 8 | `app/ai/agents/assessment_agent.py`<br>`[Agent组] [P2]` | `@register_agent("assessment")`<br>`class AssessmentAgent(BaseAgent)`（`purpose="assessment"`, `prompt_name="assessment_report"`） | 支持两种 `mode`：① `generate_questions`（按岗位与层级辅助生成题目）；② `evaluate`（对主观题语义打分，并结合客观题维度分生成 `{level, suggestions, report{summary, highlights, gaps}}`），经 `AssessmentService` 落库。 |
| 9 | `app/ai/agents/training_agent.py`<br>`[Agent组] [P2]` | `@register_agent("training")`<br>`class TrainingAgent(BaseAgent)`（`purpose="training"`, `prompt_name="training_recommend"`） | 读取人才画像弱项、测评低分维度与可选课程列表（经 `TrainingService`），生成 `{gap_dimensions, recommendations[{course_id, title, reason, gap_dimension}]}`。 |
| 10 | `app/ai/agents/assistant_agent.py`<br>`[Agent组] [P0]` | `@register_agent("assistant")`<br>`class AssistantAgent(BaseAgent)`（`purpose="chat"`, `prompt_name="assistant_system"`, `persist_result=False`） | 全局多轮智能助手：结合 `ConversationMemory` 历史上下文，根据用户意图编排调用 `[架构组]` 已注册的 8 大 Skills（制度问题调 `knowledge_search`、统计问数调 `sql_query`、找岗位调 `position_search`、算匹配调 `matching_score` 等），汇总工具返回结果生成带 `citations` 的回答，并重写 `stream()` 支持先推 `citation` 再推 `delta`。 |
| 11 | `app/repositories/conversation_repository.py`<br>`[Agent组] [P1]` | `class ConversationRepository(BaseRepository[Conversation])`：<br>- `async def create_conversation(self, db, *, user_id: int, agent_type: str, title: str)`<br>- `async def get_by_session_key_or_id(self, db, conv_id: str \| int, user_id: int)`<br>- `async def list_by_user(self, db, user_id: int, page: PageParams)`<br>- `async def append_message(self, db, payload: dict) -> ChatMessage`<br>- `async def get_recent_messages(self, db, conversation_id: int \| None = None, limit: int = 10) -> list[ChatMessage]`<br>- `async def paginate_messages(self, db, conversation_id: int, page: PageParams)` | **关键契约对齐**：`append_message` 与 `get_recent_messages` 方法名和入参必须兼容 `app/ai/memory/conversation_memory.py` 第 228–258 行的自动回调！查询会话时强制校验 `Conversation.user_id == current_user.id`（管理员除外），越权抛 `PermissionDeniedError(2004)`。 |
| 12 | `app/services/agent_service.py`<br>`[Agent组] [P0]`（含 `[Dify组] [P1]` 分支） | `class AgentService`：<br>- `async def chat(self, req, current_user) -> dict`<br>- `async def chat_stream(self, req, current_user) -> AsyncIterator[dict]`<br>- `async def list_conversations(self, page: PageParams, current_user)`<br>- `async def list_messages(self, conv_id: int, page: PageParams, current_user)`<br>- `async def submit_task(self, req, current_user) -> dict`<br>- `async def get_task_status(self, task_id: str, current_user) -> dict` | 每次入口先调 `set_context(current_user, conversation_id=...)` 与 `TokenManager().check_quota(current_user.id)`；异步任务状态存入 `state_manager`（Key `agent_task:{task_id}`）；若 `settings.DIFY_APP_ID_CHAT` 非空，在 `chat / chat_stream` 中委托 `DifyAdapter`（与 `[Dify组]` 联调分支）。 |
| 13 | `app/routers/agent_router.py`<br>`[Agent组] [P1]`（+ 新增 `app/schemas/agent.py`，标签 `[Agent组] [P1]`） | 实现 `/agents` 下全部 6 个接口（见 `docs/api_document.md` 第十七节） | SSE 响应固定 `StreamingResponse(..., media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`；完成后提 PR 在 `app/routers/router.py` 替换 `"agents"` 占位路由。 |

---

### 5.3 3 组：Vanna 组 `[Vanna组]`

#### A. 本组核心定位与严格边界

- **⚠️ 已完成资产防重复警告**：
  - ✅ `app/models/analytics.py`（PgSQL 6 张分析表 ORM 模型 `DimOrganization / DimTalentWide / Fact*` 已完整实现，**严禁重建**！）
  - ✅ `app/infrastructure/database/analytics_schema.py` 与 `scripts/init_pgsql.py`（DDL 渲染、分区创建与健康检查已完成，**严禁重写**！）
  - ✅ `app/infrastructure/database/postgres.py`（`pg_session()` 与 `get_pg_session_factory()` 已完成，**严禁另建 asyncpg 连接池**！）
  - ✅ `app/ai/skills/sql_skill.py`（`SqlQuerySkill` 已由架构组实现完毕，**严禁修改**！本组只需让 `VannaAdapter.ask` 对齐其调用签名即可自动接通！）
- **本组要做的事**：① AST 级 SQL 安全校验器 `sql_validator.py`（最高安全红线）；② Vanna 训练与查询适配器 `vanna_adapter.py`（含图表推断与 AI 数据解读）；③ MySQL → PgSQL 6 张宽表增量同步脚本 `scripts/sync_analytics.py`；④ 扩充训练语料 `scripts/train_vanna.py`（示例 SQL ≥ 40 条）；⑤ 数据决策服务与接口（`analytics_service.py`、`schemas/analytics.py`、`analytics_router.py`）。

#### B. 本组逐文件详细开发清单（共 6 个核心代码文件 + ETL 脚本 + 单测）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/adapters/nl2sql/sql_validator.py`<br>`[Vanna组] [P0]` | - `ALLOWED_TABLES: frozenset[str]`（严格等于 `ANALYTICS_TABLE_NAMES` 6 张表）<br>- `class SQLValidationError(BusinessRuleError)`（`code=1002`）<br>- `def validate_select_sql(sql: str, allowed_tables: set[str] \| None = None) -> str`<br>- `def inject_data_scope(sql: str, scope_sql: str \| None, *, table_org_col: str = "organization_id") -> str`<br>- `def inject_limit(sql: str, max_rows: int = 200) -> str` | 使用 `sqlglot.parse(sql, read="postgres")` 做 AST 校验：① 仅允许单条 `exp.Select`（含 CTE `WITH`），拒绝任何多语句 `;`、DDL/DML/DCL、危险函数（`pg_sleep`, `pg_read_file`, `dblink`, `lo_import`）；② 遍历全部 `exp.Table`（排除 CTE 别名），表名不在白名单或 schema 为 `pg_catalog`/`information_schema` 立即抛 `SQLValidationError`；③ 返回 `ast.sql(dialect="postgres")` 重新序列化后的纯净 SQL。 |
| 2 | `app/adapters/nl2sql/vanna_adapter.py`<br>`[Vanna组] [P0]` | - `@dataclass class NL2SQLResponse: question: str; sql: str; columns: list[str]; rows: list[list[Any]]; chart: dict[str, Any] \| None = None; insight: str \| None = None; elapsed_ms: int = 0; cached: bool = False`<br>- `class VannaAdapter`：<br>  - `def train(self, corpus: dict[str, Any]) -> dict[str, int]`<br>  - `async def ask(self, question: str, *, user_scope: dict \| str \| None = None, max_rows: int = 200, with_insight: bool = True) -> NL2SQLResponse`<br>  - `def infer_chart(self, columns: list[str], rows: list[list[Any]], title: str = "") -> dict[str, Any] \| None` | **关键契约对齐**：① `sql_skill.py` 第 121 行以 `adapter.ask(question=question, user_scope=scope, max_rows=max_rows)` 调用（`scope` 为 dict：`{user_id, roles, organization_id, data_scope}`），`VannaAdapter.ask` 必须同时兼容 `user_scope` 传 `dict` 或 `str`；② `scripts/train_vanna.py` 第 126 行调用 `adapter.train(corpus)`；③ 当未安装 `vanna` 库时，内置回退通路：使用 `render_pgsql_ddl()` + Few-shot Prompt 调 `llm_manager.chat(..., purpose="sql")` 生成 SQL，同样强制经过 `sql_validator` 校验后在 `async with pg_session() as pg:` 中只读执行（设置 `SET LOCAL statement_timeout = '30s'`）。 |
| 3 | `scripts/sync_analytics.py`（新增）<br>`[Vanna组] [P1]` | - `async def sync_all(*, full_refresh: bool = False) -> dict[str, int]`<br>- CLI 支持 `--full` 全量重刷、`--month YYYY-MM` 指定快照月、`--dry-run` | 从 MySQL（`session_scope()` 只读查询 ORM）抽取组织、人才+画像、测评结果、学习记录、审计事件，转换写入 PgSQL 6 张宽表（**注意**：`pg_session()` 是只读不提交的，ETL 写入必须使用 `factory = get_pg_session_factory()` 并显式 `await pg.commit()`，采用 PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` 保证幂等可重跑）。 |
| 4 | `scripts/train_vanna.py`（扩充）<br>`[架构组]+[Vanna组] [P1]` | 扩充 `BUSINESS_TERMS` 与 `EXAMPLE_QUERIES` 至 **≥ 40 条**覆盖全场景（结构、年龄学历分布、流失趋势、测评均值、培训完成率、部门对比、同比环比） | 严禁破坏现有 `--output` 与 `--dry-run` CLI 参数兼容性；所有示例 SQL 必须能通过本组 `validate_select_sql` 校验。 |
| 5 | `app/schemas/analytics.py`<br>`[Vanna组] [P1]` | `OverviewResponse`、`DistributionResponse`、`TrendResponse`、`NL2SQLRequest`、`NL2SQLResponseSchema`、`ReportCreateRequest`、`ReportTaskResponse` | 字段严格对齐 `docs/api_document.md` 第十六节。 |
| 6 | `app/services/analytics_service.py` + `app/routers/analytics_router.py`<br>`[Vanna组] [P0/P1]` | `AnalyticsService` 实现：`overview`、`distribution`、`trend`、`nl2sql_ask`、`create_report`、`list_reports`；`analytics_router` 实现 `/analytics` 下 6 个接口 | **优雅降级要求**：`overview / distribution / trend` 优先查 PgSQL 宽表；当 `PGSQL_ENABLED=False` 或分析库为空时，自动降级通过 MySQL ORM 聚合统计返回真实数据。报告导出生成 `.docx`（`python-docx`）/ HTML-PDF 后调用 `[业务组] FileService`（或 `[架构组] get_storage()`）落盘并返回下载链接。 |

---

### 5.4 4 组：Dify 组 `[Dify组]`

#### A. 本组核心定位与严格边界

- **本组要做的事**：封装平台对接 Dify AI 应用中台的统一异步通信层（`dify_adapter.py`）、SSE 事件流协议转换器（`sse.py`）、Dify 连通性健康探针（`health.py`）、低代码工作流/知识库单向同步运维脚本（`sync_dify_workflows.py`），并配合 `[Agent组]` 打通 `AgentService` 的可选 Dify 后端路由分支。
- **本组严禁做的事（防重复红线）**：
  - ❌ **严禁**新建 `/api/v1/dify/*` 等业务层重复路由（前端只认统一的 `/api/v1/agents/*` 与 `/api/v1/knowledge/*`）；
  - ❌ **严禁**将 Dify 变成平台硬依赖（未配置 `DIFY_API_KEY` 或 Dify 容器未启动时，平台所有接口必须自动走本地自研引擎正常运转，绝不能报错拦截）；
  - ❌ **严禁**自写重试装饰器（直接复用 `from app.ai.utils.retry_handler import ai_retry, SimpleCircuitBreaker`）；
  - ❌ **严禁**在 Dify 侧另建一套不受 `sql_validator` 管控的数据库连接去直查业务库。

#### B. 本组逐文件详细开发清单（共 4 个专属文件 + 1 处跨组协同分支 + 单测）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/adapters/dify/sse.py`（新增）<br>`[Dify组] [P0]` | - `async def iter_dify_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]`<br>- `def normalize_dify_event(raw_event: dict[str, Any]) -> dict[str, Any] \| None` | 解析 Dify `response_mode="streaming"` 的 SSE 行流，将 Dify 原生事件（`message` / `agent_message` / `message_end` / `workflow_finished` / `error`）统一映射为平台标准 SSE 事件字典（`start` / `message{"delta": ...}` / `citation` / `done` / `error`），使前端无需区分后端是本地 Agent 还是 Dify。 |
| 2 | `app/adapters/dify/dify_adapter.py`<br>`[Dify组] [P0]` | `class DifyAdapter`（注册至 `Registry("dify")`）：<br>- `async def chat(self, query: str, *, inputs: dict \| None = None, conversation_id: str \| None = None, user: str = "system", app_key: str \| None = None) -> dict[str, Any]`<br>- `async def chat_stream(self, query: str, *, inputs: dict \| None = None, conversation_id: str \| None = None, user: str = "system", app_key: str \| None = None) -> AsyncIterator[dict[str, Any]]`<br>- `async def run_workflow(self, inputs: dict[str, Any], *, user: str = "system", app_key: str \| None = None) -> dict[str, Any]`<br>- `async def upload_document(self, dataset_id: str, filename: str, content: bytes) -> dict[str, Any]`<br>- `async def list_documents(self, dataset_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]`<br>- `async def delete_document(self, dataset_id: str, document_id: str) -> bool` | 基于 `httpx.AsyncClient` 全异步封装，请求头透传 `X-Trace-Id`；挂载 `@ai_retry`；HTTP 401/403/404/5xx 统一映射为 `ExternalServiceError(5001)` 并附带 Dify `code/message`；未配置 `DIFY_API_KEY` 时调用即抛 `ExternalServiceError("Dify 未配置启用")`。 |
| 3 | `app/adapters/dify/health.py`（新增）<br>`[Dify组] [P1]` | - `async def check_dify() -> dict[str, Any]` | 未配置 `DIFY_API_KEY` 时返回 `{"status": "disabled", "reason": "not_configured", ...}`；配置后探测连通性并返回与 `check_mysql` 完全一致的字典结构，供 `/health/ready` 与 `/api/v1/system/info` 聚合展示。 |
| 4 | `scripts/sync_dify_workflows.py`（新增）<br>`[Dify组] [P2]` | - 同步校验配置的 Dify App / Workflow 可用性、支持将 `prompt_config.py` 模板导出或将本地已索引知识文档单向镜像同步至指定 `DIFY_DATASET_ID` | CLI 支持 `--check`、`--sync-kb`，幂等执行。 |
| 5 | `app/services/agent_service.py`（跨组协同分支）<br>`[Agent组]+[Dify组] [P1]` | 在 `AgentService` 中封装私有辅助方法 `_should_use_dify(agent_type)` 与 `_chat_via_dify(...)` | **严格边界**：仅在配置了对应 `DIFY_APP_ID_*` 时优先尝试 `DifyAdapter`，捕获 `ExternalServiceError` 时记录告警日志并无缝回退本地 `dispatch / dispatch_stream`；PR 必须由 `[Agent组]` 组长共同 Review 合并。 |

---

### 5.5 5 组：业务组 `[业务组]`

#### A. 本组核心定位与严格边界

- **本组重要性说明**：`[业务组]` 是全平台的数据基石与核心业务规则承载方（共负责 54+ 个文件），`[架构组]` 的 Skills、`[Agent组]` 的 6 大智能体、`[Vanna组]` 的宽表 ETL 全部依赖本组的 Repository 与 Service！
- **已完成样板（严禁改动破坏，直接照抄风格）**：
  - ✅ **全部 25 张业务表的 ORM 模型**（`app/models/*.py`）已 100% 编写完毕并通过 135 项 MySQL SQL 结构与约束断言测试（`tests/sql/mysql/`）！**严禁随意删改已有表名、字段名、类型或外键约束**！
  - ✅ **认证（Auth）、用户（User）、组织架构（Organization）** 三大模块的 Repository + Schema + Service + Router 已完整跑通，本组开发其余 8 个模块时**必须严格保持相同的分层与代码风格**。
- **本组严禁做的事（防重复红线）**：
  - ❌ **严禁**在子 Repository 中重复编写 `BaseRepository` 已有的通用 CRUD 方法；
  - ❌ **严禁**在 `MatchingService` 中重写匹配打分公式（必须调用 `app.patterns.strategy.MatchStrategyFactory` 与 `app.ai.skills.matching_skill.compute_match_score`）；
  - ❌ **严禁**在任何业务 Service 中直接拼 Prompt 调 `llm_manager`（AI 生成能力一律通过 `await dispatch("<agent_type>", payload, db=db)` 委托给 `[Agent组]`）；
  - ❌ **严禁**直接 `import pymilvus`（向量 upsert/search/delete 一律调用 `[RAG组]` 的 `EmbeddingService` + `MilvusAdapter`，且必须 `try/except ExternalServiceError` 保证 Milvus 未启用时不阻塞业务 CRUD 落库！）；
  - ❌ **严禁**直接 `open()` 写磁盘（文件读写一律走 `FileService` -> `[架构组] get_storage()`）。

#### B. 本组八大待开发业务模块详细代码清单

> **⚠️ 跨组关键强制要求（Skill 桥接）**：`PositionRepository`、`MatchingService`、`ResumeService`、`TalentService` 的 `__init__` 方法签名必须写成 `def __init__(self, db: AsyncSession | None = None)`（当 `self.db is None` 时内部使用 `async with session_scope() as db:`），以无缝兼容 `[架构组]` Skills 的无参反射调用！

##### 模块 1：岗位管理模块（Positions）— P0

- **`app/repositories/position_repository.py`（`[业务组] [P0]`）**：
  - 继承 `BaseRepository[Position]`，配置 `filterable_fields = {"organization_id", "level", "status", "code"}`，`searchable_fields = {"title", "code", "description"}`，`sortable_fields = {"id", "created_at", "publish_at", "salary_max"}`。
  - 实现方法：
    - `async def get_by_code(self, db: AsyncSession, code: str) -> Position | None`
    - `async def list_open_by_org(self, db: AsyncSession, org_ids: list[int] | None = None) -> list[Position]`
    - `async def search_by_title(self, db: AsyncSession | None = None, *, keyword: str = "", status: str = "open", limit: int = 10, context: Any = None) -> list[Position]`（**注意**：直接被 `matching_skill.PositionSearchSkill` 调用，`db=None` 时内部自动开 `session_scope()`！）
    - `async def upsert_requirements(self, db: AsyncSession, position_id: int, items: list[dict]) -> list[PositionRequirement]`
- **`app/schemas/position.py`（`[业务组] [P1]`）**：实现 `PositionCreate`、`PositionUpdate`、`PositionStatusUpdate`、`PositionQuery`、`PositionResponse`（严格对齐 `api_document.md` 第九节）。
- **`app/services/position_service.py`（`[业务组] [P0]`）**：实现 `create_position`（校验 `code` 唯一 + 部门存在 + 同步向量）、`page_list`（补充 `organization_name` 与在职人数 `headcount_filled`）、`get_detail`、`update_position`、`change_status`（强制校验 `closed` 不可直接改回 `open`）、`delete_position`、`parse_jd`（调 `[RAG组] document_loader` + `llm_manager` 或 `dispatch` 提取结构化要求）、`_sync_position_vector`（调 `[RAG组] EmbeddingService` + `MilvusAdapter` 写 `position_vectors`，异常仅记 warning）。
- **`app/routers/position_router.py`（`[业务组] [P1]`）**：实现 `/positions` 下 7 个接口。

##### 模块 2：人才智能档案模块（Talents）— P0

- **`app/repositories/talent_repository.py`（`[业务组] [P0]`）**：
  - 继承 `BaseRepository[Talent]`，实现：
    - `async def get_by_mobile(self, db: AsyncSession, mobile: str) -> Talent | None`（仅查 `is_deleted == False`）
    - `async def paginate_talents(self, db: AsyncSession, page: PageParams, *, filters: dict, skill: str | None = None, work_years_min: int | None = None, work_years_max: int | None = None, org_ids: list[int] | None = None)`
    - `async def search_by_skills(self, db: AsyncSession, skills: list[str], limit: int = 50) -> list[Talent]`
    - `async def count_by_organization(self, db: AsyncSession, org_ids: list[int]) -> dict[int, int]`
    - `async def get_latest_portrait(self, db: AsyncSession, talent_id: int) -> TalentPortrait | None`
    - `async def save_portrait(self, db: AsyncSession, talent_id: int, data: dict) -> TalentPortrait`（自动查询当前最大 `version + 1` 新增记录）
- **`app/schemas/talent.py`（`[业务组] [P1]`）**：实现 `TalentCreate`、`TalentUpdate`、`TalentResponse`、`TalentPortraitResponse`、`SemanticSearchRequest`、`SemanticSearchResponse`、`TalentMergeRequest`。
- **`app/services/talent_service.py`（`[业务组] [P0]`）**：实现 `create_talent`（手机号查重 + 写主档 + 异步同步 `talent_vectors` 与画像）、`page_list`（非管理员自动调用脱敏函数把手机号转为 `138****0001`）、`get_detail`、`get_portrait`、`update_talent`（记录审计前后对比）、`delete_talent`（联动删 `talent_vectors`）、`semantic_search`（调 `[RAG组] EmbeddingService` + `MilvusAdapter.search("talent_vectors")` 拿 ID 后回表组装）、`refresh_portrait`（调用 `[架构组] dispatch("talent", {"talent_id": id}, db=self.db)`）、`save_portrait`、`merge_talents`（在 `transaction(self.db)` 中把源档案的 `resumes` / `assessments` / `learning_records` / `matching_records` 的 `talent_id` 批量迁移到目标主档，再软删源档案）。
- **`app/routers/talent_router.py`（`[业务组] [P1]`）**：实现 `/talents` 下 9 个接口。

##### 模块 3：简历上传与解析编排模块（Resumes）— P0

- **`app/repositories/resume_repository.py`（`[业务组] [P0]`）**：
  - 继承 `BaseRepository[Resume]`，实现：`get_by_hash`、`list_by_talent`、`update_parse_result(db, resume_id, *, status, parsed_data, accuracy, parse_error)`、`replace_experiences(db, resume_id, items)`（写 `ResumeExperience` 子表）。
- **`app/schemas/resume.py`（`[业务组] [P1]`）**：实现 `ResumeUploadResponse`、`ResumeParseTriggerRequest`、`ResumeResponse`、`ResumeParseResultResponse`、`ResumeBindRequest`。
- **`app/services/resume_service.py`（`[业务组] [P0]`）**：
  - `def __init__(self, db: AsyncSession | None = None)`
  - `async def upload_resume(self, upload_file, current_user) -> dict`（校验扩展名与 ≤20MB → 计算 `sha256` → 命中 `get_by_hash` 直接秒传返回 `duplicated=True` → 否则经 `FileService` 落盘建记录）
  - `async def parse_resume(self, *, file_id: int | None = None, resume_id: int | None = None, force: bool = False, context: Any = None) -> dict`（**关键契约**：直接被 `resume_skill.ResumeParseSkill` 调用！置 `status="parsing"` 后调用 `[Agent组] resume_workflow.run_resume_workflow`）
  - `async def save_parse_result(self, resume_id: int, parsed: dict, accuracy: float = 0.95, warnings: list[str] | None = None) -> Resume`（被 `resume_workflow` 的 `persist` 节点调用）
  - `async def dedupe_resume(self, *, resume_id: int | None = None, text: str | None = None, threshold: float = 0.92, context: Any = None) -> dict`（**关键契约**：直接被 `resume_skill.ResumeDedupeSkill` 调用！结合手机号精确查重 + `[RAG组] MilvusAdapter.search("talent_vectors")` 向量相似度查重，返回 `{"duplicated": bool, "threshold": threshold, "matches": [...]}`）
  - `async def get_parse_result(self, resume_id: int)`、`async def bind_talent(self, resume_id: int, req, current_user)`。
- **`app/routers/resume_router.py`（`[业务组] [P1]`）**：实现 `/resumes` 下 6 个接口。

##### 模块 4：人岗匹配模块（Matchings）— P0

- **`app/repositories/matching_repository.py`（`[业务组] [P0]`）**：
  - 继承 `BaseRepository[MatchingRecord]`，实现：`save_batch(db, records)`、`get_latest_by_position(db, position_id, top_k=20, min_score=0)`、`get_by_batch_id(db, batch_id)`、`get_history_by_talent(db, talent_id, page)`。
- **`app/schemas/matching.py`（`[业务组] [P1]`）**：实现 `MatchingRunRequest`、`MatchingBatchResponse`、`MatchingItemResponse`、`MatchingExplainRequest`、`MatchingExplainResponse`。
- **`app/services/matching_service.py`（`[业务组] [P0]`）**：
  - `def __init__(self, db: AsyncSession | None = None)`
  - `async def calculate(self, *, talent_id: int, position_id: int, context: Any = None) -> dict`（**关键契约**：直接被 `matching_skill.MatchingScoreSkill` 调用！查出人才与岗位后直接调 `compute_match_score`）
  - `async def list_candidates(self, *, position_id: int, top_k: int = 10, min_score: float = 0, context: Any = None) -> list[dict]`（**关键契约**：直接被 `matching_skill.CandidateListSkill` 调用！）
  - `async def run_matching(self, req: MatchingRunRequest, current_user) -> dict`（校验岗位 `open` → 候选池召回 → 调用 `MatchStrategyFactory.create(req.strategy).match(pos, pool)` → 生成 `batch_id` 落库）
  - `async def get_by_batch`、`async def get_talent_history`、`async def explain_match`（调 `dispatch("matching")`，失败自动降级规则说明）。
- **`app/routers/matching_router.py`（`[业务组] [P1]`）**：实现 `/matchings` 下 5 个接口。

##### 模块 5：智能测评模块（Assessments）— P1

- **`app/repositories/assessment_repository.py`**：继承 `BaseRepository[Assessment]`，管理 `Assessment`、`AssessmentQuestion`、`AssessmentAnswer`、`AssessmentResult` 四表读写与聚合。
- **`app/schemas/assessment.py`**：实现创建、开考响应（**定义专用 `QuestionForCandidateSchema`，物理剔除 `answer` 标准答案字段！**）、交卷请求、结果详情 Schema。
- **`app/services/assessment_service.py`**：实现创建测评与抽题、开始测评（置 `in_progress`）、提交答卷（检查 `deadline` 标记 `overtime`，完成客观题规则判分，调 `dispatch("assessment")` 完成主观题评分与报告生成，联动 `TalentService` 更新评级）、查询结果。
- **`app/routers/assessment_router.py`**：实现 `/assessments` 下 5 个接口。

##### 模块 6：智能培训模块（Trainings）— P1

- **`app/repositories/training_repository.py`**：继承 `BaseRepository[Course]`，管理 `Course`、`TrainingPlan`、`PlanCourse`、`LearningRecord` 四表读写及计划完成率统计、滞后计划筛选。
- **`app/schemas/training.py`**：实现课程、计划、学习进度上报、AI 推荐请求/响应、滞后预警 Schema。
- **`app/services/training_service.py`**：实现课程管理、计划管理、学习进度上报（强制校验 `new_progress >= old_progress`，回退抛 `BusinessRuleError(1002)`；进度增幅与 `study_minutes` 严重失衡时标记 `suspicious=True`）、AI 课程推荐（调 `dispatch("training")` 并支持落库计划）、滞后预警列表。
- **`app/routers/training_router.py`**：实现 `/trainings` 下 9 个接口。

##### 模块 7：通用文件中心模块（Files）— P1

- **`app/repositories/file_repository.py`**：继承 `BaseRepository[File]`，实现 `get_by_hash`、按业务归属查询。
- **`app/schemas/file.py`（新增，标签 `[业务组] [P1]`）**：实现 `FileUploadResponse`、`BatchUploadResponse`。
- **`app/services/file_service.py`**：调用 `[架构组] file_utils` 校验大小/类型并算 `sha256`，调用 `get_storage().save / read / delete` 管理底层文件，校验下载人权限（本人上传或管理员/对应业务模块权限），软删文件元数据。
- **`app/routers/file_router.py`（新增，标签 `[业务组] [P1]`）**：实现 `/files` 下 5 个接口，替换 `router.py` 中 `"files"` 占位。

##### 模块 8：全局操作与 AI 审计模块（Audit）— P1

- **`app/repositories/audit_repository.py`**：继承 `BaseRepository[AuditLog]`（只暴露 `create`、多条件分页检索 `paginate_logs` 与按动作/状态统计 `stats_by_action`，不暴露修改/删除）。
- **`app/schemas/audit.py`（新增，标签 `[业务组] [P1]`）**：实现 `AuditLogQuery`、`AuditLogResponse`、`AuditStatsResponse`。
- **`app/services/audit_service.py`**：实现 `log(...)`（内部 `try/except` 吞掉异常仅记错误日志，自动对 `password / token / mobile` 字段脱敏），并在模块加载时通过 `event_bus.subscribe("audit.log", ...)` 订阅异步审计事件；实现管理员分页查询与统计。
- **`app/routers/audit_router.py`（新增，标签 `[业务组] [P1]`）**：实现 `/audit` 下 3 个只读查询接口（挂管理员权限），并在 `router.py` 注册。

---

## 六、新增文件与跨组变更合规守门规则

### 6.1 新增文件强制标签模板（不写则 CI 挂红！）

仓库已启用自动化守门测试 `tests/test_code_tags.py`。任何组新增 `.py` 文件（例如 `[Dify组]` 新增 `sse.py`、`[Vanna组]` 新增 `sync_analytics.py`、`[业务组]` 新增 `file_router.py` / `audit_router.py`、`[Agent组]` 新增 `schemas/agent.py`），文件开头模块 docstring **必须包含合规标签行**：

```python
"""
模块：app/routers/file_router.py
职责：通用文件上传、鉴权下载、批量上传与元数据接口。

标签：[业务组] [P1]  依赖：[业务组] FileService
"""
```

提交前本地必跑校验命令（离线毫秒级完成）：

```bash
python3 -m pytest tests/test_code_tags.py -q
```

### 6.2 共享入口文件变更审批表

| 共享文件 | 唯一主责合入组 | 其他组需要变更时的标准操作 |
| --- | --- | --- |
| `app/routers/router.py` | `[架构组]` | 各组在本组 `xxx_router.py` 开发并自测通过后，提 PR 将 `_STUB_MODULES` 对应项替换为 `api_router.include_router(xxx_router)`，由 `[架构组]` Review 合入 |
| `app/config/settings.py` / `.env.example` / `requirements.txt` | `[架构组]` | 提 PR 注明新增配置项默认值及所属组，禁止删除或重命名已有配置项 |
| `app/services/agent_service.py` | `[Agent组]` 牵头 + `[Dify组]` 协同 | 主体由 `[Agent组]` 开发；Dify 双模分支由 `[Dify组]` 提 PR 经 `[Agent组]` 组长共同签字合入 |
| `app/models/*.py` | `[架构组]` + 领域归属组 | 25 张 MySQL 表与 6 张 PgSQL 表结构已冻结；确需增补非破坏性字段时，必须同步运行 `scripts/dump_orm_mapping.py` 并保证 `tests/sql/` 全绿 |
