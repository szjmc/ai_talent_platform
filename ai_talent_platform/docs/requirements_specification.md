# AI 数字化人才平台 · 需求规格说明书（SRS）

> **文档版本**：v3.0.0（5 组垂直闭环版） ｜ **更新日期**：2026-11-04
> **需求依据**：《2026 顶配版 AI 数字化人才平台详细技术与功能需求》+ 仓库既有接口契约（`docs/api_document.md`）+ 代码实现基线（`docs/已实现功能清单.md`）
> **配套文档**：[系统总体与详细设计文档](system_design_document.md) ｜ [五大研发组职责边界与防重复开发总规范](group_boundaries_and_tasks.md) ｜ [API 接口文档](api_document.md) ｜ [数据库设计](database_design.md)

---

## 目录

1. [项目定位与总体建设目标](#1-项目定位与总体建设目标)
2. [强制技术栈与架构约束](#2-强制技术栈与架构约束)
3. [用户角色、RBAC 权限与数据安全隔离需求](#3-用户角色rbac-权限与数据安全隔离需求)
4. [五大核心 AI 能力详细需求与量化指标](#4-五大核心-ai-能力详细需求与量化指标)
5. [全业务模块功能需求规格（15 大模块）](#5-全业务模块功能需求规格15-大模块)
6. [四库混合存储与数据治理需求](#6-四库混合存储与数据治理需求)
7. [非功能性需求（性能、高可用、安全合规、国产化）](#7-非功能性需求性能高可用安全合规国产化)
8. [需求追踪与五大研发组唯一归属矩阵（RTM）](#8-需求追踪与五大研发组唯一归属矩阵rtm)

---

## 1. 项目定位与总体建设目标

### 1.1 项目背景与定位

本项目建设**企业级 AI 原生数字化人才智能平台**，彻底摒弃传统 HR 管理系统“人工填表 + 静态报表 + 关键词搜索”的表单式架构，以**大模型多智能体（AI Agent）为核心、私有化 RAG 知识库为数据底座、有状态 AI 工作流编排为业务驱动、自然语言智能问数（NL2SQL）为决策输出**，支撑政企及大型组织在“选、用、育、评、留、策”全生命周期的人才数字化、智能化、无人化运转。

### 1.2 核心建设目标

1. **档案自动化**：多格式简历（PDF / Word / Excel / TXT / 图片扫描件）自动解析、清洗、提取百级技能标签、向量查重合并、动态生成多维人才数字画像。
2. **测评与培养闭环化**：围绕岗位能力模型实现“千人千卷自适应测评 → 规则与 AI 双重评分 → 短板诊断 → 个性化培训计划推送 → 学习进度防刷监控”自动化闭环。
3. **人岗匹配精准化与可解释化**：融合硬性门槛过滤、Milvus 高维语义向量召回与四维加权打分，输出 0–100 精准匹配分及基于真实履历证据的 AI 可解释评估报告。
4. **知识问答零幻觉与严隔离**：汇聚企业人才制度、岗位说明书、培养方案等私有资料，构建分级权限隔离的 RAG 知识库，回答必附原文出处，无依据时明确拒答、绝不编造。
5. **数据决策口语化**：管理层通过口语化自然语言自由查询人才结构、流失趋势、测评分布、培训完成率等多维数据，自动完成安全校验、图表推断、风险解读与 Word/PDF 汇报导出。
6. **能力组件化与低代码编排**：沉淀标准化可插拔 Skills 技能组件库，并与 Dify AI 应用中台协同，支持可视化 Prompt 版本管理与低代码应用发布。

---

## 2. 强制技术栈与架构约束

平台严格采用指定技术栈落地，**严禁替换、降级或删减核心技术组件**（尤其严禁使用 SQLite 替代业务库或测试库）：

| 技术领域 | 强制组件 | 平台落地规范与约束 | 当前状态 |
| --- | --- | --- | --- |
| 后端服务框架 | **FastAPI（Python 全异步）** | 全链路 `async def` 异步非阻塞、统一 `/api/v1` 前缀、四段式响应、SSE 流式推送、自动生成 `/docs` 与 `/redoc` | ✅ 已落地 |
| 数据访问层 | **SQLAlchemy 2.0 Async ORM** | 全局对象关系映射，**业务代码严禁裸 SQL**（仅 Vanna 只读分析在 AST 安全校验后执行规范化 SELECT） | ✅ 已落地 |
| 业务主数据库 | **MySQL 8.0+（`mysql+aiomysql`）** | 25 张核心交易表（用户/权限/组织/岗位/人才/简历/测评/培训/匹配/知识库/会话/文件/审计），请求级事务 + 乐观锁 + 软删除 | ✅ ORM 已落地 |
| 海量分析与时序库 | **PostgreSQL 15+（`postgresql+asyncpg`）** | 6 张分析宽表与 RANGE 时间分区事实表，读写分离，专供复杂统计与 Vanna NL2SQL 只读查询 | ✅ 表结构已落地（ETL 待开发） |
| 向量检索引擎 | **Milvus 2.4+（`pymilvus`）** | 4 大专属向量集合（`talent_vectors` / `position_vectors` / `knowledge_vectors` / `question_vectors`），COSINE 度量 | 🔧 初始化脚本已就绪，读写待开发 |
| 缓存与实时中间件 | **Redis 7+（`redis.asyncio`）** | 热点缓存、Agent 短期会话记忆、LangGraph 状态持久化、Token 日配额计数、限流计数；未启用时优雅降级 | ✅ 客户端已落地 |
| 大模型编排与 RAG | **LangChain + 自研异步适配器** | 文档多格式加载、中文语义/章节切分、Embedding 向量化、语义+关键词混合检索（RRF）与重排 | ✅ LLM 底座已落地，RAG 待开发 |
| 多智能体工作流 | **LangGraph** | 有状态图引擎、条件分支、校验失败自动重试、断点续跑（Checkpointer）、人工介入节点（`need_human`） | 🔧 状态管理已就绪，图与 Agent 待开发 |
| 自然语言数据分析 | **Vanna.ai + `sqlglot`** | DDL/术语/Few-shot 训练、AST 语法白名单安全校验、行级权限自动注入、图表自动推断、AI 解读 | 🔧 语料脚本与 Skill 已就绪，核心待开发 |
| 低代码 AI 中台 | **Dify** | 模型网关、Prompt 工程管理、低代码工作流/对话应用 API 封装、SSE 流式协议转换与本地双模切换 | 🔧 待开发 |
| 可编排技能库 | **自研 `BaseSkill` 体系** | OpenAI Tool Calling 标准 JSON Schema、自动参数校验、上下文权限强制拦截、8 大核心业务/数据技能 | ✅ 已落地 |

---

## 3. 用户角色、RBAC 权限与数据安全隔离需求

### 3.1 五大内置角色与默认数据范围

| 角色编码 | 角色名称 | 业务职责画像 | 默认行级数据范围（`DataScope`） |
| --- | --- | --- | --- |
| `super_admin` | 超级管理员 | 平台全局管控、系统配置、全量审计与运维 | `all`（全部数据，拥有 `*` 通配权限） |
| `admin` | 系统管理员 | 用户账号管理、角色授权、组织架构维护、日志审计 | `all`（全部数据） |
| `hr` | 人力资源专员/BP | 人才建档、简历导入解析、岗位发布、人岗匹配、测评与培训组织、制度库维护 | `all`（全公司业务数据） |
| `manager` | 部门管理者 | 查看本部门及下属部门人才画像、发起匹配、查看团队数据看板与自然语言问数 | `dept_sub`（本部门及所有下级子部门） |
| `employee` | 普通员工 | 自助查询公开/本角色人才制度（RAG 问答）、参与个人测评、执行个人培训学习计划 | `self`（仅本人关联数据与公开制度） |

### 3.2 28 项操作权限码体系

系统内置以下细粒度权限码（与 `app/core/permissions.py::PermissionEnum` 逐项一致），所有业务与 AI 接口必须通过 `Depends(require_permission(...))` 强制拦截：

- **用户与组织**（6）：`user:read`、`user:create`、`user:update`、`user:delete`、`organization:read`、`organization:manage`
- **人才与简历**（6）：`talent:read`、`talent:create`、`talent:update`、`talent:delete`、`resume:upload`、`resume:parse`
- **岗位与匹配**（4）：`position:read`、`position:manage`、`matching:read`、`matching:run`
- **测评与培训**（4）：`assessment:read`、`assessment:manage`、`training:read`、`training:manage`
- **AI 与数据**（5）：`knowledge:read`、`knowledge:manage`、`analytics:read`、`analytics:export`、`agent:use`
- **文件与审计**（3，`[架构组]` 横向服务）：`file:upload`、`file:delete`、`audit:read`

### 3.3 AI 场景下的安全红线需求（强制）

1. **上下文唯一身份源**：Agent、Skill、RAG 检索、Vanna 问数的用户身份、角色、部门 ID 必须且只能从服务端 `AIContext`（基于 JWT 解析注入）读取，**严禁信任大模型在 Tool Calling 参数或 Prompt 中传入的 `user_id` / `role` / `organization_id`**。
2. **检索前权限过滤（Pre-filtering）**：RAG 知识库检索必须在 Milvus 查询表达式（`expr`）和 MySQL 查询条件中前置过滤 `permission_scope`（`public` / `dept` / `role`）与 `organization_id`，严禁“先全量召回再应用层丢弃”导致侧信道泄密。
3. **问数行级与表级隔离**：NL2SQL 查询仅允许访问 PgSQL 分析库 6 张白名单表，非全局角色自动在 SQL AST 的 `WHERE` 子句追加 `organization_id IN (...)` 条件。
4. **敏感字段脱敏**：非管理员查询人才详情/列表时，手机号自动脱敏为 `138****0001`；任何接口与日志中绝不输出 `password_hash`、明文密钥或测评标准答案（答题阶段）。

---

## 4. 五大核心 AI 能力详细需求与量化指标

### 4.1 RAG 检索增强生成系统（归口：`[RAG组]`）

| 需求项 | 详细功能与技术指标要求 |
| --- | --- |
| 多源异构文档加载 | 支持 `.pdf`、`.docx`/`.doc`、`.xlsx`/`.xls`、`.txt`、`.md` 及扫描件/图片（OCR 扩展）加载；保留文件名、页码（`page`）、文档类型等元数据；不支持格式明确抛 `FileError(5003)`。 |
| 中文智能切片 | 支持按固定窗口+重叠（默认 `chunk_size=512, overlap=64`，优先在中文句末标点/换行处切分，不截断完整短句）及按制度章节标题（`第X章/第X条/#`）层级切分，记录 `chunk_index` 与 `heading`。 |
| 向量化与索引状态机 | 文档上传后状态流转为 `indexing → indexed / failed`；批量调用 Embedding（`batch=16`）写入 Milvus `knowledge_vectors` 并同步落库 `knowledge_chunks`；支持按 `file_hash` 去重与失败重索引。 |
| 混合检索与重排 | 同时支持语义向量检索（`semantic_search`）、关键词检索（`keyword_search`）及基于 RRF（Reciprocal Rank Fusion）算法的混合检索（`hybrid_search`），支持可选 Rerank 重排；综合检索准确率目标 ≥ 95%，10 万级向量 P95 检索延迟 ≤ 500ms。 |
| 无幻觉引用式问答 | 回答必须严格基于召回片段并在文末/事件流中返回结构化引用 `citations[{doc_id, doc_title, page, chunk, score}]`；**当召回结果为空或全部低于阈值时，固定返回“知识库中未找到依据”，不调用大模型自由发挥，彻底杜绝幻觉**。 |

### 4.2 LangGraph 多智能体协同系统（归口：`[Agent组]`，基类与调度器由 `[架构组]` 提供）

平台基于统一的 `BaseAgent` 与 LangGraph `StateGraph` 落地 **6 个专属智能体 + 3 条核心有状态工作流**（完整覆盖原需求文档五大专属智能体与全局对话助手）：

| 智能体 / 工作流标识 | 对应原需求文档角色 | 核心职责与自动化闭环要求 |
| --- | --- | --- |
| `resume_agent` + `resume_workflow` | 1. 人才档案解析 Agent | 驱动 `extract → clean → structure → validate → persist` 有状态图：提取文本、清洗噪声、LLM 结构化抽取基本信息/教育/履历/项目/技能、JSON 校验失败自动循环重试（≤2 次）、调用技能打标与查重、幂等回写业务库。 |
| `talent_agent` | 1. 人才档案解析 Agent（画像子能力） | 综合人才履历、项目经验、测评成绩与培训记录，生成四维能力评分（`skill/experience/quality/potential`）、A/B/C/D 潜力评级、百级 AI 标签、核心优势、短板与推荐发展岗位，版本化写入 `talent_portraits`。 |
| `assessment_agent` | 2. 人才测评分析 Agent | 支持按岗位能力要求生成差异化题目、对主观简答题（`text`）进行语义评分、结合客观题得分输出维度雷达数据、短板诊断与能力提升报告。 |
| `matching_agent` + `matching_workflow` | 3. 岗位人才匹配 Agent | 驱动 `parse_position → hard_filter → vector_recall → score → explain → persist` 工作流：在复用统一匹配策略完成硬过滤与打分后，结合真实字段证据生成匹配亮点、差距分析与面试考察建议；LLM 异常时自动降级为规则化解释。 |
| `training_agent` | 4. 智能培训赋能 Agent | 根据人才能力短板与目标岗位要求诊断能力缺口（`gap_dimensions`），从课程库智能匹配推荐定制化学习计划并阐述推荐理由，形成“测评—诊断—培训—提升”闭环。 |
| `assistant_agent` + `analysis_workflow` | 5. 人才数据决策 Agent + 全局智能助手 | `assistant_agent` 作为统一多轮对话入口，支持 Function Calling 编排调用 8 大 Skills（查制度、查数据、搜岗位、算匹配、查候选人等）；`analysis_workflow` 串联多维统计指标，完成团队人才结构诊断、流失风险预警与管理层决策建议生成。 |

**通用强制要求**：支持会话短期/长期记忆（`ConversationMemory`）、工作流断点续跑状态管理（`StateManager`）、最大步数防死循环保护（`AGENT_MAX_STEPS=8`）、人工审批中断态（`need_human`）、Token 消耗记录与 SSE 逐 token 流式推送。

### 4.3 Skills 可编排技能组件库（归口：`[架构组]` 已完成底座与 8 大技能封装）

- 所有技能继承 `BaseSkill`，统一声明 `name`、`description`、JSON Schema `parameters`、`required_permission`、`timeout_seconds`，一键导出 OpenAI Function Calling 工具规范（`to_openai_tool()`）及系统清单接口（`GET /api/v1/system/skills`）。
- 已内置 **8 个标准技能组件**：
  1. `knowledge_search`（企业制度与知识库检索）
  2. `matching_score`（人岗匹配四维打分，支持传入 ID 查库或纯函数离线计算）
  3. `position_search`（在招岗位关键词与状态检索）
  4. `candidate_list`（指定岗位历史匹配候选人排序查询）
  5. `resume_parse`（简历结构化解析触发与整形）
  6. `resume_skill_extract`（离线词典正则 + 可选 LLM 混合技能标签抽取）
  7. `resume_dedupe`（基于阈值的简历与人才库向量查重）
  8. `sql_query`（输入防护 + Vanna NL2SQL 只读数据查询）

### 4.4 Vanna.ai 自然语言数据分析体系（归口：`[Vanna组]`）

1. **四类训练语料完备**：基于 PgSQL 分析库 6 张表 DDL、HR 指标统计口径说明、业务术语词典（骨干、流失率、匹配率、继任梯队等）、不少于 40 条高质量 Few-shot 示例 SQL 进行训练，支持 `--reset` 幂等重建。
2. **五道 SQL 安全防线**：通过 `sqlglot` AST 解析强制校验：① 仅允许单条 `SELECT`/`WITH`；② 严禁一切 DDL/DML/DCL；③ 表名严格限制在 6 张分析库白名单内，禁止 `information_schema`/`pg_catalog`；④ 强制注入当前用户部门数据范围 `WHERE` 条件；⑤ 强制包裹 `LIMIT` 上限（默认 200，最高 1000）并通过 PgSQL 只读连接 + `statement_timeout` 执行。
3. **自动出图与智能解读**：根据结果集行列特征自动推断可视化图表配置（数字卡片 `None` / 折线图 `line` / 饼图 `pie` / 柱状图 `bar`），调用大模型生成异常解读与管理建议（`insight`），并支持异步生成导出 PDF / Word 分析报告。

### 4.5 Dify AI 应用中台能力（归口：`[Dify组]`）

1. **统一异步适配器封装**：封装 Dify `/chat-messages`（阻塞与流式）、`/workflows/run`、`/datasets/*` 文档同步接口，统一错误码映射（`ExternalServiceError(5001)`）、超时重试与 `trace_id` 透传。
2. **SSE 协议对齐与无感双模切换**：将 Dify 原生流式事件解析转换为平台标准 SSE 事件（`start / message(delta) / citation / done / error`）；当配置 `DIFY_APP_ID_CHAT` 或 `DIFY_APP_ID_RESUME` 时，平台可通过配置在“本地自研 Agent/Workflow”与“Dify 低代码编排应用”间平滑切换，Dify 不可用时自动降级回退本地引擎。

---

## 5. 全业务模块功能需求规格（15 大模块）

> 接口级字段定义、请求/响应 JSON 示例以 [docs/api_document.md](api_document.md) 为唯一契约准绳；以下定义各模块核心业务需求与验收规则。

### 5.1 认证模块 Auth（`/api/v1/auth`）— ✅ 已落地

- 支持用户注册、账号密码登录（签发 JWT `access_token` + `refresh_token`，支持 `remember_me` 延长有效期）、刷新令牌、获取当前登录用户信息及角色权限列表（`/me`）、修改本人密码、退出登录。
- **安全规则**：密码采用 bcrypt 加盐哈希存储；连续登录失败 5 次自动锁定账号 15 分钟（错误码 `2006`）；用户名不存在与密码错误统一返回 `2003` 防止账号枚举。

### 5.2 用户管理模块 Users（`/api/v1/users`）— ✅ 已落地

- 支持管理员分页查询用户（按部门、启用状态、关键词筛选）、查看详情、创建用户（校验用户名与手机号唯一）、修改资料、启用/停用账号（禁止停用自身或超级管理员）、分配角色集合、管理员重置密码、软删除用户。

### 5.3 组织架构模块 Organizations（`/api/v1/organizations`）— ✅ 已落地

- 支持无限层级部门树查询（`/tree`，附带部门在职人数统计）、分页列表、详情、创建部门（编码全局唯一、同级名称唯一，自动维护 `level` 与 `path` 物化路径）、更新部门、移动部门（防环检测：禁止移入自身或子孙节点，级联刷新全部子孙 `path` 与 `level`）、软删除部门（存在子部门或在职员工时禁止删除）。

### 5.4 岗位管理模块 Positions（`/api/v1/positions`）— 🔧 待开发（`[Agent组]` 垂直负责，向量调 `[RAG组]`）

- **岗位全生命周期**：创建岗位（`code` 全局唯一，包含结构化 `requirements`：最低学历、最低年限、必备技能、证书、软技能及四维权重）、多条件分页查询、查看岗位详情（含编制 `headcount` 与已到位人数 `headcount_filled`）、更新岗位、状态流转（`open ⇄ paused`，`open/paused → closed` 单向关闭；关闭后不再参与匹配；存在运行中匹配任务时禁止删除）。
- **AI 赋能需求**：支持上传或提交岗位说明书全文（`POST /positions/{id}/parse`），异步调用 AI 拆解提取结构化 `requirements` 与 `skill_tags`，并在创建/更新/关闭岗位时调用 `[RAG组] MilvusAdapter` 同步维护 Milvus `position_vectors` 岗位向量（向量化异常仅记告警并提供补偿，不阻塞主事务提交）。

### 5.5 人才智能档案与数字画像模块 Talents（`/api/v1/talents`）— 🔧 待开发（`[RAG组]` 垂直负责，画像生成调 `[Agent组]`）

- **档案主数据管理**：新建人才档案（未删除记录内手机号唯一，成功后异步触发画像生成与 `talent_vectors` 向量入库）、多维组合筛选分页列表（关键词、学历、部门、在职状态、技能标签、工龄区间；非管理员响应手机号脱敏）、人才综合详情（聚合关联简历、最新测评、历史匹配摘要）、更新档案（写入变更前后审计）、软删除（同步清理 `talent_vectors`）。
- **数字画像管理**：查询最新人才画像（`GET /talents/{id}/portrait`）与手动触发重算（`POST /talents/{id}/portrait/refresh`，经 `dispatch("talent")` 调用 `[Agent组] talent_agent` 生成新版本画像并更新主档 `potential_level` 与 `portrait_summary`）。
- **自然语言语义检索**：`POST /talents/search/semantic` 支持输入口语化找人需求（如“查找擅长数字化转型、有 3 年以上 AI 项目经验的骨干”），由本组 `EmbeddingService + MilvusAdapter` 向量召回 `talent_vectors` + 标量条件过滤后回表返回高精度候选列表，响应耗时 ≤ 500ms。
- **重复档案合并治理**：`POST /talents/merge` 支持将重复源档案名下的简历、测评记录、学习记录、匹配历史原子迁移至目标主档，随后软删除源档案并记录审计日志。

### 5.6 简历上传与 AI 解析模块 Resumes（`/api/v1/resumes`）— 🔧 待开发（`[RAG组]` 垂直负责，工作流解析调 `[Agent组]`）

- **安全上传与秒传去重**：`POST /resumes/upload` 校验文件扩展名白名单与大小上限（≤ 20MB），计算 `SHA-256` 哈希；若库中已存在相同 `file_hash` 的有效简历则直接秒传复用并标记 `duplicated=true`，否则调用 `[架构组] FileService` 落盘并创建 `status=uploaded` 记录。
- **异步 AI 解析与重试**：`POST /resumes/{id}/parse` 将状态置为 `parsing`（若已在解析中返回 `1002` 防重入），后台触发 `[Agent组] resume_workflow` 执行解析，完成后回调 `ResumeService.save_parse_result` 写入 `parsed_data`、工作/教育经历子表 `resume_experiences`、置信度 `accuracy` 及 `status=success`（失败则记录 `parse_error` 并置 `status=failed`，支持 `force=true` 强制重解析）。
- **结果查询与档案绑定**：支持分页列表、详情、解析结果及告警项查询（`/result`），以及将解析成功简历一键绑定至已有人才或通过本组 `TalentService` 自动新建人才主档（`POST /resumes/{id}/bind`）。

### 5.7 AI 智能测评模块 Assessments（`/api/v1/assessments`）— 🔧 待开发（`[Vanna组]` 垂直负责，主观题与报告调 `[Agent组]`）

- **千人千卷创建**：`POST /assessments` 根据目标岗位、能力维度与题目数量从题库智能抽题（相似题查重调 `[RAG组] MilvusAdapter` 操作 `question_vectors`，辅助出题调 `dispatch("assessment")`），设定截止时间 `deadline`，初始状态 `pending`。
- **防泄密作答监控**：`POST /assessments/{id}/start` 校验状态并流转为 `in_progress`，记录 `started_at` 并下发试题列表（**响应结构体严禁包含标准答案字段 `answer`**）。
- **混合判分与深度报告**：`POST /assessments/{id}/submit` 接收答卷，若超过 `deadline` 仍保存答卷但打上 `overtime` 异常标记；系统同步完成单选/多选客观题规则判分与维度统计，随后调用 `[Agent组] assessment_agent` 完成主观题语义评分、等级评定与优劣势研判报告生成，状态流转为 `scored`，并作为本组 `fact_assessment_history` 分析宽表权威数据源。

### 5.8 个性化智能培训模块 Trainings（`/api/v1/trainings`）— 🔧 待开发（`[Dify组]` 垂直负责，协同 `[Agent组]`）

- **课程与计划管理**：支持课程创建与多维检索（分类、难度层级、关键词）；支持人工或 AI 创建培训计划（`training_plans` + `plan_courses`），实时计算计划整体完成率 `completion_rate`。
- **学习进度防刷管控**：`POST /trainings/records` 上报学习进度与学时；**强制校验进度单调递增**（回退直接抛 `1002` 拒绝），并校验单次上报进度增幅与学习时长 `study_minutes` 比例合理性，异常刷课打上 `suspicious` 标记。
- **AI 推课与滞后督办工作流**：`POST /trainings/recommend` 结合人才当前画像/测评短板与目标岗位要求优先调用本组 `DifyAdapter.run_workflow`（未启用 Dify 时自动走 `dispatch("training")` 调用 `[Agent组] training_agent`）输出缺口维度与推荐课程列表；`GET /trainings/warnings` 自动识别时间过半但进度严重滞后的学员计划并支持触发 Dify 低代码督办通知工作流。

### 5.9 人岗精准匹配模块 Matchings（`/api/v1/matchings`）— 🔧 待开发（`[Agent组]` 垂直负责，复用 `[架构组]` 策略与 `[RAG组]` 向量）

- **多策略批量匹配**：`POST /matchings/run` 校验岗位处于 `open` 状态，复用 `[架构组] MatchStrategyFactory` 支持 `hard`（硬性门槛过滤）、`soft`（向量与四维软匹配）、`hybrid`（先硬过滤淘汰不达标者，再向量召回 + 技能 0.4 / 经验 0.3 / 学历 0.2 / 素质 0.1 加权打分，默认策略），生成唯一批次号 `batch_id` 并将候选人得分明细落库 `matching_records`。
- **多维追溯与可解释性**：支持按岗位查最新批次排序、按人才查历史匹配轨迹、按 `batch_id` 查批次快照，以及调用 `POST /matchings/explain` 由本组 `matching_agent` 输出引用真实字段证据的分维度匹配解释与面试考察建议。

### 5.10 企业级知识库模块 Knowledge（`/api/v1/knowledge`）— 🔧 待开发（`[RAG组]`）

- 提供新建纯文本知识、上传多格式制度文档（异步切片向量化）、分页查询文档索引状态、删除文档（事务级联动清理 `knowledge_chunks` 与 Milvus `knowledge_vectors`，索引进行中禁止删除）、纯向量/混合片段检索（`/retrieve`）、无幻觉引用式智能问答（`/ask`）及检索召回质量自检（`/retrieve-eval`）共 7 个标准接口。

### 5.11 自然语言数据决策模块 Analytics（`/api/v1/analytics`）— 🔧 待开发（`[Vanna组]`）

- 提供管理驾驶舱首页概览（`/overview`：人才总数、在招岗位数、匹配率、培训完成率、平均测评分与月度趋势）、多维分布统计（`/distribution`：学历/年龄/技能/部门）、时序趋势分析（`/trend`：新增/流失/匹配趋势）、自然语言自由问数（`POST /analytics/nl2sql`）、异步生成导出 PDF/Word 汇报报告（`POST /analytics/report`）及报告任务列表（`GET /analytics/reports`）共 6 个接口。

### 5.12 智能体对话与任务中心模块 Agents（`/api/v1/agents`）— 🔧 待开发（`[Agent组]` 主导，协同 `[Dify组]`）

- 提供非流式对话（`POST /agents/chat`）、SSE 实时流式对话（`POST /agents/chat/stream`）、当前用户历史会话列表（`GET /agents/conversations`，严格校验归属防越权）、会话历史消息分页（`GET /agents/conversations/{id}/messages`）、异步/同步触发指定业务 Agent 任务（`POST /agents/tasks`）及任务状态轮询（`GET /agents/tasks/{task_id}`）共 6 个接口；内置每用户每日 Token 配额检查（超限返回 `429/5002`）。

### 5.13 文件中心模块 Files（`/api/v1/files`）— 🔧 待开发（`[架构组]` 平台横向服务）

- 提供通用单文件上传、批量上传、元数据查询、鉴权文件流下载（校验 `biz_type` 与归属权限，严禁暴露服务器本地绝对路径）、软删除文件共 5 个接口；统一复用本组 `get_storage()`、UUID 安全重命名与 `SHA-256` 摘要登记。

### 5.14 操作与 AI 审计模块 Audit（`/api/v1/audit`）— 🔧 待开发（`[架构组]` 平台横向服务）

- **只增不改**：审计表不对外提供任何修改或删除接口；订阅本组 `EventBus` 异步事件并支持管理员按操作人、模块、动作类型（含 `ai_call`、`export`、`login`）、状态、`trace_id`、时间范围分页检索日志、查看变更前后 JSON Diff 详情及动作失败率统计；写审计异常必须内部捕获，绝不阻塞主业务链路。

### 5.15 系统探针与自检模块 System（`/health*`、`/api/v1/system/*`）— ✅ 已落地

- 提供 `/` 服务元信息、`/health` 存活探针、`/health/ready` 四库就绪诊断探针（含故障分类与中文修复建议）、`/api/v1/system/info`（ORM 表、AI 运行时就绪态）与 `/api/v1/system/skills`（已注册技能 Schema 清单）。

---

## 6. 四库混合存储与数据治理需求

| 存储引擎 | 定位与存储内容 | 强制边界与治理要求 |
| --- | --- | --- |
| **MySQL 8.0** | 25 张核心业务主数据表（高频 OLTP 事务读写） | 统一继承 `BaseModel`（`id` / `version` 乐观锁 / `created_at` / `updated_at` / `is_deleted` / `deleted_at` / `created_by` / `updated_by`）；禁止存大文件二进制；禁止跑全表扫描大报表。 |
| **PostgreSQL 15+** | 6 张分析与时序宽表（`dim_organization`、`dim_talent_wide` + 4 张按时间 RANGE 分区的 `fact_*` 事实表） | 需提供幂等增量同步 ETL 脚本（`scripts/sync_analytics.py`）将 MySQL 业务变更同步汇聚至 PgSQL 宽表；Vanna NL2SQL 查询只允许打向本库只读会话。 |
| **Milvus 2.4+** | 4 个高维语义向量集合（`talent_vectors`、`position_vectors`、`knowledge_vectors`、`question_vectors`） | 主键与 MySQL 业务实体 ID 一一对应，标量字段携带 `organization_id` / `status` / `allowed_roles` 供检索前过滤；业务主记录软删除时必须同步删除对应向量，防止孤儿向量召回。 |
| **Redis 7+** | 热点缓存、会话短期记忆（`chat:mem:*`）、工作流检查点（`wf:state:*`）、Token 配额（`ai:token:*`）、限流计数 | 所有 Key 必须设置合理 TTL；当 `REDIS_ENABLED=false` 或连接中断时，所有功能必须无感退化至进程内内存或数据库，严禁导致服务崩溃。 |

---

## 7. 非功能性需求（性能、高可用、安全合规、国产化）

1. **并发与响应时延指标**：
   - 支持 ≥ 2000 用户并发在线访问；单实例默认开启 IP 滑动窗口限流（300 次/分钟）。
   - 常规 CRUD 接口平均响应时长 ≤ 1s；Milvus 向量语义检索耗时 ≤ 500ms；复杂分析报表查询 ≤ 5s；SSE 流式问答首字节响应（TTFT）≤ 2s。
2. **高可用与优雅降级（强制验收项）**：
   - 全年系统可用率目标 ≥ 99.9%；外部组件（LLM、Milvus、PgSQL、Redis、Dify、Vanna）任一未部署或临时故障时，FastAPI 主进程必须正常启动，健康探针准确标记 `degraded`，受影响接口返回规范业务码 `5001`（外部服务异常）或 `5002`（AI 能力异常），**严禁抛出未捕获 500 堆栈崩溃**。
3. **安全与合规要求**：
   - 满足《网络安全法》《数据安全法》《个人信息保护法》要求；全站响应附带安全头（`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY` 等）；文件上传严防路径穿越（Path Traversal）；全链路日志携带 `X-Trace-Id` 且自动过滤/脱敏口令、令牌与个人隐私字段。
4. **国产化与私有化部署适配**：
   - 容器化编排支持一键私有化离线部署；大模型层通过统一 `BaseLLM` 适配通义千问（Qwen）、本地私有化 Ollama 及国产 OpenAI 兼容推理框架，确保敏感人才与制度数据 100% 不出私域。

---

## 8. 需求追踪与五大研发组唯一归属矩阵（RTM）

> **防重复立项原则**：全平台严格划分为 **`[架构组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]`** 五大研发组；每一项业务需求与技术能力在全平台**有且仅有一个主责研发组（Owner）**，其他组需要使用该能力时，必须通过主责组暴露的公开 Service / Adapter / Skill 契约调用，**严禁跨组重复编码**。

| 需求编号 | 需求模块 / 能力项 | 当前基线 | 唯一主责组（Owner） | 协同被调方（Dependencies） | 交付路由 / 核心产物 |
| --- | --- | --- | --- | --- | --- |
| REQ-01 | 公共框架、RBAC、统一响应/异常、四库连接池与探针 | ✅ 已完成 | `[架构组]` | — | `app/core/*`、`app/infrastructure/database/*` |
| REQ-02 | LLM 多模型统一适配、模型路由、Token 配额、重试熔断、JSON 解析 | ✅ 已完成 | `[架构组]` | — | `app/adapters/llm/*`、`app/ai/core/*`、`app/ai/utils/*` |
| REQ-03 | AI 上下文、8 大可编排 Skills 组件、Agent 基类与调度器、会话记忆/状态管理、文件存储、事件总线、匹配策略算法 | ✅ 已完成 | `[架构组]` | 运行时动态调用 RAG/Agent/Vanna | `app/ai/{context,agent,skills,memory}/*`、`app/patterns/strategy.py` |
| REQ-04 | 认证（Auth）、用户管理（Users）、角色（Roles）、组织架构树（Organizations） | ✅ 已完成 | `[架构组]` | — | `/api/v1/{auth,users,organizations}` |
| REQ-05 | 通用文件上传下载鉴权（Files）与全局操作/AI 审计查询（Audit） | 🔧 待开发 | `[架构组]` | 复用本组 `get_storage()` 与 `EventBus` | `/api/v1/{files,audit}`、`FileService`、`AuditService` |
| REQ-06 | 多格式文档加载器、中文/章节切片器、Embedding 服务、Milvus 四集合唯一读写适配器 | 🔧 待开发 | `[RAG组]` | 向量生成调 `[架构组] llm_manager.embed` | `app/ai/rag/{document_loader,document_splitter,embedding_service}.py`、`milvus_adapter.py` |
| REQ-07 | 混合检索器（语义+关键词+RRF+权限前置 expr）与企业知识库全链路接口 | 🔧 待开发 | `[RAG组]` | 生成调 `[架构组] llm_manager.chat/stream` | `/api/v1/knowledge`、`Retriever`、`RAGPipeline`、`KnowledgeService` |
| REQ-08 | 人才档案 CRUD、脱敏、语义搜人编排、重复档案合并、画像刷新触发 | 🔧 待开发 | `[RAG组]` | 本组 `MilvusAdapter` 查向量；画像生成调 `[Agent组] talent_agent` | `/api/v1/talents`、`TalentService` |
| REQ-09 | 简历安全上传、SHA256 秒传、`talent_vectors` 查重、解析编排与绑定人才 | 🔧 待开发 | `[RAG组]` | 存储调 `[架构组] FileService`；LLM 解析调 `[Agent组] resume_workflow` | `/api/v1/resumes`、`ResumeService` |
| REQ-10 | LangGraph 图构建器与 3 条有状态工作流（简历解析流/人岗匹配流/综合分析决策流） | 🔧 待开发 | `[Agent组]` | 状态持久化复用 `[架构组] state_manager`；节点调 Skills 与各组 Service | `app/ai/workflows/{graph_builder,resume_workflow,matching_workflow,analysis_workflow}.py` |
| REQ-11 | 6 大业务智能体实现、会话仓储落地、Agent 对话/SSE/任务中心接口 | 🔧 待开发 | `[Agent组]` | 继承 `[架构组] BaseAgent`；记忆复用 `ConversationMemory`；可选切 `[Dify组]` | `/api/v1/agents`、`app/ai/agents/*_agent.py`、`AgentService` |
| REQ-12 | 岗位管理 CRUD、状态流转、JD 解析触发与 `position_vectors` 同步 | 🔧 待开发 | `[Agent组]` | 文档加载与向量同步调 `[RAG组]` | `/api/v1/positions`、`PositionService` |
| REQ-13 | 人岗匹配跑批、批次追溯、候选人排序、匹配解释接口 | 🔧 待开发 | `[Agent组]` | 算法复用 `[架构组] MatchStrategyFactory`；候选人调 `[RAG组] TalentService` | `/api/v1/matchings`、`MatchingService` |
| REQ-14 | SQL 安全校验器、Vanna 训练与问数适配器、MySQL→PgSQL 宽表同步 ETL、数据决策看板与报告导出接口 | 🔧 待开发 | `[Vanna组]` | PgSQL 引擎与 6 表 ORM 复用 `[架构组]`；报告落盘调 `FileService` | `/api/v1/analytics`、`sql_validator.py`、`vanna_adapter.py`、`AnalyticsService`、`sync_analytics.py` |
| REQ-15 | 智能测评题库、防泄密开考、客观题规则判分、维度统计与 AI 研判编排 | 🔧 待开发 | `[Vanna组]` | 主观题与能力报告调 `[Agent组] assessment_agent`；题库向量调 `[RAG组]` | `/api/v1/assessments`、`AssessmentService` |
| REQ-16 | Dify API 异步适配器、Dify SSE 协议转换器、健康检查、低代码工作流/知识库同步脚本与 `AgentService` 双模接入分支 | 🔧 待开发 | `[Dify组]` | 重试熔断复用 `[架构组] ai_retry`；路由入口协同 `[Agent组] AgentService` | `app/adapters/dify/*`、`scripts/sync_dify_workflows.py` |
| REQ-17 | 培训课程/计划 CRUD、防刷进度上报、滞后预警与 Dify/本地双模 AI 推课督办 | 🔧 待开发 | `[Dify组]` | 本地推课回退调 `[Agent组] training_agent` | `/api/v1/trainings`、`TrainingService` |
