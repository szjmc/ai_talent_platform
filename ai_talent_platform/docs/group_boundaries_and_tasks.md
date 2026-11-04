# 五大研发组职责边界与防重复开发总规范

> **文档版本**：v3.0.0（5 组垂直闭环强制执行版） ｜ **更新日期**：2026-11-04
> **适用团队**：`[架构组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]`（**全平台严格划分为此五大研发组；原独立业务模块已按领域相关性垂直归口至各组，由该组端到端闭环负责**）
> **核心目标**：**一个功能全平台只写一次、一个文件有且仅有一个主责组（跨组脚本双标签除外）、已完成代码严禁二次重写、跨组能力一律走公开契约调用**。
> **配套文档**：[需求规格说明书](requirements_specification.md) ｜ [系统总体与详细设计文档](system_design_document.md) ｜ [代码标签与优先级总表](plan/05-代码标签与优先级总表.md) ｜ [API 接口文档](api_document.md)

---

## 目录

1. [五大研发组端到端业务与 AI 职责划分总览](#一五大研发组端到端业务与-ai-职责划分总览)
2. [全局防重复开发十条铁律（PR 一票否决红线）](#二全局防重复开发十条铁律pr-一票否决红线)
3. [已落地冻结资产总表（直接调用，严禁任何组重写！）](#三已落地冻结资产总表直接调用严禁任何组重写)
4. [九大高频跨组重叠场景（Hotspots）严格切分裁定](#四九大高频跨组重叠场景hotspots严格切分裁定)
5. [四库（MySQL / PgSQL / Milvus / Redis）按组读写权限边界矩阵](#五四库mysql--pgsql--milvus--redis按组读写权限边界矩阵)
6. [五大研发组逐组详细代码开发说明书](#六五大研发组逐组详细代码开发说明书)
   - [6.0 架构组 `[架构组]`（底座与认证/用户/组织已冻结 ✅ + 文件/审计横向服务 🔧）](#60-架构组-架构组底座与认证用户组织已冻结---文件审计横向服务-)
   - [6.1 1 组：RAG 组 `[RAG组]`（RAG 引擎 + 知识库 + 人才档案 + 简历管理）](#61-1-组rag-组-rag组rag-引擎--知识库--人才档案--简历管理)
   - [6.2 2 组：Agent 组 `[Agent组]`（LangGraph/6 大 Agent + 岗位管理 + 人岗匹配）](#62-2-组agent-组-agent组langgraph6-大-agent--岗位管理--人岗匹配)
   - [6.3 3 组：Vanna 组 `[Vanna组]`（NL2SQL/统计看板 + 在线智能测评）](#63-3-组vanna-组-vanna组nl2sql统计看板--在线智能测评)
   - [6.4 4 组：Dify 组 `[Dify组]`（Dify 低代码网关 + 个性化智能培训）](#64-4-组dify-组-dify组dify-低代码网关--个性化智能培训)
7. [新增文件与跨组变更合规守门规则](#七新增文件与跨组变更合规守门规则)

---

## 一、五大研发组端到端业务与 AI 职责划分总览

全平台 **195 个现存 `.py` 文件 + 4 个根配置文件 + 5 个 MySQL 测试 SQL** 已 100% 完成五大研发组标签覆盖，由 `tests/test_code_tags.py` 强制守门（每个业务模块从 `Model → Repository → Schema → Service → Router` 垂直归口同一组，消除“AI 组等待业务实现方”的跨组扯皮）：

| 组别 | 代码标签 | 人员规模 | AI / 平台技术底座职责 | 垂直归口的业务模块（端到端闭环） | 现存 `.py` 文件数 |
| --- | --- | --- | --- | --- | --- |
| 0 组 | `[架构组]` | 2 人 + 4 组长 | FastAPI 公共框架、RBAC、四库连接池、LLM 门面、AIContext、8 大 Skills、BaseAgent/dispatch、Memory/State、匹配三策略、存储、事件总线 | ✅ **认证/用户/角色/组织**（`auth / users / roles / organizations`，已完成）<br>🔧 **通用文件中心与全局审计**（`files / audit` 横向底座） | **124**（P0:53 / P1:59 / P2:12） |
| 1 组 | `[RAG组]` | 3 人 | Milvus 连接与 4 集合唯一适配器、Embedding 缓存、多格式文档加载与切分、混合检索器 `Retriever`、无幻觉问答 `RAGPipeline` | 🔧 **企业知识库**（`knowledge`）<br>🔧 **人才智能档案与画像底座**（`talents`）<br>🔧 **简历上传、查重与解析入库**（`resumes`） | **26**（P0:16 / P1:10 / P2:0） |
| 2 组 | `[Agent组]` | 4 人 | LangGraph 图构建器、`resume / matching / analysis` 3 条有状态工作流、6 个领域 Agent 子类、多轮会话与异步任务调度 | 🔧 **智能体对话与任务中心**（`agents`）<br>🔧 **岗位管理与 JD 解析**（`positions`）<br>🔧 **人岗精准匹配与排序解释**（`matchings`） | **26**（P0:13 / P1:10 / P2:3） |
| 3 组 | `[Vanna组]` | 4 人 | `sqlglot` AST 安全校验器 `sql_validator`、`VannaAdapter` NL2SQL 问数、PgSQL 6 张分析宽表增量 ETL、训练语料扩充 | 🔧 **数据决策看板与智能问数**（`analytics`）<br>🔧 **在线人才智能测评**（`assessments`，题库/防泄密开考/客观题判分/分析数据源） | **16**（P0:4 / P1:12 / P2:0） |
| 4 组 | `[Dify组]` | 2 人 | `DifyAdapter` 异步工作流/对话客户端、Dify SSE 事件归一化协议、连通性健康探针、知识库/Prompt 单向同步脚本、`AgentService` 双模分支 | 🔧 **个性化智能培训与学习督办**（`trainings`，课程/计划/防刷进度/滞后预警 + Dify 工作流通知） | **7**（P0:2 / P1:5 / P2:0）+ 3 个新建文件 |

> 注：4 个跨组共建文件（`scripts/init_milvus.py`、`scripts/init_pgsql.py`、`scripts/train_vanna.py`、`tests/test_pgsql_schema.py`）带 `[架构组]+[RAG组/Vanna组]` 双标签，扣除重复计数后全仓唯一 `.py` 文件总数为 **195**（`P0=88, P1=92, P2=15`）。

---

## 二、全局防重复开发十条铁律（PR 一票否决红线）

1. **禁止重写大模型调用客户端**：全平台只有 `[架构组]` 的 `app/ai/core/llm_manager.py`（`llm_manager.chat / stream / embed`）允许调用大模型 API。其余 4 组**严禁**自己 `httpx.post("/chat/completions")`、自己 `import openai` 或 `import dashscope`。
2. **禁止重写向量库客户端与 Embedding**：全平台只有 `[RAG组]` 的 `MilvusAdapter` 允许 `import pymilvus`，只有 `EmbeddingService` 允许封装文本向量化与缓存。`[Agent组]`（写 `position_vectors`）和 `[Vanna组]`（写 `question_vectors`）操作向量时，**必须调用 `[RAG组]` 的 `EmbeddingService` + `MilvusAdapter`**。
3. **禁止重写文档文本提取器**：PDF、Word、Excel、TXT、Markdown 的文本提取统一由 `[RAG组]` 在 `app/ai/rag/document_loader.py` 实现一次。本组简历解析、`[Agent组]` 的 `resume_workflow` 及 `PositionService.parse_jd` **必须直接调用 `document_loader.load(file_path)`**，严禁另写第二套 `pdfplumber` / `python-docx` 解析代码。
4. **禁止重写人岗匹配打分公式**：四维加权打分算法（`compute_match_score`）与三大匹配策略（`HardFilterStrategy / SoftMatchStrategy / HybridStrategy`）**已由 `[架构组]` 在 `app/ai/skills/matching_skill.py` 与 `app/patterns/strategy.py` 完整实现**。`[Agent组]` 的 `MatchingService`、`matching_workflow`、`matching_agent` **必须直接调用**，严禁再写第二套加权打分或硬性门槛判断逻辑！
5. **禁止重写技能标签抽取、输出 JSON 解析、重试熔断、会话记忆与状态管理**：`extract_skills`（`resume_skill.py`）、`parse_json / parse_model`（`output_parser.py`）、`ai_retry / SimpleCircuitBreaker`（`retry_handler.py`）、`ConversationMemory`（`conversation_memory.py`）、`StateManager`（`state_manager.py`）**全部已由 `[架构组]` 实现完毕**，其余各组只准 `import` 使用，严禁覆盖或另建同类工具！
6. **禁止重写通用 CRUD 与分页/响应/权限**：所有 MySQL 仓储必须继承 `BaseRepository`（自带 17 个异步 CRUD/分页/软删/乐观锁方法），禁止在子 Repository 重复写 `get_by_id`、`create`、`paginate`；所有接口返回必须用 `app.core.response.success`，异常必须抛 `BusinessException` 子类；全部 25 张业务表 ORM 模型已冻结，严禁重建 Model！
7. **禁止重写物理文件读写与哈希工具**：磁盘读写统一走 `[架构组]` 的 `FileService`（底层 `get_storage()`，`app/adapters/storage/base_storage.py`），文件名清洗与 SHA-256 统一调用 `app/utils/file_utils.py`，严禁业务代码直接 `open(..., "wb")` 或 `os.remove()`。
8. **禁止跨组直触他组 ORM 表与越层写 SQL**：每张 MySQL 业务表有且仅有一个归属组（见第五章矩阵）。非归属组（含 AI Agent/Workflow）**严禁直接 `select/insert/update` 他组 ORM 表**，跨组取数与落库一律通过归属组公开 `Service` 方法或 `[架构组]` 已注册的 8 个 `Skills`。
9. **禁止多头建设 NL2SQL**：全平台自然语言转 SQL 与 PgSQL 分析库查询由 `[Vanna组]` 的 `sql_validator.py` + `vanna_adapter.py` **唯一实现**；`[Agent组]` 的 `analysis_workflow` / `AssistantAgent` 需要查统计数据时只能调 `sql_query` 技能或 `AnalyticsService`，`[Dify组]` 需要查数据时只能调平台 `/api/v1/analytics/nl2sql` 接口。
10. **禁止无标签新增文件或私改他组文件**：每个代码文件头部已有明确 `标签：[xx组] [Px]`，严禁跨组私自修改非本组文件；如需新增 `.py` 文件，模块 docstring 必须包含合法 `标签：[本组] [P0|P1|P2]` 并通过 `pytest tests/test_code_tags.py -q`。

---

## 三、已落地冻结资产总表（直接调用，严禁任何组重写！）

在动手写任何一行代码前，请先查阅下表——**以下能力均已由 `[架构组]` 100% 编码落地并通过测试**：

| 能力类别 | 已实现文件路径 | 暴露的核心类 / 函数（直接 `import` 使用） | 哪些组必须直接复用 |
| --- | --- | --- | --- |
| 响应与分页 | `app/core/response.py`、`app/core/pagination.py` | `success()`, `PageParams`, `PageResult`, `build_page_result()` | 全部 5 组（Router / Service） |
| 异常体系 | `app/core/exceptions.py` | `ParamError(1001)`, `BusinessRuleError(1002)`, `NotFoundError(3001)`, `DuplicateError(3002)`, `DataConflictError(3003)`, `ExternalServiceError(5001)`, `AIError(5002)`, `FileError(5003)` | 全部 5 组 |
| 认证与 RBAC | `app/core/dependencies.py`、`app/core/permissions.py` | `get_db`, `get_current_user`, `require_permission`, `PermissionEnum`, `get_data_scope`, `check_data_scope` | 全部 5 组 |
| MySQL 会话与事务 | `app/infrastructure/database/session.py`、`transaction.py` | `get_db()`（Router 注入）、`session_scope()`（后台/脚本/Skill）、`transaction()`（嵌套事务+死锁重试） | 全部 5 组 |
| PgSQL 会话与 6 表 ORM | `app/infrastructure/database/postgres.py`、`app/models/analytics.py`、`analytics_schema.py` | `pg_session()`（只读查询）、`get_pg_session_factory()`（ETL 写入）、`DimOrganization`, `DimTalentWide`, `Fact*` | `[Vanna组]` |
| Redis 异步缓存 | `app/infrastructure/cache/redis.py` | `get_json(key)`, `set_json(key, val, ttl)`, `delete_keys(pattern)`, `incr_with_ttl(key, ttl)` | 全部 5 组 |
| 25 张业务表 ORM | `app/models/{user,role,organization,position,talent,resume,matching,assessment,training,knowledge,conversation,file,audit}.py` | 全部 25 个 SQLAlchemy ORM 类（已注册进 `app/models/__init__.py`） | 全部 5 组（**严禁重建或私改字段**） |
| 通用 CRUD 基类 | `app/repositories/base.py` | `BaseRepository`（`get_by_id`, `get_by_id_or_404`, `paginate`, `list`, `create`, `create_many`, `update`, `delete`, `exists`, `count`, `check_version`） | 全部 5 组 |
| 已完成 3 大业务样板 | `app/{repositories,schemas,services,routers}/{auth,user,organization}*` | 登录鉴权、用户管理、组织树与部门防环移动（**作为其余 8 个业务模块的标准代码样板**） | `[架构组]` 维护，其余 4 组只读调用并参照风格 |
| AI 安全上下文 | `app/ai/context.py` | `AIContext`, `set_context()`, `get_context()`, `clear_context()`, `context_scope()` | 全部 5 组 |
| 大模型统一门面 | `app/ai/core/llm_manager.py`、`token_manager.py` | `llm_manager.chat()`, `llm_manager.stream()`, `llm_manager.embed()`, `TokenManager` | `[RAG组]`、`[Agent组]`、`[Vanna组]` |
| AI JSON 解析与重试 | `app/ai/utils/output_parser.py`、`retry_handler.py` | `parse_json()`, `parse_model()`, `ai_retry()`, `SimpleCircuitBreaker` | `[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]` |
| Prompt 版本仓库 | `app/config/prompt_config.py`、`app/ai/prompts/*_prompt.py` | `prompt_registry.get(name)`, `prompt_registry.render(name, **kw)` | `[Agent组]`、`[Dify组]` |
| 8 大标准 Skills | `app/ai/skills/{base,knowledge_skill,matching_skill,resume_skill,sql_skill,registry_bootstrap}.py` | `get_skill()`, `list_tools()`, `compute_match_score()`, `extract_skills()`, `search_knowledge()`, `query_database()` | `[Agent组]`、`[RAG组]` |
| 人岗匹配三策略 | `app/patterns/strategy.py` | `MatchStrategyFactory.create("hard" \| "soft" \| "hybrid")`, `HardFilterStrategy`, `SoftMatchStrategy`, `HybridStrategy` | `[Agent组]`（`MatchingService` / `matching_workflow`） |
| Agent 基类与调度 | `app/ai/agents/base_agent.py`、`app/ai/agent.py` | `BaseAgent`, `@register_agent`, `dispatch()`, `dispatch_stream()`, `AGENT_TYPES` | `[Agent组]` 实现子类；`[RAG组]`/`[Vanna组]`/`[Dify组]` 通过 `dispatch()` 调用 |
| 会话记忆与图状态 | `app/ai/memory/conversation_memory.py`、`state_manager.py` | `ConversationMemory`, `StateManager`, `state_manager`, `to_jsonable()` | `[Agent组]` |
| 文件存储与文件工具 | `app/adapters/storage/base_storage.py`、`app/utils/file_utils.py` | `get_storage()`, `safe_filename()`, `sha256_bytes()`, `validate_extension()` | `[架构组]`（`FileService`）、`[RAG组]`、`[Vanna组]` |
| 进程内异步事件总线 | `app/infrastructure/messaging/event_bus.py` | `event_bus.subscribe()`, `event_bus.publish()`, `event_bus.publish_await()` | `[架构组]`（`AuditService` 订阅）、其余 4 组发布事件 |

---

## 四、九大高频跨组重叠场景（Hotspots）严格切分裁定

| 重叠场景 | 唯一实现归属（谁写核心逻辑） | 协同调用方（怎么调） | 严禁越界红线（绝对不允许出现的情况） |
| --- | --- | --- | --- |
| **① 人岗匹配打分、跑批与解释** | `[架构组]` 已实现确定性打分 `compute_match_score` 与 `MatchStrategyFactory`；`[Agent组]` 垂直负责 `PositionService`、`MatchingService`、`matching_workflow` 与 `matching_agent` | `MatchingService` 调 `[RAG组] TalentService` 取候选人池、调 `MatchStrategyFactory` 算分落库；`matching_agent` 仅对规则算出的四维分生成文字解释 | 严禁 `[Agent组]` 再写第二套加权打分公式、严禁让 LLM 篡改规则总分、严禁直查 `talents` 表 |
| **② 简历上传、文档提取、查重与 AI 解析** | `[RAG组]` 垂直负责 `document_loader`、`ResumeService`（上传秒传、SHA256+`talent_vectors` 查重、绑定 `TalentService`）；`[Agent组]` 负责 `resume_workflow` + `resume_agent`（LLM 结构化抽取） | `ResumeService.parse_resume` 触发 `[Agent组] run_resume_workflow`；工作流内部调 `[RAG组] document_loader.load()` 取文本、调 `[架构组] extract_skills()` 补标签、回调 `[RAG组] ResumeService.save_parse_result()` 落库 | 严禁 `[Agent组]` 写 PDF/Word 解析器或直写 `resumes` 表；严禁 `[RAG组]` 在 `ResumeService` 里手写 Prompt 调 LLM 解析简历 |
| **③ Milvus 4 大向量集合操作** | `[RAG组]` 独占实现 `infrastructure/vector/milvus.py`、`MilvusAdapter` 与 `EmbeddingService`，并直接读写 `knowledge_vectors` 与 `talent_vectors` | `[Agent组] PositionService` 调 `EmbeddingService` + `MilvusAdapter` 维护 `position_vectors`；`[Vanna组] AssessmentService` 调 `MilvusAdapter` 维护 `question_vectors` | 严禁 `[Agent组]`、`[Vanna组]`、`[Dify组]` 直接 `import pymilvus` 或自建向量连接池 |
| **④ 制度知识问答 vs 全局智能体助手 vs Dify 知识库** | `[RAG组]` 独占实现 `/api/v1/knowledge/*` 与 `RAGPipeline`（单领域严谨制度问答）；`[Agent组]` 实现 `/api/v1/agents/chat*` 与 `AssistantAgent`（多技能路由助手） | `AssistantAgent` 遇到制度问题时调用 `knowledge_search` 技能（底层走 `[RAG组] Retriever`）；`[Dify组]` 仅做单向同步脚本把已索引文档镜像至 Dify Dataset | 严禁 `[Agent组]` 直查 `knowledge_chunks` 表；严禁 `[Dify组]` 接管或重写 `/api/v1/knowledge/*` |
| **⑤ 自然语言问数 NL2SQL vs 统计看板 vs 决策工作流** | `[Vanna组]` 独占实现 `sql_validator`、`vanna_adapter`、`AnalyticsService`、`/api/v1/analytics/*` 与 PgSQL 宽表 ETL | `[Agent组]` 的 `analysis_workflow`（人才数据决策工作流）通过调用 `sql_query` 技能与 `AnalyticsService` 获取聚合数据，再用 LLM 生成管理建议 | 严禁 `[Agent组]` 或 `[Dify组]` 自行生成 SQL 或直连 `pg_session()` |
| **⑥ Dify 中台 vs 本地 Agent / 培训督办工作流** | `[Dify组]` 负责 `app/adapters/dify/*` 与培训业务模块 `TrainingService`；`[Agent组]` 拥有 `agent_router.py` 与 `agent_service.py` 主体 | ① `AgentService` 预留配置开关：配了 `DIFY_APP_ID_*` 调 `DifyAdapter`，未配走本地 `dispatch`；② `TrainingService` 可选调 `DifyAdapter.run_workflow` 推送滞后预警督办 | 严禁 `[Dify组]` 新建 `/api/v1/dify/*` 重复业务路由，严禁改写 `BaseAgent` |
| **⑦ 人才画像 / 测评判分 / 培训推课的“业务规则 vs AI 推理”切分** | `[RAG组] TalentService`、`[Vanna组] AssessmentService`、`[Dify组] TrainingService` 各自负责本域主档 CRUD、客观题判分、防超时/防刷课规则与 REST API；`[Agent组]` 统一负责 `talent_agent / assessment_agent / training_agent` | 各业务 Service 完成确定性事务后，统一通过 `await dispatch("talent" \| "assessment" \| "training", payload, db=self.db)` 调用 `[Agent组]` 的智能体生成画像/主观题评语/选课推荐 | 严禁 `[Agent组]` 写客观题计分或学习进度校验；严禁 `[RAG组]/[Vanna组]/[Dify组]` 在业务 Service 里手写 Prompt 调大模型 |
| **⑧ 多轮对话记忆 vs 会话历史持久化** | `[架构组]` 已实现 `ConversationMemory`（Redis 窗口+摘要+自动回调仓储）；`[Agent组]` 仅实现 `ConversationRepository` | `AgentService` 调 `ConversationMemory` 管理上下文，调 `ConversationRepository` 提供会话列表/历史分页查询接口 | 严禁 `[Agent组]` 重写或修改 `app/ai/memory/conversation_memory.py` 与 `state_manager.py` |
| **⑨ 通用文件中心与全局操作/AI 审计** | `[架构组]` 统一负责底层 `get_storage()`、`EventBus` 以及横向服务 `FileService`（`/api/v1/files`）与 `AuditService`（`/api/v1/audit`） | `[RAG组]`（简历/制度上传）、`[Vanna组]`（分析报告导出）统一调 `FileService`；全部 5 组记审计统一调 `AuditService.log()` 或发 `event_bus.publish("audit.log", ...)` | 严禁其余 4 组直写 `files` 表或 `audit_logs` 表 |

---

## 五、四库（MySQL / PgSQL / Milvus / Redis）按组读写权限边界矩阵

> 图例：`R/W` = 唯一允许通过本组 Repository/Adapter 读写的归属组 ｜ `Via Service/Skill` = 仅允许通过主责组公开方法间接访问 ｜ `❌` = 严禁直接触碰

| 存储库与对象 | `[架构组]` | `[RAG组]` | `[Agent组]` | `[Vanna组]` | `[Dify组]` |
| --- | --- | --- | --- | --- | --- |
| **MySQL**：`users / roles / user_roles / organizations` | **R/W（✅已实现）** | Via `AIContext` / `OrganizationService` | Via `UserService` / `OrganizationService` | ETL 只读抽取 | Via `AIContext` |
| **MySQL**：`files / audit_logs` | **R/W（唯一归属）** | Via `FileService` / `EventBus` | Via `FileService` / `EventBus` | Via `FileService` / `EventBus` + ETL 只读 | Via `EventBus` |
| **MySQL**：`knowledge_docs / knowledge_chunks` | Via `knowledge_search` 技能 | **R/W（唯一归属）** | Via `knowledge_search` 技能 | ❌ | 只读同步脚本（经 Service） |
| **MySQL**：`talents / talent_portraits` | ❌ | **R/W（唯一归属）** | Via `TalentService`（画像回调与匹配取数） | ETL 只读抽取 + 测评评级回调 `TalentService` | Via `TalentService` |
| **MySQL**：`resumes / resume_experiences` | Via `resume_*` 技能 | **R/W（唯一归属）** | `resume_workflow` 经 `ResumeService` 回写 | ❌ | ❌ |
| **MySQL**：`positions / position_requirements` | Via `position_search` 技能 | ❌ | **R/W（唯一归属）** | ETL 只读抽取 | ❌ |
| **MySQL**：`matching_records` | Via `matching_*` 技能 | `merge_talents` 经 Service 迁移 | **R/W（唯一归属）** | ETL 只读抽取 | ❌ |
| **MySQL**：`conversations / chat_messages` | `ConversationMemory` 回调 | ❌ | **R/W（唯一归属）** | ❌ | ❌ |
| **MySQL**：`assessments / *_questions / *_answers / *_results` | ❌ | `merge_talents` 经 Service 迁移 | `assessment_agent` 经 `AssessmentService` 回写 | **R/W（唯一归属）** | Via `AssessmentService` 查短板 |
| **MySQL**：`courses / training_plans / plan_courses / learning_records` | ❌ | `merge_talents` 经 Service 迁移 | `training_agent` 经 `TrainingService` 读写 | ETL 只读抽取 | **R/W（唯一归属）** |
| **PgSQL**：6 张分析宽表（`dim_*` / `fact_*`） | 引擎与建表骨架已交付 | ❌ | Via `sql_query` 技能 / `AnalyticsService` | **ETL 写 + 问数/看板只读（唯一归属）** | Via `/analytics/nl2sql` |
| **Milvus**：`knowledge_vectors / talent_vectors` | 初始化脚本已交付 | **R/W（唯一归属）** | Via `knowledge_search` / `TalentService` | ❌ | ❌ |
| **Milvus**：`position_vectors` | 初始化脚本已交付 | 提供 `MilvusAdapter` 网关 | **业务 R/W（调 `MilvusAdapter`）** | ❌ | ❌ |
| **Milvus**：`question_vectors` | 初始化脚本已交付 | 提供 `MilvusAdapter` 网关 | ❌ | **业务 R/W（调 `MilvusAdapter`）** | ❌ |
| **Redis**：各业务命名空间 | `org:*` / `ai:token:*` / `chat:*` / `wf:*` | `rag:*` / `talent:*` | 复用架构组 `Memory/State` | `nl2sql:*` / `analytics:*` | `dify:*` |

---

## 六、五大研发组逐组详细代码开发说明书

> **⚠️ 跨组关键强制契约（Skill 双模式会话桥接）**：
> `[架构组]` 已实现的 `matching_skill.py` 与 `resume_skill.py` 会通过无参构造函数反射调用 `PositionRepository()`、`MatchingService()`、`ResumeService()`。因此负责实现它们的 `[Agent组]` 与 `[RAG组]` **必须**将其构造函数签名写为 `def __init__(self, db: AsyncSession | None = None)`：当 Router 注入 `db` 时复用请求级事务；当 `self.db is None` 时内部使用 `async with session_scope() as db:` 自动管理会话！

---

### 6.0 架构组 `[架构组]`（底座与认证/用户/组织已冻结 ✅ + 文件/审计横向服务 🔧）

#### A. 本组已完成冻结资产（120 个 `.py` 文件 + 5 个 MySQL 测试 SQL）

- ✅ **公共底座与 AI 核心层**：`app/config/*`、`app/core/*`、`app/infrastructure/*`、`app/patterns/*`、`app/adapters/llm/*`、`app/adapters/storage/*`、`app/ai/{context,agent,core,memory,prompts,skills,utils}/*`、`app/models/{base,mapping,user,role,organization,file,audit}.py`、`app/repositories/base.py` 及 `tests/` 守门测试集。
- ✅ **认证、用户与组织架构三大核心模块**：`app/{repositories,schemas,services,routers}/{auth,user,organization}*` 已 100% 落地并通过测试，作为其余 4 组编写业务模块的统一标准样板。

#### B. 本组待补齐的 2 个平台横向模块（共 8 个文件：4 个骨架完善 + 4 个新建）

| 序号 | 模块 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- | --- |
| 1 | 通用文件中心 `Files` | `app/repositories/file_repository.py`<br>`[架构组] [P1]` | `class FileRepository(BaseRepository[File])`：<br>- `async def get_by_hash(self, db: AsyncSession, file_hash: str) -> File \| None`<br>- `async def list_by_biz(self, db: AsyncSession, biz_type: str, biz_id: int) -> list[File]` | 继承 `BaseRepository[File]`，仅查 `is_deleted == False` 的有效文件元数据。 |
| 2 | 通用文件中心 `Files` | `app/schemas/file.py`（新建）<br>`[架构组] [P1]` | `FileUploadResponse`、`FileDetailResponse`、`BatchUploadResponse` | 严格对齐 `docs/api_document.md` 第十八节返回结构。 |
| 3 | 通用文件中心 `Files` | `app/services/file_service.py`<br>`[架构组] [P1]` | `class FileService`：<br>- `def __init__(self, db: AsyncSession \| None = None)`<br>- `async def save_upload(self, upload_file, current_user, *, biz_type: str = "general", biz_id: int \| None = None) -> File`<br>- `async def save_bytes(self, data: bytes, filename: str, *, uploader_id: int \| None = None, biz_type: str = "export") -> File`<br>- `async def get_download_stream(self, file_id: int, current_user) -> tuple[File, bytes]`<br>- `async def delete_file(self, file_id: int, current_user) -> None` | 调用 `file_utils.validate_extension / safe_filename / sha256_bytes` 做安全校验与哈希计算；命中同哈希时复用底层存储路径实现秒传；底层物理读写统一调 `get_storage().save / read / delete`；下载时校验上传者本人、管理员或对应业务模块权限。同时供 `[RAG组]`（简历/制度上传）和 `[Vanna组]`（分析报告落盘）直接调用。 |
| 4 | 通用文件中心 `Files` | `app/routers/file_router.py`（新建）<br>`[架构组] [P1]` | 实现 `/files` 下 5 个接口（单传、批传、详情、鉴权下载、软删） | 在本组主管的 `app/routers/router.py` 中替换 `"files"` 占位。 |
| 5 | 全局审计 `Audit` | `app/repositories/audit_repository.py`<br>`[架构组] [P1]` | `class AuditRepository(BaseRepository[AuditLog])`：<br>- `async def paginate_logs(self, db: AsyncSession, page: PageParams, *, filters: dict, start_time=None, end_time=None)`<br>- `async def stats_by_action(self, db: AsyncSession, *, days: int = 7) -> dict[str, Any]` | **Append-Only 红线**：只暴露新增与查询聚合接口，禁止暴露修改或删除审计日志的方法。 |
| 6 | 全局审计 `Audit` | `app/schemas/audit.py`（新建）<br>`[架构组] [P1]` | `AuditLogQuery`、`AuditLogResponse`、`AuditStatsResponse` | 对齐 `docs/api_document.md` 第十九节。 |
| 7 | 全局审计 `Audit` | `app/services/audit_service.py`<br>`[架构组] [P1]` | `class AuditService`：<br>- `async def log(self, payload: dict[str, Any]) -> None`<br>- `async def page_list(self, query: AuditLogQuery, page: PageParams)`<br>- `async def get_stats(self, days: int = 7) -> dict` | `log()` 内部自动对 `password / token / secret / mobile` 等敏感字段脱敏，并用 `try/except` 吞掉写库异常（仅打 `logger.error`），确保审计故障绝不阻断主业务；在应用启动时通过 `event_bus.subscribe("audit.log", ...)` 订阅全平台异步审计事件。 |
| 8 | 全局审计 `Audit` | `app/routers/audit_router.py`（新建）<br>`[架构组] [P1]` | 实现 `/audit` 下 3 个只读查询与统计接口 | 挂载 `PermissionEnum.AUDIT_READ` 管理员权限，在 `router.py` 中替换 `"audit"` 占位。 |

---

### 6.1 1 组：RAG 组 `[RAG组]`（RAG 引擎 + 知识库 + 人才档案 + 简历管理）

#### A. 本组核心定位与严格边界

- **为什么将「人才档案 `talents`」与「简历管理 `resumes`」垂直归口 `[RAG组]`**：
  简历多格式文档提取依赖 `document_loader`，简历语义查重与人才语义搜人（`POST /talents/semantic-search`）直接读写 `talent_vectors` 集合。将 `knowledge + talents + resumes` 统一归口 `[RAG组]`，实现了**文档提取 → 向量化 → Milvus 检索/查重 → 人才/知识入库**的本组零跨组阻塞闭环！
- **本组严禁做的事（防重复红线）**：
  - ❌ **严禁**新建或修改 `app/models/{knowledge,talent,resume}.py`（ORM 模型已冻结）及 `app/ai/skills/{knowledge_skill,resume_skill}.py`（架构组已完成）；
  - ❌ **严禁**自己写 HTTP 客户端调用大模型或 Embedding 接口（统一调 `llm_manager.chat / stream / embed`）；
  - ❌ **严禁**在 `ResumeService` 或 `TalentService` 中手写 Prompt 让大模型解析简历或生成人才画像——简历 LLM 结构化解析必须调 `[Agent组] run_resume_workflow`，人才画像生成必须调 `[架构组] await dispatch("talent", ...)`！
  - ❌ **严禁**直接 `open()` 写磁盘（统一调 `[架构组] FileService` / `get_storage()`）。

#### B. 本组逐文件详细开发清单（共 19 个待开发代码文件，分三大子域）

##### 子域一：Milvus 向量底座 + RAG 引擎 + 知识库闭环（11 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/infrastructure/vector/milvus.py`<br>`[RAG组] [P0]` | - `connect_milvus() -> None`<br>- `disconnect_milvus() -> None`<br>- `check_milvus() -> dict[str, Any]`<br>- `get_collection(name: str)` | 封装 `pymilvus.connections` 惰性连接；`settings.MILVUS_ENABLED=False` 或未装 `pymilvus` 时 `check_milvus()` 返回 `{"status": "disabled"\|"down", ...}`，绝不阻塞启动。 |
| 2 | `app/adapters/vector_store/milvus_adapter.py`<br>`[RAG组] [P0]` | `class MilvusAdapter(BaseVectorStore)`：<br>- `async def ensure_collections(self, dim: int \| None = None) -> None`<br>- `async def upsert(self, collection: str, ids: list[int], vectors: list[list[float]], scalars: list[dict[str, Any]]) -> int`<br>- `async def search(self, collection: str, vector: list[float], *, top_k: int = 10, expr: str \| None = None, output_fields: list[str] \| None = None) -> list[dict[str, Any]]`<br>- `async def delete(self, collection: str, *, ids: list[int] \| None = None, expr: str \| None = None) -> int` | **全平台 4 个集合（`knowledge_vectors / talent_vectors / position_vectors / question_vectors`）的唯一读写网关**（严格对齐 `scripts/init_milvus.py`）。内部用 `asyncio.to_thread` 包装同步 SDK；未启用时抛 `ExternalServiceError("Milvus 向量库未启用")`。 |
| 3 | `app/ai/rag/embedding_service.py`<br>`[RAG组] [P0]` | `class EmbeddingService`：<br>- `async def embed_texts(self, texts: list[str], *, batch_size: int = 16) -> list[list[float]]`<br>- `async def embed_query(self, query: str) -> list[float]` | 统一调 `await llm_manager.embed(batch)`；对查询向量做 Redis（`rag:emb:{model}:{sha256}`）+ 进程内字典二级缓存。供本组知识库/人才库及 `[Agent组]` 岗位、`[Vanna组]` 题库复用。 |
| 4 | `app/ai/rag/document_loader.py`<br>`[RAG组] [P1]` | - `@dataclass class LoadedDocument: content: str; metadata: dict[str, Any]`<br>- `def load(file_path: str \| Path) -> list[LoadedDocument]`<br>- `def load_bytes(data: bytes, filename: str) -> list[LoadedDocument]` | 支持 `.pdf`（`pdfplumber` 逐页带页码）、`.docx`（`python-docx`）、`.xlsx`（`openpyxl`）、`.txt/.md`（`utf-8 → gbk`）。同时供本组知识库、本组简历查重、`[Agent组]` 简历工作流与 JD 解析复用。 |
| 5 | `app/ai/rag/document_splitter.py`<br>`[RAG组] [P1]` | `split_text`、`split_by_headings`、`split_documents` | 中文句末标点优先切分，识别 `第X章/节/条` 与 Markdown 标题写入 `metadata["heading"]` 与 `metadata["chunk_index"]`。 |
| 6 | `app/repositories/knowledge_repository.py`<br>`[RAG组] [P0]` | `class KnowledgeRepository(BaseRepository[KnowledgeDoc])`：<br>`get_by_hash`、`list_accessible_docs`、`save_chunks`、`get_chunks_by_ids`、`search_chunks_by_keyword`、`delete_chunks_by_doc`、`update_index_status` | 严格过滤 `status == "indexed"` 且满足当前用户 `permission_scope`（`public` / 本部门 `dept` / 角色命中 `role`）的文档与切片。 |
| 7 | `app/ai/rag/retriever.py`<br>`[RAG组] [P0]` | `class Retriever`：<br>- `def __init__(self, db: AsyncSession \| None = None, store=None, embedder=None)`<br>- `semantic_search`、`keyword_search`、`hybrid_search(self, question: str, *, top_k: int = 10, scope: dict \| AIContext \| None = None, use_rerank: bool = False) -> list[RetrievedChunk]` | **关键契约**：无参 `Retriever().hybrid_search(question, top_k=top_k, scope=scope)` 直接被 `knowledge_skill.py` 调用！构造 Milvus 前置权限 `expr` + RRF（`1/(60+rank)`）融合排序。 |
| 8 | `app/ai/rag/rag_pipeline.py`<br>`[RAG组] [P1]` | `class RAGPipeline`：`index_document`、`ask`、`ask_stream` | **零幻觉铁律**：检索为空时禁止调 LLM，直接返回 `{"answer": "知识库中未找到依据", "citations": []}`；有命中时带 `[1][2]` 引用编号生成答案并返回 `citations`。 |
| 9–11 | `app/schemas/knowledge.py`、`app/services/knowledge_service.py`、`app/routers/knowledge_router.py`<br>`[RAG组] [P0/P1]` | 实现 `/knowledge` 全部 7 个接口及文档状态机（`uploaded → indexing → indexed / failed`） | 删除文档时校验 `status != "indexing"`，软删 MySQL 记录并联动清理 `knowledge_vectors`；完成后提 PR 替换 `"knowledge"` 占位路由。 |

##### 子域二：人才智能档案模块 `Talents`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 12 | `app/repositories/talent_repository.py`<br>`[RAG组] [P0]` | `class TalentRepository(BaseRepository[Talent])`：<br>- `get_by_mobile(self, db, mobile: str) -> Talent \| None`<br>- `paginate_talents(self, db, page: PageParams, *, filters: dict, skill: str \| None = None, work_years_min: int \| None = None, work_years_max: int \| None = None, org_ids: list[int] \| None = None)`<br>- `search_by_skills(self, db, skills: list[str], limit: int = 50) -> list[Talent]`<br>- `count_by_organization(self, db, org_ids: list[int]) -> dict[int, int]`<br>- `get_latest_portrait(self, db, talent_id: int) -> TalentPortrait \| None`<br>- `save_portrait(self, db, talent_id: int, data: dict) -> TalentPortrait` | 操作 `Talent` 与 `TalentPortrait` 两张表；`save_portrait` 自动查询当前最大 `version + 1` 追加版本快照；手机号查重仅查 `is_deleted == False`。 |
| 13 | `app/schemas/talent.py`<br>`[RAG组] [P1]` | `TalentCreate`、`TalentUpdate`、`TalentResponse`、`TalentPortraitResponse`、`SemanticSearchRequest`、`SemanticSearchResponse`、`TalentMergeRequest` | 对齐 `docs/api_document.md` 第十节。 |
| 14 | `app/services/talent_service.py`<br>`[RAG组] [P0]` | `class TalentService`（支持 `db: AsyncSession \| None = None`）：<br>- `create_talent`、`create_from_resume`、`page_list`、`get_detail`、`get_portrait`、`update_talent`、`delete_talent`<br>- `semantic_search`（调本组 `EmbeddingService` + `MilvusAdapter.search("talent_vectors")`）<br>- `refresh_portrait`（调 `await dispatch("talent", {"talent_id": id}, db=db)`）<br>- `save_portrait`（供 `[Agent组] TalentAgent.persist` 回调）<br>- `merge_talents`（在 `transaction(db)` 内把源档案关联记录迁移至目标主档并软删源档案） | 非管理员查询列表/详情时对手机号执行 `138****0001` 脱敏；新增/修改/删除人才时同步 `upsert/delete` 本组 `talent_vectors`（捕获 `ExternalServiceError` 记 warning，不阻断主库事务）；同时暴露候选池查询接口供 `[Agent组] MatchingService` 调用。 |
| 15 | `app/routers/talent_router.py`<br>`[RAG组] [P1]` | 实现 `/talents` 下 9 个接口 | 挂载 `PermissionEnum.TALENT_READ / WRITE / DELETE`，提 PR 替换 `"talents"` 占位路由。 |

##### 子域三：简历上传、查重与解析编排模块 `Resumes`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 16 | `app/repositories/resume_repository.py`<br>`[RAG组] [P0]` | `class ResumeRepository(BaseRepository[Resume])`：<br>`get_by_hash`、`list_by_talent`、`update_parse_result`、`replace_experiences` | 操作 `Resume` 与 `ResumeExperience` 表。 |
| 17 | `app/schemas/resume.py`<br>`[RAG组] [P1]` | `ResumeUploadResponse`、`ResumeParseTriggerRequest`、`ResumeResponse`、`ResumeParseResultResponse`、`ResumeBindRequest` | 对齐 `docs/api_document.md` 第十一节。 |
| 18 | `app/services/resume_service.py`<br>`[RAG组] [P0]` | `class ResumeService`（**必须支持 `def __init__(self, db: AsyncSession \| None = None)`**）：<br>- `upload_resume(self, upload_file, current_user) -> dict`<br>- `parse_resume(self, *, file_id: int \| None = None, resume_id: int \| None = None, force: bool = False, context: Any = None) -> dict`<br>- `save_parse_result(self, resume_id: int, parsed: dict, accuracy: float = 0.95, warnings: list[str] \| None = None) -> Resume`<br>- `dedupe_resume(self, *, resume_id: int \| None = None, text: str \| None = None, threshold: float = 0.92, context: Any = None) -> dict`<br>- `get_parse_result`、`bind_talent` | **关键契约**：① `parse_resume` 与 `dedupe_resume` 直接被 `[架构组] resume_skill.py` 调用！② 上传走 `[架构组] FileService.save_upload` + `sha256` 秒传；③ `dedupe_resume` 用本组 `TalentRepository.get_by_mobile` + `MilvusAdapter.search("talent_vectors")` 双重查重；④ `parse_resume` 调 `[Agent组] run_resume_workflow`；⑤ `bind_talent` 直接调本组 `TalentService.create_from_resume`。 |
| 19 | `app/routers/resume_router.py`<br>`[RAG组] [P1]` | 实现 `/resumes` 下 6 个接口 | 提 PR 替换 `"resumes"` 占位路由。 |

---

### 6.2 2 组：Agent 组 `[Agent组]`（LangGraph/6 大 Agent + 岗位管理 + 人岗匹配）

#### A. 本组核心定位与严格边界

- **为什么将「岗位管理 `positions`」与「人岗匹配 `matchings`」垂直归口 `[Agent组]`**：
  JD 智能解析、人岗匹配工作流（`matching_workflow`）、人岗匹配解释智能体（`matching_agent`）及 `[架构组]` 的 `matching_skill` 高度耦合岗位与匹配数据。将 `positions + matchings + agents/workflows` 统一交由 4 人规模的 `[Agent组]` 端到端闭环负责，彻底消除匹配链路的跨组等待！
- **⚠️ 最高优先级防重复警告（必读！）**：
  1. **严禁重写架构组已完成底座**：`BaseAgent`（`base_agent.py`）、`dispatch / dispatch_stream`（`ai/agent.py`）、`ConversationMemory`（`conversation_memory.py`）、`StateManager`（`state_manager.py`）、`Conversation / ChatMessage` ORM（`models/conversation.py`）均已 100% 完成，**直接继承或调用，严禁覆盖**！
  2. **严禁重写匹配打分算法**：`MatchingService` 与 `matching_workflow` 必须直接调用 `[架构组]` 已实现的 `compute_match_score`（`matching_skill.py`）与 `MatchStrategyFactory`（`patterns/strategy.py`），**严禁再写第二套打分代码**！
  3. **严禁越界写他组业务模块**：`TalentService / ResumeService` 归 `[RAG组]`、`AssessmentService / AnalyticsService` 归 `[Vanna组]`、`TrainingService` 归 `[Dify组]`——本组 6 个 Agent 读写这些领域数据时，**一律调用上述公开 Service 方法或 8 大 Skills，严禁直触其 ORM 表**！

#### B. 本组逐文件详细开发清单（共 21 个待开发代码文件，分三大子域）

##### 子域一：LangGraph 编排 + 3 条工作流 + 6 个领域 Agent + 会话中心（13 个文件 + `schemas/agent.py`）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/ai/workflows/graph_builder.py`<br>`[Agent组] [P0]` | `WorkflowState`、`build_graph`、`run_workflow`、`route_by_status` | 封装 LangGraph `StateGraph`（未装 `langgraph` 时自动降级内置轻量状态图执行器，确保离线单测可跑）；每步自动调 `state_manager.save(thread_id, state)` 持久化断点，受 `settings.AGENT_MAX_STEPS` 防死循环保护。 |
| 2 | `app/ai/workflows/resume_workflow.py`<br>`[Agent组] [P0]` | `async def run_resume_workflow(resume_id: int, file_path: str, *, db: AsyncSession \| None = None, force: bool = False) -> dict[str, Any]` | 5 节点图：`extract`（调 `[RAG组] document_loader.load`）→ `clean` → `structure`（调本组 `ResumeAgent`）→ `validate`（失败重试 ≤ 2 次 + 叠加 `[架构组] extract_skills`）→ `persist`（回调 `[RAG组] ResumeService.save_parse_result` 幂等落库）。 |
| 3 | `app/ai/workflows/matching_workflow.py`<br>`[Agent组] [P1]` | `async def run_matching_workflow(position_id: int, *, strategy: str = "hybrid", top_k: int = 20, min_score: float = 60.0, with_ai_explain: bool = True, db: AsyncSession \| None = None) -> dict[str, Any]` | 串联本组 `PositionService` + `MatchingService`（底层复用 `[架构组] MatchStrategyFactory`）→ 对 Top 候选人用 `asyncio.Semaphore(5)` 限流调 `MatchingAgent` 补齐 AI 解释（失败自动退化规则文案）→ 幂等回写批次结果。 |
| 4 | `app/ai/workflows/analysis_workflow.py`<br>`[Agent组] [P2]` | `async def run_analysis_workflow(topic: str, *, organization_id: int \| None = None, db: AsyncSession \| None = None) -> dict[str, Any]` | 人才数据决策工作流：`collect_metrics`（调 `[Vanna组] AnalyticsService` 与 `sql_query` 技能，**严禁直连 PgSQL**）→ `talent_review` → `team_structure` → `recommendation`（输出结构化管理洞察与建议）。 |
| 5–10 | `app/ai/agents/{resume,talent,matching,assessment,training,assistant}_agent.py`<br>`[Agent组] [P0–P2]` | 6 个 `@register_agent(...)` 子类继承 `BaseAgent` | - `ResumeAgent`：解析前 12000 字简历 JSON；<br>- `TalentAgent`：经 `[RAG组] TalentService` 读档案并回调 `save_portrait`；<br>- `MatchingAgent`：基于规则四维分生成 `{highlights, gaps, suggestion, explanation}`（禁改总分）；<br>- `AssessmentAgent`：主观题评分与能力报告，经 `[Vanna组] AssessmentService` 落库；<br>- `TrainingAgent`：结合短板与 `[Dify组] TrainingService` 课程池生成推荐理由；<br>- `AssistantAgent`：全局多轮助手，结合 `ConversationMemory` 路由调用 8 大 Skills 并流式输出 `citation + delta`。 |
| 11 | `app/repositories/conversation_repository.py`<br>`[Agent组] [P1]` | `class ConversationRepository(BaseRepository[Conversation])`：<br>`create_conversation`、`get_by_session_key_or_id`、`list_by_user`、`append_message`、`get_recent_messages`、`paginate_messages` | `append_message` 与 `get_recent_messages` 精确对齐 `conversation_memory.py` 第 228–258 行回调签名；强制校验 `Conversation.user_id == current_user.id` 防越权。 |
| 12–13 | `app/services/agent_service.py`、`app/routers/agent_router.py`（+ 新建 `app/schemas/agent.py`）<br>`[Agent组] [P0/P1]` | 实现 `/agents` 下全部 6 个接口（同步对话、SSE 流式对话、会话列表、历史消息、异步任务提交与轮询） | 入口必调 `set_context` 与 `TokenManager().check_quota`；预留 `[Dify组]` 双模开关（配 `DIFY_APP_ID_*` 时委托 `DifyAdapter`，异常无缝回退本地 `dispatch`）。 |

##### 子域二：岗位管理与 JD 解析模块 `Positions`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 14 | `app/repositories/position_repository.py`<br>`[Agent组] [P0]` | `class PositionRepository(BaseRepository[Position])`（**支持 `def __init__(self, db: AsyncSession \| None = None)`**）：<br>- `get_by_code(self, db, code: str) -> Position \| None`<br>- `list_open_by_org(self, db, org_ids: list[int] \| None = None) -> list[Position]`<br>- `search_by_title(self, db: AsyncSession \| None = None, *, keyword: str = "", status: str = "open", limit: int = 10, context: Any = None) -> list[Position]`<br>- `upsert_requirements(self, db, position_id: int, items: list[dict]) -> list[PositionRequirement]` | **关键契约**：`search_by_title` 直接被 `[架构组] matching_skill.PositionSearchSkill` 调用（当未传 `db` 时内部用 `session_scope()`）！ |
| 15 | `app/schemas/position.py`<br>`[Agent组] [P1]` | `PositionCreate`、`PositionUpdate`、`PositionStatusUpdate`、`PositionQuery`、`PositionResponse` | 对齐 `docs/api_document.md` 第九节。 |
| 16 | `app/services/position_service.py`<br>`[Agent组] [P0]` | `class PositionService`：`create_position`、`page_list`、`get_detail`、`update_position`、`change_status`、`delete_position`、`parse_jd`、`_sync_position_vector` | 状态机强校验 `closed` 不可直接改回 `open`（抛 `BusinessRuleError(1002)`）；`parse_jd` 调 `[RAG组] document_loader` + `llm_manager` 提取结构化任职要求；`_sync_position_vector` 调 `[RAG组] EmbeddingService + MilvusAdapter` 写 `position_vectors`（异常仅记 warning）。 |
| 17 | `app/routers/position_router.py`<br>`[Agent组] [P1]` | 实现 `/positions` 下 7 个接口 | 提 PR 替换 `"positions"` 占位路由。 |

##### 子域三：人岗精准匹配模块 `Matchings`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 18 | `app/repositories/matching_repository.py`<br>`[Agent组] [P0]` | `class MatchingRepository(BaseRepository[MatchingRecord])`：<br>`save_batch`、`get_latest_by_position`、`get_by_batch_id`、`get_history_by_talent` | 按 `batch_id` 保存批次快照，支持按岗位查最新 TopK、按人才查历史匹配记录。 |
| 19 | `app/schemas/matching.py`<br>`[Agent组] [P1]` | `MatchingRunRequest`、`MatchingBatchResponse`、`MatchingItemResponse`、`MatchingExplainRequest`、`MatchingExplainResponse` | 对齐 `docs/api_document.md` 第十四节。 |
| 20 | `app/services/matching_service.py`<br>`[Agent组] [P0]` | `class MatchingService`（**必须支持 `def __init__(self, db: AsyncSession \| None = None)`**）：<br>- `calculate(self, *, talent_id: int, position_id: int, context: Any = None) -> dict`<br>- `list_candidates(self, *, position_id: int, top_k: int = 10, min_score: float = 0, context: Any = None) -> list[dict]`<br>- `run_matching`、`get_by_batch`、`get_talent_history`、`explain_match` | **关键契约**：`calculate` 与 `list_candidates` 直接被 `[架构组] matching_skill.py` 调用！候选池通过 `[RAG组] TalentService` 获取，打分直接复用 `[架构组] compute_match_score` 与 `MatchStrategyFactory.create(strategy)`；`explain_match` 调用本组 `MatchingAgent`（失败自动退化规则化说明）。 |
| 21 | `app/routers/matching_router.py`<br>`[Agent组] [P1]` | 实现 `/matchings` 下 5 个接口 | 提 PR 替换 `"matchings"` 占位路由。 |

---

### 6.3 3 组：Vanna 组 `[Vanna组]`（NL2SQL/统计看板 + 在线智能测评）

#### A. 本组核心定位与严格边界

- **为什么将「在线智能测评 `assessments`」垂直归口 `[Vanna组]`**：
  在线测评的核心是结构化题库管理、防泄密抽题开考、客观题规则计分、各能力维度得分聚合以及向 PgSQL `fact_assessment_history` 分析宽表供给测评指标数据，与 `[Vanna组]` 的数据工程及统计聚合专长高度一致！
- **⚠️ 防重复与边界红线**：
  - ❌ **严禁**重建或修改 `app/models/{analytics,assessment}.py`、`analytics_schema.py`、`postgres.py`、`sql_skill.py`（均已冻结）；
  - ❌ **严禁**在 `AssessmentService` 中手写 Prompt 做主观题大模型评阅——完成客观题确定性判分后，统一调用 `[架构组] await dispatch("assessment", payload, db=db)` 委托 `[Agent组] AssessmentAgent` 生成主观题得分与能力报告；
  - ❌ **严禁**直接 `import pymilvus`（相似题查重读写 `question_vectors` 统一调用 `[RAG组] EmbeddingService + MilvusAdapter`）。

#### B. 本组逐文件详细开发清单（共 11 个待开发代码文件，分两大子域）

##### 子域一：AST SQL 安全校验 + Vanna 问数引擎 + PgSQL ETL + 统计分析看板 `Analytics`（7 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/adapters/nl2sql/sql_validator.py`<br>`[Vanna组] [P0]` | `ALLOWED_TABLES`、`SQLValidationError`、`validate_select_sql`、`inject_data_scope`、`inject_limit` | 基于 `sqlglot.parse(sql, read="postgres")` 强制单条 `SELECT`、仅允许 6 张分析宽表白名单、拦截系统表与危险函数、注入 `organization_id` 范围条件与 `LIMIT <= 200`。 |
| 2 | `app/adapters/nl2sql/vanna_adapter.py`<br>`[Vanna组] [P0]` | `NL2SQLResponse`、`VannaAdapter`（`train`、`ask`、`infer_chart`） | **关键契约**：`ask(question=..., user_scope=..., max_rows=...)` 直接被 `sql_skill.py` 调用（兼容 `user_scope` 为 `dict` 或 `str`）；未装 `vanna` 时内置 `render_pgsql_ddl() + llm_manager.chat(purpose="sql")` 回退生成，经 `sql_validator` 校验后在 `pg_session()`（`statement_timeout='30s'`）只读执行。 |
| 3 | `scripts/sync_analytics.py`（新建）<br>`[Vanna组] [P1]` | `async def sync_all(*, full_refresh: bool = False) -> dict[str, int]` | 从 MySQL（含本组 `assessments` 表及其他组主表只读抽取）增量同步至 PgSQL 6 张宽表（使用 `get_pg_session_factory()` + `ON CONFLICT DO UPDATE` 幂等提交）。 |
| 4 | `scripts/train_vanna.py`（扩充）<br>`[架构组]+[Vanna组] [P1]` | 扩充 `BUSINESS_TERMS` 与 `EXAMPLE_QUERIES` 至 **≥ 40 条** | 保持 `--output` / `--dry-run` 兼容，全部示例 SQL 通过 `validate_select_sql` 校验。 |
| 5–7 | `app/schemas/analytics.py`、`app/services/analytics_service.py`、`app/routers/analytics_router.py`<br>`[Vanna组] [P0/P1]` | 实现 `/analytics` 下 6 个接口（总览、分布、趋势、智能问数、报告生成与列表） | `PGSQL_ENABLED=False` 时看板接口自动降级 MySQL 聚合统计；报告导出生成 `.docx`/HTML 后调 `[架构组] FileService.save_bytes` 落盘；提 PR 替换 `"analytics"` 占位路由。 |

##### 子域二：在线人才智能测评模块 `Assessments`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 8 | `app/repositories/assessment_repository.py`<br>`[Vanna组] [P1]` | `class AssessmentRepository(BaseRepository[Assessment])`：<br>- `draw_questions(self, db, *, dimensions: list[str], difficulty: str \| None, count: int) -> list[AssessmentQuestion]`<br>- `save_answers(self, db, assessment_id: int, answers: list[dict]) -> list[AssessmentAnswer]`<br>- `save_result(self, db, assessment_id: int, result_data: dict) -> AssessmentResult`<br>- `get_result_by_assessment(self, db, assessment_id: int) -> AssessmentResult \| None`<br>- `stats_by_dimension(self, db, org_ids: list[int] \| None = None) -> list[dict]` | 操作 `Assessment / AssessmentQuestion / AssessmentAnswer / AssessmentResult` 四张表；保证 `(assessment_id, question_id)` 唯一幂等更新。 |
| 9 | `app/schemas/assessment.py`<br>`[Vanna组] [P1]` | `AssessmentCreate`、`QuestionForCandidateSchema`、`AssessmentStartResponse`、`AssessmentSubmitRequest`、`AssessmentResultResponse` | **防泄密铁律**：考生端开考响应 `QuestionForCandidateSchema` **物理剔除 `answer` 标准答案与解析字段**！对齐 `docs/api_document.md` 第十二节。 |
| 10 | `app/services/assessment_service.py`<br>`[Vanna组] [P1]` | `class AssessmentService`（支持 `db: AsyncSession \| None = None`）：<br>`create_assessment`、`start_assessment`、`submit_answers`、`save_ai_evaluation`、`get_result`、`page_list` | `start_assessment` 校验未取消且未交卷，置 `status="in_progress"`；`submit_answers` 校验 `deadline`（超时打 `overtime=True` 标签），完成单选/多选客观题确定性计分与各维度得分汇总，随后调 `await dispatch("assessment", ...)` 交由 `[Agent组] AssessmentAgent` 评阅主观题并生成能力报告，完成后联动 `[RAG组] TalentService` 更新测评等级。 |
| 11 | `app/routers/assessment_router.py`<br>`[Vanna组] [P1]` | 实现 `/assessments` 下 5 个接口 | 提 PR 替换 `"assessments"` 占位路由。 |

---

### 6.4 4 组：Dify 组 `[Dify组]`（Dify 低代码网关 + 个性化智能培训）

#### A. 本组核心定位与严格边界

- **为什么将「个性化智能培训 `trainings`」垂直归口 `[Dify组]`**：
  培训模块包含课程库、培养计划、学习进度防刷上报、计划滞后预警（`GET /trainings/warnings`）及定期督办提醒，天然契合 Dify 低代码工作流（`DifyAdapter.run_workflow`）编排通知推送与个性化培养方案生成，使 2 人规模的 `[Dify组]` 同时具备完整业务闭环与低代码平台集成能力！
- **⚠️ 防重复与边界红线**：
  - ❌ **严禁**新建 `/api/v1/dify/*` 等业务层重复路由，严禁把 Dify 变成硬依赖（未配 `DIFY_API_KEY` 时培训模块全部接口与平台本地 Agent 必须 100% 正常工作！）；
  - ❌ **严禁**自写重试熔断（直接复用 `ai_retry / SimpleCircuitBreaker`）或重建 `app/models/training.py`；
  - ❌ **严禁**在 `TrainingService` 中直拼 Prompt 调 LLM——本地 AI 课程推荐统一调用 `await dispatch("training", payload, db=db)`（若配置了 `DIFY_WORKFLOW_TRAINING` 则优先尝试 `DifyAdapter.run_workflow`，失败自动回退 `dispatch("training")`）。

#### B. 本组逐文件详细开发清单（共 8 个专属文件 + 1 处跨组协同分支）

##### 子域一：Dify 低代码工作流网关与 SSE 协议适配（4 个文件 + 1 处协同分支）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 1 | `app/adapters/dify/sse.py`（新建）<br>`[Dify组] [P0]` | `iter_dify_sse(response)`、`normalize_dify_event(raw_event)` | 将 Dify 原生流式事件统一归一化为平台标准 SSE 事件（`start / message{"delta"} / citation / done / error`），前端零感知后端差异。 |
| 2 | `app/adapters/dify/dify_adapter.py`<br>`[Dify组] [P0]` | `class DifyAdapter`：`chat`、`chat_stream`、`run_workflow`、`upload_document`、`list_documents`、`delete_document` | 全异步 `httpx.AsyncClient` + `X-Trace-Id` 透传 + `@ai_retry`；错误统一映射为 `ExternalServiceError(5001)`。 |
| 3 | `app/adapters/dify/health.py`（新建）<br>`[Dify组] [P1]` | `async def check_dify() -> dict[str, Any]` | 未配置时返回 `status="disabled"`，配置后探测连通性并对齐 `health.py` 返回结构。 |
| 4 | `scripts/sync_dify_workflows.py`（新建）<br>`[Dify组] [P2]` | 支持 `--check` 校验工作流可用性、`--sync-kb` 将已索引知识文档单向同步至 Dify Dataset | 幂等运维脚本，只读调 `[RAG组] KnowledgeService` 接口或公开方法。 |
| 5 | `app/services/agent_service.py`（协同分支）<br>`[Agent组]+[Dify组] [P1]` | `_should_use_dify(agent_type)`、`_chat_via_dify(...)` | 配置了 `DIFY_APP_ID_*` 时走 `DifyAdapter`，捕获 `ExternalServiceError` 自动无缝回退本地 `dispatch`；PR 须经 `[Agent组]` 组长联合 Review。 |

##### 子域二：个性化智能培训与督办模块 `Trainings`（4 个文件）

| 序号 | 文件路径（标签） | 必须实现的类与精确方法签名 | 实现细节与上下游对接规范 |
| --- | --- | --- | --- |
| 6 | `app/repositories/training_repository.py`<br>`[Dify组] [P1]` | `class TrainingRepository(BaseRepository[Course])`：<br>- `paginate_courses`、`create_plan_with_courses`、`get_plan_detail`<br>- `get_learning_record(self, db, plan_id: int, course_id: int, talent_id: int) -> LearningRecord \| None`<br>- `upsert_learning_progress(self, db, ...)`<br>- `calc_plan_completion_rate(self, db, plan_id: int) -> float`<br>- `list_lagging_plans(self, db, *, org_ids: list[int] \| None = None) -> list[dict]` | 操作 `Course / TrainingPlan / PlanCourse / LearningRecord` 四张表；`list_lagging_plans` 筛选距离截止日期 `< 3` 天且完成率 `< 50%`（或已逾期未完成）的培养计划。 |
| 7 | `app/schemas/training.py`<br>`[Dify组] [P1]` | `CourseCreate`、`CourseResponse`、`TrainingPlanCreate`、`TrainingPlanResponse`、`ProgressReportRequest`、`CourseRecommendRequest`、`CourseRecommendResponse`、`TrainingWarningItem` | 对齐 `docs/api_document.md` 第十三节。 |
| 8 | `app/services/training_service.py`<br>`[Dify组] [P1]` | `class TrainingService`（支持 `db: AsyncSession \| None = None`）：<br>`create_course`、`page_courses`、`create_plan`、`page_plans`、`get_plan_detail`、`report_progress`、`recommend_courses`、`list_warnings` | **防刷与双模编排**：① `report_progress` 强制单调递增校验 `new_progress >= old_progress`（回退抛 `BusinessRuleError(1002)`），当单次进度跃升幅度与 `study_minutes` 严重失衡（如 1 分钟涨 80%）时打标 `suspicious=True`；② `recommend_courses` 经 `[RAG组] TalentService` / `[Vanna组] AssessmentService` 读取员工能力短板，优先调 `DifyAdapter.run_workflow`（未配置则调 `await dispatch("training", ...)`）生成推荐课程列表；③ `list_warnings` 支持可选触发 `DifyAdapter.run_workflow` 发送督办通知。 |
| 9 | `app/routers/training_router.py`<br>`[Dify组] [P1]` | 实现 `/trainings` 下 9 个接口 | 挂载 `PermissionEnum.TRAINING_READ / WRITE`，提 PR 替换 `"trainings"` 占位路由。 |

---

## 七、新增文件与跨组变更合规守门规则

### 7.1 新增文件强制标签模板（不写则 CI 挂红！）

仓库已启用自动化守门测试 `tests/test_code_tags.py`（仅允许 `架构组 / RAG组 / Agent组 / Vanna组 / Dify组` 五种组标签）。任何组新增 `.py` 文件（例如 `[架构组]` 新增 `file_router.py` / `audit_router.py`、`[Agent组]` 新增 `schemas/agent.py`、`[Vanna组]` 新增 `scripts/sync_analytics.py`、`[Dify组]` 新增 `sse.py` / `health.py`），文件开头模块 docstring **必须包含合规标签行**：

```python
"""
模块：app/routers/file_router.py
职责：通用文件上传、鉴权下载、批量上传与元数据接口。

标签：[架构组] [P1]  依赖：[架构组] FileService
"""
```

提交前本地必跑校验命令（离线毫秒级完成）：

```bash
python3 -m pytest tests/test_code_tags.py -q
```

### 7.2 共享入口文件变更审批表

| 共享文件 | 唯一主责合入组 | 其他组需要变更时的标准操作 |
| --- | --- | --- |
| `app/routers/router.py` | `[架构组]` | 各组在本组 `xxx_router.py`（如 `[RAG组]` 的 `knowledge/talent/resume`、`[Agent组]` 的 `agent/position/matching`、`[Vanna组]` 的 `analytics/assessment`、`[Dify组]` 的 `training`）自测通过后，提 PR 将 `_STUB_MODULES` 对应项替换为真实路由，由 `[架构组]` Review 合入 |
| `app/config/settings.py` / `.env.example` / `requirements.txt` | `[架构组]` | 提 PR 注明新增配置项默认值及所属组，禁止删除或重命名已有配置项 |
| `app/services/agent_service.py` | `[Agent组]` 牵头 + `[Dify组]` 协同 | 主体由 `[Agent组]` 开发；Dify 双模分支由 `[Dify组]` 提 PR 经 `[Agent组]` 组长共同签字合入 |
| `app/models/*.py` | `[架构组]` + 领域归属组 | 25 张 MySQL 表与 6 张 PgSQL 表结构已冻结；确需增补非破坏性字段时，必须同步运行 `scripts/dump_orm_mapping.py` 并保证 `tests/sql/` 全绿 |
