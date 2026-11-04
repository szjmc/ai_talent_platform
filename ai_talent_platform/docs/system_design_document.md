# AI 数字化人才平台 · 系统总体与详细设计文档（SDD）

> **文档版本**：v2.0.0 ｜ **更新日期**：2026-11-04
> **配套文档**：[需求规格说明书（SRS）](requirements_specification.md) ｜ [六大研发组职责边界与防重复开发总规范](group_boundaries_and_tasks.md) ｜ [架构总览图](architecture.md) ｜ [数据库设计字典](database_design.md) ｜ [API 接口文档](api_document.md)

---

## 目录

1. [总体架构与分层单向依赖设计](#1-总体架构与分层单向依赖设计)
2. [四库混合存储架构与键空间详细设计](#2-四库混合存储架构与键空间详细设计)
3. [公共底座与 AI 中台核心组件详细设计（已落地基线）](#3-公共底座与-ai-中台核心组件详细设计已落地基线)
4. [六大核心业务与 AI 链路端到端时序设计](#4-六大核心业务与-ai-链路端到端时序设计)
5. [跨组核心集成契约与桥接协议设计](#5-跨组核心集成契约与桥接协议设计)
6. [纵深安全防线、故障降级与可观测性设计](#6-纵深安全防线故障降级与可观测性设计)

---

## 1. 总体架构与分层单向依赖设计

### 1.1 六层架构到代码目录的精确映射

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ① 业务应用层（PC 管理端 / 员工自助端 / 移动端 / 第三方系统）                     │
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        │ HTTP JSON / Multipart / SSE (text/event-stream)
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ ② 服务接口层：app/routers/*  +  app/schemas/*  +  app/core/*                  │
│   TraceIdMiddleware → RateLimit → JWT/RBAC(require_permission) → AIContext  │
└───────────────────┬───────────────────────────────────────┬──────────────────┘
                    │ 领域业务编排                           │ AI 任务与对话编排
                    ▼                                       ▼
┌─────────────────────────────────────┐ ┌──────────────────────────────────────┐
│ ③-A 业务服务层：app/services/*       │ │ ③-B AI 中台与智能能力层：app/ai/*      │
│   Auth / User / Org / Talent        │◄┤   agent.py(dispatch) / BaseAgent     │
│   Position / Resume / Matching      ├─►   workflows/* (LangGraph)            │
│   Assessment / Training / File      │ │   skills/* (8 个标准技能，唯一工具门面) │
│   Knowledge / Analytics / Audit     │ │   rag/* (Loader/Splitter/Retriever)  │
└───────────────────┬─────────────────┘ │   core/* (LLMManager/TokenManager)   │
                    │                   │   memory/* (Conversation/State)      │
                    │                   └───────────────────┬──────────────────┘
                    │ ORM 调用                              │ 协议适配调用
                    ▼                                       ▼
┌─────────────────────────────────────┐ ┌──────────────────────────────────────┐
│ ④ 数据访问层：app/repositories/*     │ │ ④ 外部能力适配层：app/adapters/*       │
│   BaseRepository (17 通用异步方法)    │ │   llm/* (OpenAI / Qwen / Ollama)     │
│   app/models/* (MySQL 25 + PgSQL 6) │ │   vector_store/milvus_adapter.py     │
└───────────────────┬─────────────────┘ │   nl2sql/{sql_validator,vanna}.py    │
                    │                   │   dify/{dify_adapter,sse}.py         │
                    │                   │   storage/{base,local_storage}.py    │
                    ▼                   └───────────────────┬──────────────────┘
┌───────────────────────────────────────────────────────────┴──────────────────┐
│ ⑤ 基础设施与存储层：app/infrastructure/{database,cache,vector,storage,messaging}│
│   MySQL 8.0 (OLTP) │ PostgreSQL 16 (OLAP) │ Milvus 2.4 (Vector) │ Redis 7    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 分层单向依赖铁律（严禁逆向或跨层越权）

| 所在层 | 允许调用的下层组件 | 严禁出现的越层/逆向行为（Review 一票否决） |
| --- | --- | --- |
| `app/routers/*` | `app/schemas/*`、`app/services/*`、`app/core/{dependencies,response,pagination}` | 严禁注入或调用 `Repository`、严禁操作 `AsyncSession` 写查询、严禁直接调用 `llm_manager` 或 `MilvusAdapter`、严禁写业务状态机判断 |
| `app/services/*` | 本模块 `Repository`、其他模块公开 `Service`、`app/ai/agent.dispatch`、`app/patterns/strategy`、`MilvusAdapter`/`EmbeddingService`、`get_storage()`、`EventBus` | 严禁接收 FastAPI `Request`/`Response`、严禁跨模块直接 `import` 他人 `Repository`（`UserService` 内部关联 `OrganizationRepository` 除外）、严禁拼接裸 SQL |
| `app/ai/{agents,workflows}/*` | `app/ai/skills/*`、公开 `Service`、`llm_manager`、`output_parser`、`ConversationMemory`、`StateManager`、`document_loader` | **严禁写任何 SQL 或直接操作 ORM Model**、严禁跳过 `Service` 直接改业务表、严禁绕过 `AIContext` 自行鉴权 |
| `app/ai/skills/*` | 公开 `Service`、`Retriever`、`VannaAdapter`（均通过 `import_dependency` 惰性加载） | 严禁信任入参中的 `user_id`/`role`/`organization_id`（一律从 `load_skill_context()` 获取） |
| `app/repositories/*` | `BaseRepository`、`app/models/*`、SQLAlchemy 2.0 `select/update/func` | **严禁调用 `await db.commit()`**（事务边界由 `get_db()` 或 `session_scope()` 统一控制）、严禁写业务规则或调外部 HTTP |
| `app/adapters/*` | `app/infrastructure/*`、`httpx.AsyncClient`、`pymilvus`、`vanna`、`sqlglot`、`ai_retry` | 严禁感知具体业务模型（如 `Talent`、`Position`），只做纯粹协议转换、安全校验与错误映射 |

---

## 2. 四库混合存储架构与键空间详细设计

### 2.1 MySQL 8.0 业务主库设计（25 张表，OLTP 强一致）

所有表继承 `app.models.base.BaseModel`（含 `id` BIGINT 主键、`version` 乐观锁、`created_at`、`updated_at`、`is_deleted`、`deleted_at`、`created_by`、`updated_by`），按领域划分为 9 个聚合根域（完整字段见 `docs/orm_mapping.md` 与 `docs/database_design.md`）：

| 领域聚合 | 表清单 | 唯一写入归属组 | 关键一致性与索引设计 |
| --- | --- | --- | --- |
| 身份与组织域 | `users`、`roles`、`user_roles`、`organizations` | `[业务组]`（✅ 已落地） | `organizations.path` 物化路径（形如 `/1/3/`）支撑 `dept_sub` 子树 `LIKE` 高效过滤 |
| 岗位域 | `positions`、`position_requirements` | `[业务组]` | `positions.code` 唯一；`requirements` JSON 列存储最低学历/工龄/技能与权重 |
| 人才与画像域 | `talents`、`talent_portraits` | `[业务组]`（`talent_agent` 经 `TalentService` 写画像） | 未删除手机号业务唯一；`talent_portraits` 按 `(talent_id, version)` 递增留存历史画像快照 |
| 简历与文件域 | `files`、`resumes`、`resume_experiences` | `[业务组]`（`resume_workflow` 经 `ResumeService` 回写） | `files.file_hash` 与 `resumes.file_hash`（SHA-256）支撑秒传与重复导入拦截 |
| 人岗匹配域 | `matching_records` | `[业务组]` | `batch_id` 标识单次跑批快照，记录总分、`dimension_scores`、`highlights`、`gaps`、`suggestion` |
| 智能测评域 | `assessments`、`assessment_questions`、`assessment_answers`、`assessment_results` | `[业务组]` | `(assessment_id, question_id)` 答题唯一约束；`assessment_questions.answer` 对考生端物理隔离 |
| 智能培训域 | `courses`、`training_plans`、`plan_courses`、`learning_records` | `[业务组]` | `learning_records.progress` 单调递增校验，`suspicious` 标记防挂机刷课 |
| 知识库域 | `knowledge_docs`、`knowledge_chunks` | `[RAG组]` | `knowledge_chunks.milvus_pk` 与 Milvus `knowledge_vectors.chunk_id` 双向锚定 |
| 会话与审计域 | `conversations`、`chat_messages`（`[Agent组]`）；`audit_logs`（`[业务组]`） | `[Agent组]` / `[业务组]` | `audit_logs` 仅追加写入（Append-Only），索引覆盖 `(module, action, created_at)` 与 `trace_id` |

### 2.2 PostgreSQL 16 分析与时序库设计（6 张宽表，OLAP 读写分离）

使用独立 `AnalyticsBase`（`app/models/analytics.py`），与 MySQL `Base` 元数据物理隔离：

| 表名 | 类型与分区策略 | 核心分析用途 | 数据来源（由 `[Vanna组] scripts/sync_analytics.py` 增量同步） |
| --- | --- | --- | --- |
| `dim_organization` | 维度表（`org_id` 唯一） | 部门层级与路径维表，支撑按部门/事业群聚合与行级权限关联 | MySQL `organizations` |
| `dim_talent_wide` | 宽表（`talent_id` 唯一） | 预关联人才主档+最新画像等级+技能数组（`TEXT[]`），避免 NL2SQL 多表复杂 JOIN | MySQL `talents` + `talent_portraits` + `organizations` |
| `fact_talent_snapshot` | 事实表（按 `snapshot_month` RANGE 分区） | 各部门月度在职人数、新增/离职人数、平均工龄、骨干占比快照 | MySQL `talents` 按月聚合 |
| `fact_assessment_history` | 事实表（按 `scored_at` RANGE 分区） | 历次测评总分、维度 JSONB 得分、等级时序变化分析 | MySQL `assessments` + `assessment_results` |
| `fact_training_track` | 事实表（按 `recorded_at` RANGE 分区） | 学时投入、完成进度、课程类别学习轨迹分析 | MySQL `learning_records` + `training_plans` + `courses` |
| `fact_behavior_event` | 事实表（按 `event_at` RANGE 分区） | 平台关键操作与 AI 调用行为事件流统计 | MySQL `audit_logs` |

> **分区保障**：`scripts/init_pgsql.py` 已自动为 4 张事实表创建 `_default` 默认分区，后续按年度/季度滚动挂载子分区。

### 2.3 Milvus 2.4 向量检索库设计（4 大专属集合）

集合定义以 `scripts/init_milvus.py` 为唯一标准（默认维度 `EMBEDDING_DIM=1024`，索引 `IVF_FLAT(nlist=1024)`，度量 `COSINE`，检索 `nprobe=16`），**全平台仅允许 `[RAG组]` 的 `MilvusAdapter` 执行集合读写**：

| 集合名称 | 主键字段（INT64） | 标量过滤字段（Scalar Schema） | 写入触发方 | 检索消费方 |
| --- | --- | --- | --- | --- |
| `knowledge_vectors` | `chunk_id`（对应 `knowledge_chunks.id`） | `doc_id:int64`、`organization_id:int64`、`allowed_roles:json`、`category:varchar` | `[RAG组] RAGPipeline.index_document` | `[RAG组] Retriever` → `knowledge_search` 技能 |
| `talent_vectors` | `talent_id`（对应 `talents.id`） | `organization_id:int64`、`status:varchar`、`skill_tags:json` | `[业务组] TalentService`（调 `MilvusAdapter`） | `[业务组] TalentService.semantic_search`、`ResumeService.dedupe_resume`、`MatchingService` |
| `position_vectors` | `position_id`（对应 `positions.id`） | `organization_id:int64`、`status:varchar`、`level:varchar` | `[业务组] PositionService`（调 `MilvusAdapter`） | `[业务组] MatchingService` |
| `question_vectors` | `question_id`（对应 `assessment_questions.id`） | `dimension:varchar`、`difficulty:varchar` | `[业务组] AssessmentService`（调 `MilvusAdapter`） | `[业务组] AssessmentService`（相似题去重与抽题） |

### 2.4 Redis 7 键空间隔离规范（防止跨组 Key 冲突）

| Key 前缀规范 | 归属组 / 模块 | 数据结构与内容 | 默认 TTL | 降级策略（`REDIS_ENABLED=false`） |
| --- | --- | --- | --- | --- |
| `ai:token:{user_id}:{YYYYMMDD}` | `[架构组]` `TokenManager` | JSON/计数器：当日已消耗 Token 与费用 | 当日午夜过期 | 进程内字典 `_USAGE_STORE` |
| `chat:mem:{conversation_id}` | `[架构组]` `ConversationMemory` | JSON 数组：最近 N 轮对话消息窗口 | 3600s | 进程内 `_MEMORY_STORE` + MySQL `chat_messages` 回填 |
| `chat:summary:{conversation_id}` | `[架构组]` `ConversationMemory` | String：超长历史对话压缩摘要 | 3600s | 进程内 `_SUMMARY_STORE` |
| `wf:state:{thread_id}` | `[架构组]` `StateManager` | JSON：LangGraph 工作流节点状态快照 | 86400s | 进程内 `_MEMORY_STATES` |
| `rag:emb:{model}:{sha256}` | `[RAG组]` `EmbeddingService` | JSON `list[float]`：高频查询词向量缓存 | 86400s | 进程内 `functools.lru_cache` / 有界字典 |
| `rag:qa:{scope_hash}:{q_hash}` | `[RAG组]` `RAGPipeline` | JSON：同权限范围下热点制度问答缓存 | 600s | 跳过缓存直接走检索生成 |
| `nl2sql:cache:{scope_hash}:{q_hash}` | `[Vanna组]` `VannaAdapter` | JSON：同数据范围下热点问数结果缓存 | 300s | 跳过缓存直接走校验查询 |
| `org:tree:all` | `[业务组]` `OrganizationService` | JSON：全公司部门树缓存（变更时 `delete_keys("org:*")`） | 1800s | 直接查询 MySQL |

---

## 3. 公共底座与 AI 中台核心组件详细设计（已落地基线）

以下组件**已由 `[架构组]` 100% 编码实现并通过 59 项自动化单元测试验收**，作为全平台冻结基线供其余 5 组直接调用：

### 3.1 `AIContext` 请求级安全上下文（`app/ai/context.py`）

- 基于 `ContextVar` 实现协程隔离，携带 `user_id`、`roles`、`organization_id`、`trace_id`、`conversation_id`、`extra`，内置 `data_scope` 属性推导（`all / dept_sub / dept / self`）。
- **调用规范**：`AgentService`、`KnowledgeService`、`AnalyticsService` 在进入 AI 链路前调用 `set_context(current_user, conversation_id=...)`（或 `with context_scope(...):`）；下游 `BaseAgent`、`BaseSkill`、`Retriever`、`VannaAdapter` 统一调用 `get_context()`（未设置时安全返回匿名上下文，不抛异常）。

### 3.2 `LLMManager` 多模型统一门面（`app/ai/core/llm_manager.py`）

- 单例 `llm_manager`，底层通过 `llm_registry` 管理 `OpenAIAdapter`、`QwenAdapter`（默认 `qwen-plus` + `text-embedding-v3`）、`OllamaAdapter`。
- 按 `purpose`（`chat / resume / matching / assessment / training / sql / summary`）自动经 `model_selector` 选择最优模型与温度参数，内置 `TokenManager` 日配额拦截、故障降级链切换与耗时/Token 统一埋点。
- **标准调用方式**：
  - 对话生成：`res = await llm_manager.chat(messages, purpose="resume", user_id=ctx.user_id, trace_id=ctx.trace_id)`
  - 流式生成：`async for delta in llm_manager.stream(messages, purpose="chat"): ...`
  - 文本向量化：`vectors = await llm_manager.embed(texts)`

### 3.3 `BaseSkill` 8 大标准技能体系（`app/ai/skills/*`）

- 每个技能对外提供两套调用方式：
  1. **面向 Agent / Tool Calling**：`result: SkillResult = await get_skill("matching_score").run(**args)`（自动执行 JSON Schema 参数校验、`PermissionEnum` 权限拦截、`asyncio.wait_for` 超时控制，返回不抛裸异常的 `SkillResult(ok, data, error, error_code, elapsed_ms)`）。
  2. **面向工作流节点内部编排**：直接调用模块导出的异步函数（如 `compute_match_score`、`extract_skills`、`search_knowledge`、`query_database`）。

### 3.4 `BaseAgent` 与 `dispatch` 调度器（`app/ai/agents/base_agent.py`、`app/ai/agent.py`）

- 6 个业务智能体统一继承 `BaseAgent` 并使用 `@register_agent("<type>")` 装饰器注册，只需实现三个钩子方法：
  1. `async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]`
  2. `async def parse_output(self, raw: str) -> dict[str, Any]`
  3. `async def persist(self, result: dict[str, Any], payload: dict[str, Any]) -> Any`（可选覆盖）
- 基类 `run()` 已完整封装：注入 `system_prompt`（优先从 `prompt_registry` 按 `prompt_name` 读取）→ 调用 `llm_manager.chat` → 调用 `parse_output` → 模型/JSON 异常按 `LLM_MAX_RETRIES` 自动重试、业务异常立即终止 → 调用 `persist` → 返回包含 `ok / result / persisted / tokens / elapsed_ms / attempts` 的标准字典。

---

## 4. 六大核心业务与 AI 链路端到端时序设计

### 4.1 链路一：简历上传 → AI 解析 → 技能打标 → 向量查重 → 建档画像

```text
[前端] POST /api/v1/resumes/upload
  └─► [业务组] resume_router.upload()
        └─► [业务组] ResumeService.upload_resume(file, current_user)
              ├─ 1. 调 [架构组] file_utils 校验扩展名/大小 + 计算 sha256
              ├─ 2. 查 [业务组] ResumeRepository.get_by_hash() → 命中则秒传返回 duplicated=True
              ├─ 3. 调 [业务组] FileService.save_upload() (底层调 [架构组] get_storage().save)
              └─ 4. 写 resumes 表 (status="uploaded") → 返回 resume_id

[前端] POST /api/v1/resumes/{id}/parse
  └─► [业务组] ResumeService.trigger_parse(resume_id, force)
        ├─ 置 status="parsing"，后台/异步启动工作流（注入 AIContext）
        └─► [Agent组] resume_workflow.run(resume_id, file_path)
              ├─ Node 1 [extract]: 调 [RAG组] document_loader.load(file_path) 提取多页纯文本
              ├─ Node 2 [clean]:   清洗页眉页脚、乱码与多余空白
              ├─ Node 3 [structure]: 调 [Agent组] ResumeAgent.run() (底层走 [架构组] llm_manager + parse_json)
              ├─ Node 4 [validate]: Pydantic 校验 parsed_data；失败且 retries<=2 则跳回 Node 3 重试
              │                     成功后叠加 [架构组] resume_skill.extract_skills() 补全标准技能标签
              └─ Node 5 [persist]:  调 [业务组] ResumeService.save_parse_result() 幂等回写 parsed_data + resume_experiences (status="success")

[前端] POST /api/v1/resumes/{id}/bind (绑定或新建人才)
  └─► [业务组] ResumeService.bind_talent()
        ├─ 可选先调 ResumeService.dedupe_resume() (调 [RAG组] EmbeddingService + MilvusAdapter 查 talent_vectors)
        ├─ 调 [业务组] TalentService.create_from_resume() 写入 talents 主档 + 同步 upsert talent_vectors
        └─ 异步触发 [架构组] dispatch("talent", {"talent_id": id}) → [Agent组] TalentAgent 生成画像写回 talent_portraits
```

### 4.2 链路二：人岗精准匹配与可解释评估链路

```text
[前端] POST /api/v1/matchings/run  {position_id, strategy="hybrid", top_k=20, min_score=60}
  └─► [业务组] matching_router.run_matching()
        └─► [业务组] MatchingService.run_matching(...)
              ├─ 1. 调 [业务组] PositionRepository.get_by_id_or_404() 校验 status == "open"
              ├─ 2. 候选池初筛：按数据权限 + 硬性条件调 [业务组] TalentRepository 获取在职/储备候选人
              ├─ 3. 语义向量召回（若 MILVUS_ENABLED）：调 [RAG组] MilvusAdapter.search("talent_vectors", pos_vec, top_k=top_k*3)
              ├─ 4. 策略加权打分（核心复用！）：调 [架构组] MatchStrategyFactory.create(strategy).match(position, pool)
              │      └─ 内部自动复用 matching_skill.compute_match_score() 计算 skill(0.4)/exp(0.3)/edu(0.2)/quality(0.1)
              ├─ 5. 生成规则化默认 suggestion，按 batch_id 批量写入 [业务组] MatchingRepository.save_batch()
              └─ 6. 返回批次匹配排序列表

[前端] POST /api/v1/matchings/explain  {position_id, talent_id}
  └─► [业务组] MatchingService.explain_match(position_id, talent_id)
        ├─ 先计算确定性四维分数与命中/缺失技能证据（compute_match_score）
        ├─ 调 [架构组] dispatch("matching", payload, db=self.db) → [Agent组] MatchingAgent 生成可解释评语
        └─ 若 LLM 超时/不可用，自动捕获 AIError 降级返回基于 highlights/gaps 拼接的规则化解释（保障高可用）
```

### 4.3 链路三：企业制度知识库索引与无幻觉引用问答链路

```text
[索引链路] POST /api/v1/knowledge/docs/upload
  └─► [RAG组] KnowledgeService.upload_document()
        ├─ 调 [业务组] FileService / [架构组] get_storage() 保存原文件，写 knowledge_docs (status="indexing")
        └─► [RAG组] RAGPipeline.index_document(doc_id)
              ├─ 1. document_loader.load() → document_splitter.split_document() 产出带 page/heading/chunk_index 的切片
              ├─ 2. EmbeddingService.embed_texts(batch=16) (底层调 [架构组] llm_manager.embed)
              ├─ 3. KnowledgeRepository.save_chunks() 写入 MySQL 拿到自增 chunk_id
              ├─ 4. MilvusAdapter.delete(expr=f"doc_id == {doc_id}") + MilvusAdapter.upsert("knowledge_vectors", ...) (带 allowed_roles/organization_id)
              └─ 5. 更新 knowledge_docs (status="indexed", chunk_count, indexed_at)；异常则写 index_error 且 status="failed"

[问答链路] POST /api/v1/knowledge/ask
  └─► [RAG组] KnowledgeService.ask() → RAGPipeline.ask(question, top_k=5)
        ├─ 1. 从 get_context() 提取用户 roles 与 organization_id，构造前置权限过滤 scope
        ├─ 2. Retriever.hybrid_search(question, top_k=5, scope=scope):
        │      ├─ semantic_search: EmbeddingService.embed_query → MilvusAdapter.search(expr=权限表达式)
        │      ├─ keyword_search:  KnowledgeRepository.search_chunks_by_keyword(scope=scope)
        │      └─ RRF 融合 score = Σ 1/(60 + rank) + 可选 rerank
        ├─ 3. 【零幻觉铁律】若召回列表为空 → 直接返回 {"answer": "知识库中未找到依据", "citations": []}（不调 LLM！）
        └─ 4. 拼接带编号 [1][2] 的严谨 Prompt → llm_manager.chat → 提取引用来源返回 answer + citations
```

### 4.4 链路四：Vanna NL2SQL 自然语言安全问数链路

```text
[前端 / sql_query 技能] POST /api/v1/analytics/nl2sql  {question, max_rows=200}
  └─► [Vanna组] AnalyticsService.nl2sql_ask(question, current_user)
        ├─ 1. 根据用户 DataScope 与部门树计算行级过滤条件 scope_sql（如 "organization_id IN (3, 4)"，超管/HR 为 None）
        └─► [Vanna组] VannaAdapter.ask(question, user_scope=scope_dict, max_rows=200)
              ├─ Step 1: 检索热点缓存；未命中则由 Vanna（或降级 LLM + DDL Few-shot）生成候选 SQL
              ├─ Step 2: sql_validator.validate_select_sql(sql, ALLOWED_TABLES) -> sqlglot AST 校验并重新生成干净 SQL
              ├─ Step 3: sql_validator.inject_data_scope(sql, scope_sql) -> AST 注入 WHERE 权限条件
              ├─ Step 4: sql_validator.inject_limit(sql, max_rows) -> 强制外层或子句收敛行数
              ├─ Step 5: async with pg_session() as pg: 设置 local statement_timeout 后只读执行，获取 columns + rows
              ├─ Step 6: _infer_chart(columns, rows) 推断 card/line/pie/bar 图表配置
              ├─ Step 7: 调用 llm_manager.chat 生成简洁数据洞察与风险提示 insight
              └─ Step 8: 异步发审计事件，返回 NL2SQLResponse
```

### 4.5 链路五：智能体多轮流式对话与 Dify 中台双模路由链路

```text
[前端] POST /api/v1/agents/chat/stream
  └─► [Agent组] agent_router.chat_stream()
        └─► [Agent组] AgentService.chat_stream(request, current_user)
              ├─ 1. set_context(current_user, conversation_id=...) + TokenManager().check_quota(user.id)
              ├─ 2. 校验/创建 Conversation（ConversationRepository），通过 [架构组] ConversationMemory 追加用户消息
              ├─ 3. 【双模路由分支】（Agent 组与 Dify 组唯一交界点）：
              │      ├─ 分支 A（settings.DIFY_APP_ID_CHAT 非空且 Dify 可用）：
              │      │     调用 [Dify组] DifyAdapter().chat_stream(...) + iter_sse() 转换为平台标准事件；
              │      │     若抛 ExternalServiceError 且开启本地回退，自动切入分支 B
              │      └─ 分支 B（默认本地引擎）：
              │            调用 [架构组] dispatch_stream("assistant", payload, db=self.db, user=current_user)
              │            └─ [Agent组] AssistantAgent 结合历史记忆与 8 大 Skills 输出 citation + delta 流
              └─ 4. 流结束时将完整回答写入 ConversationMemory（含 DB 落库）并发送 event: done
```

---

## 5. 跨组核心集成契约与桥接协议设计

### 5.1 Skill ↔ Service 双模式会话桥接协议（关键防踩坑设计）

`[架构组]` 已落地的 Skills（如 `MatchingScoreSkill`、`CandidateListSkill`、`ResumeParseSkill`、`ResumeDedupeSkill`、`PositionSearchSkill`）在被 LLM Function Calling 动态调用时，采用**无参构造**（如 `MatchingService()`、`ResumeService()`、`PositionRepository()`）触发方法。

因此，`[业务组]` 在实现被 Skill 依赖的 Service 与 Repository 时，**必须兼容请求级注入（传入 `db`）与 Skill 独立调用（`db=None` 时内部开启 `session_scope()`）两种形态**：

```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.database.session import session_scope

class MatchingService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self.repo = MatchingRepository()

    @asynccontextmanager
    async def _db_ctx(self):
        """Router 调用时复用请求级 self.db；Skill 无参调用时自动借用 session_scope()。"""
        if self.db is not None:
            yield self.db
        else:
            async with session_scope() as session:
                yield session
```

### 5.2 全平台统一 SSE 流式事件协议

`POST /api/v1/agents/chat/stream`（`[Agent组]` / `[Dify组]`）与流式知识问答（`[RAG组]`）严格输出以下事件格式，禁止自定义异构字段：

| `event` 名称 | `data` JSON 结构 | 触发时机 |
| --- | --- | --- |
| `start` | `{"conversation_id": "c-...", "message_id": 88, "trace_id": "..."}` | 流建立首帧 |
| `message`（或 `delta`） | `{"delta": "文本增量片段"}` | 大模型每产出一个 token/词块 |
| `citation` | `{"doc_id": 3, "doc_title": "职级晋升管理办法", "page": 2, "score": 0.88}` | 命中 RAG 知识片段或业务实体溯源时 |
| `done` | `{"conversation_id": "c-...", "usage": {"prompt_tokens": 510, "completion_tokens": 180}, "elapsed_ms": 1620}` | 正常生成完毕末帧 |
| `error` | `{"code": 5002, "message": "模型调用超时，请稍后重试"}` | 发生业务/外部异常时输出并优雅关闭流 |

---

## 6. 纵深安全防线、故障降级与可观测性设计

### 6.1 外部依赖故障降级矩阵（保障无外部环境可启动、可单测）

| 故障组件 | 启动阶段表现（`lifespan`） | 运行期业务降级行为 | 接口错误码约定 |
| --- | --- | --- | --- |
| **MySQL 不可用** | 开发模式打印修复建议不崩溃；`/health/ready` 返回 HTTP 503 | 核心业务不可用，提示检查 `DB_*` | `5000` / `503` |
| **PgSQL 不可用 / 关闭** | `/health/ready` 返回 HTTP 200（`status=degraded`） | `AnalyticsService` 看板统计自动降级查 MySQL 聚合；NL2SQL 返回外部服务未启用提示 | 看板正常 `200`；NL2SQL 返回 `5001` |
| **Milvus 不可用 / 关闭** | `/system/info` 标记 `milvus.enabled=false` | 人才/岗位/文档 CRUD 主流程正常提交（跳过向量写入并记 warning）；语义检索与 RAG 返回明确提示 | CRUD 正常 `200`；向量检索返回 `5001` |
| **Redis 不可用 / 关闭** | 自动回退进程内内存结构 | `TokenManager`、`ConversationMemory`、`StateManager`、限流器全部无感退化至进程内字典 | 全部业务正常 `200` |
| **LLM API 超时 / 欠费** | 不影响服务启动 | `llm_manager` 按降级链切换备用模型；仍失败时匹配解释/技能抽取退化至规则/词典版，纯生成接口报 `AIError` | 可退化场景 `200`；纯生成返回 `5002` |
| **Dify 不可用 / 未配置** | 默认走本地自研引擎 | `AgentService` 自动切回本地 `BaseAgent` + LangGraph 执行 | 本地兜底正常 `200`（直调 Dify 失败返 `5001`） |

### 6.2 全链路可观测性设计

1. **Trace 贯穿**：HTTP 入口 `TraceIdMiddleware` 生成或透传 16 位 `trace_id`，自动写入日志上下文、`AIContext.trace_id`、Dify/LLM 请求头、`audit_logs.trace_id` 与统一响应体。
2. **AI 成本账本**：所有经 `llm_manager` 的调用自动向 `TokenManager` 登记 `user_id / model / prompt_tokens / completion_tokens / purpose`，并可通过 `EventBus` 异步写入 `audit_logs`（`action="ai_call"`）。
