# CODEX_HANDOVER —— 岗位人才匹配 Agent 开发交接

> **交接对象**：OpenAI Codex
> **交接时间**：2026-11-04 18:50（GMT+8）
> **代码位置**：`D:\0904开班\agent-system-v1\ai-digital-talent-platform`
> **Git 分支**：`lz_agent`，HEAD `bed2b54`（本地领先远程 1 个提交，**未推送**）
> **本文定位**：一页看懂全局，细节分派到 4 份子文档。**请按第 9 节的顺序读**，不要跳读。

---

## 0. 十秒速览

| 问题 | 答案 |
|---|---|
| 这是什么项目 | 企业级「AI 数字化人才平台」后端（FastAPI + 多 Agent），本次交接其中**岗位人才匹配 Agent** 一个模块 |
| 做到什么程度 | 主链路 8 个环节**全部跑得通**，4 个真链路 + 4 个如实标注的降级链路；接口 12 个可用 |
| 最大短板 | ① **离职人才过滤回归（本次会话引入，已实测：离职者 97 分排第 1）** ② 人才/简历/文件三类上游接口全是 501 占位 ③ Agent 层无 HTTP 入口 ④ Dify 两条工作流未真实联调 ⑤ 语义匹配未实现 |
| 能不能演示 | **能**，但必须先跑 `scripts/seed_data.py` 造 5 个人才，且只能用 Swagger/curl（无前端）；**演示前必须避开离职过滤问题**（别传离职人员 ID） |
| 代码能不能跑 | **能**，用 `.venv`（Python 3.10.4），非集成测试 228/231 通过 |
| 接手第一件事 | **先提交代码**（27 项未提交改动），再修离职回归（1 行），然后补 `/agents/tasks` |

---

## 1. 项目是做什么的

**平台定位**（`docs/plan/`、`需求输出版.html` V1.4）：
面向 HR、招聘人员、用人部门，把「岗位需求」与「人才档案」转化为可计算、可检索、可解释的数据，辅助人才筛选、内部流动与岗位储备管理。

**产品形态**：不是一个独立产品，而是**6 个 Agent + 公共层**的后端平台。6 个 Agent 分别是：

| Agent | 职责 | 实现状态 |
|---|---|---|
| `resume` 简历解析 | 简历 → 结构化人才档案 | **骨架**（31 行 TODO） |
| `talent` 人才决策/画像 | 人才档案 → 画像、研判 | **骨架**（32 行 TODO） |
| `matching` **岗位人才匹配** | **本次交接对象** | **已实现**（128 行 + 6 个服务文件） |
| `assessment` 人才测评 | 出题、结果研判 | **骨架**（30 行 TODO） |
| `training` 培训推荐 | 课程/计划推荐 | **骨架**（30 行 TODO） |
| `assistant` 智能助手 | RAG 问答 | **骨架**（30 行 TODO） |

> **交接边界**：本次只交接 `matching` 一个 Agent。其余 5 个是骨架，**不要替它们实现**（用户明确要求「不修改其他 Agent」）。但你必须知道它们的接口现状，因为匹配依赖简历解析的人才数据（见第 5.7 节）。

---

## 2. 岗位人才匹配 Agent 的业务需求

### 2.1 业务目标（需求文档原文口径）

三项核心业务结果，**全部纳入第一阶段**：

1. **岗位找人才**：给定岗位，召回并排序合适候选人（主链路，已实现）
2. **人才找岗位**：给定人才，反查适合的岗位（`POST /matchings/reverse`，已实现）
3. **岗位储备预警**：识别零产出/储备不足/长期空缺（已实现简化版）

业务价值：降低人工筛选成本、统一 JobProfile/TalentProfile 口径、让结论**有分数、有分项依据、有原文证据、有待核实事项**。

### 2.2 第一阶段 MVP 范围（需求文档「五大模块」）

| 模块 | 要求 | 当前状态 |
|---|---|---|
| 一、岗位智能解析与画像 | JD → JobProfile 草稿 → HR 确认 → 版本化 | 已实现（解析走规则降级） |
| 二、人才档案接入与标准化 | 复用 Resume Agent 的 Schema/API | **未实现**（Resume Agent 是骨架） |
| 三、人岗匹配（岗位找人才） | 召回→硬过滤→软评分→排序→解释 | 已实现（召回 SQL 降级、解释规则降级） |
| 四、双向匹配（人才找岗位） | 复用统一引擎，不做占位 | 已实现（复用打分内核） |
| 五、岗位储备预警 | 基于真实业务数据形成闭环 | 简化实现（缺编制/继任数据） |

### 2.3 已确定的创新点（需求文档明确的设计取向）

1. **四态判定而非二元通过/不通过**：`PASS / FAIL / UNKNOWN / NEEDS_REVIEW`——「档案缺失」不等于「不合格」。
2. **结构化证据 + 五层校验防编造**：模型不许自由发挥，每条结论必须引用 `evidence_id`，Python 逐条校验，无依据结论剔除并留痕（`rejected_claims`）。
3. **降级必须如实标注**：`source`（dify/manual）、`channel`（dify/llm_fallback/rule_fallback）、`recall_mode`（vector/sql_fallback）三处标识，**禁止把规则结果伪装成 AI 结果**。
4. **画像版本化 + 匹配结果记录版本号**：可回答「这条结论当时依据哪一版要求」。
5. **双向匹配共用一套打分内核**，只反转召回方向，避免两套算法口径漂移。

### 2.4 明确不做的（需求文档「后续增强」）

- 长期人才预测、自动招聘决策、未经验证的模型准确率承诺；
- 项目经历深度挖掘、多 Agent 专业协作、面试问题自动生成、个性化权重学习、招聘反馈闭环优化。

> 请注意：`interview_suggestions` 字段存在（解释里会产出），但「面试问题生成」作为独立能力**不在第一阶段**。

---

## 3. 项目架构

### 3.1 真实目录结构（只列与匹配相关的）

```
ai-digital-talent-platform/
├── main.py                     # 应用入口（uvicorn）
├── requirements.txt            # 依赖清单（注意：UTF-16 编码问题，见 KNOWN_ISSUES P2-5）
├── .env                        # 本地配置（已 gitignore，含 Ollama/Dify 配置）
├── app/
│   ├── core/                   # 公共层：application.py 装配、dependencies、permissions、response、exceptions
│   ├── config/                 # settings.py（pydantic-settings）、prompt_config.py（提示词注册表）
│   ├── models/                 # ORM（26 张表）：matching.py、position.py、talent.py、reserve_warning.py…
│   ├── schemas/                # Pydantic 契约：matching.py（458 行）、position.py、warning.py
│   ├── repositories/           # 数据访问：matching/position/talent/warning_repository.py
│   ├── services/               # ★ 业务核心：matching_*.py、evidence_*.py、jd_parser.py、explanation_service.py
│   ├── routers/                # 接口层：matching_router.py、position_router.py、router.py（总注册）
│   ├── patterns/               # 设计模式：strategy.py（三种匹配策略）、registry.py（注册表）
│   ├── adapters/               # 外部适配：dify/、llm/(ollama)、vector_store/(milvus)、nl2sql/、storage/
│   ├── ai/
│   │   ├── agent.py            # 调度器：bootstrap_ai / dispatch / list_agents / _resolve
│   │   ├── agents/             # base_agent.py + 6 个 Agent（只有 matching_agent.py 实现了）
│   │   ├── skills/             # matching_skill.py（422 行，3 个 Skill）
│   │   ├── workflows/          # matching_workflow.py（LangGraph 6 节点）
│   │   ├── prompts/            # 版本化提示词（只有 matching_prompt.py 有内容）
│   │   ├── core/               # llm_manager.py、model_selector.py、token_manager.py
│   │   └── rag/                # embedding_service.py（已实现）+ 4 个骨架
│   └── infrastructure/         # database/、cache/、vector/、storage/、messaging/
├── docs/                       # 见第 9 节阅读顺序（本次新增 5 份交接文档在根目录）
├── scripts/                    # init_db.py、seed_data.py、e2e_matching_check.py、diagnose_api.py…
└── tests/                      # unit/(9 文件) + integration/(2 文件) + 公共层测试 6 个
```

### 3.2 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| Web | FastAPI + uvicorn | 统一响应 `{code, message, data, trace_id}` |
| ORM | SQLAlchemy 2.x（async）+ aiomysql | 26 张表，MySQL 8 |
| 校验 | Pydantic v2 + pydantic-settings | |
| 工作流 | **LangGraph**（已安装） | 6 节点线性图；ImportError 时降级为顺序执行 |
| 大模型 | **Ollama**（本地 `qwen3:0.6b`）/ 通义 qwen / OpenAI | 通过 `LLMManager` + `model_selector` 按 provider 选模型 |
| 向量 | **bge-m3（1024 维，本地）** + Milvus（未部署） | 适配器已写，`MILVUS_ENABLED=false` |
| 外部工作流 | **Dify**（两条工作流，未联调） | JD 解析、匹配解释 |
| 分析库 | PgSQL + Vanna（未启用） | `PGSQL_ENABLED=false` |
| 缓存 | Redis（未启用） | 进程内降级，多 worker 下限流失效 |
| 存储 | 本地盘（MinIO 未接） | 容器重启即丢文件 |

### 3.3 LangGraph / Python Service / Dify 的分工（重要）

这是本项目最容易被误解的地方，**三者不是替代关系，而是各管一段**：

| 角色 | 负责什么 | 代码位置 | 边界 |
|---|---|---|---|
| **Dify** | 只做「语义提取」与「自然语言表达」：JD → 结构化字段；证据包 → 解释文案 | `app/adapters/dify/dify_adapter.py` | **不判定、不算分、不生成证据 ID** |
| **Python Service** | 所有确定性逻辑：四态判定、四维打分、证据构建、五层校验、落库 | `app/services/*.py` | 不依赖模型，可单测、可复现 |
| **LangGraph** | 编排：把 6 个节点串成图，管状态传递 | `app/ai/workflows/matching_workflow.py` | 只管流程，不含业务规则（节点实现在 Service 里） |

**为什么这样切**：
- 判定/打分必须确定性可复现 → 放 Python；
- 语义理解与文案生成模型更擅长 → 放 Dify/LLM；
- 流程编排与状态传递用 LangGraph，但**不能把业务规则写进图**，否则无法单测。

**降级链**（每一层都必须能独立失败）：
```
JD 解析：Dify → 规则提取（jd_parser.extract_by_rules）
解释：  Dify → LLM（LLMManager）→ 规则模板（explanation_service._via_rule）
召回：  Milvus 向量 → SQL 全量
编排：  LangGraph → 顺序执行
```

### 3.4 与其他 Agent 的依赖关系（真实状态）

```
Resume Agent（骨架）──┐
                     ├──→ 人才档案 talents 表 ──→ 【匹配 Agent】──→ 匹配结果 matching_records
Talent Agent（骨架）──┘                                    │
                                                          └──→ 解释（Dify/LLM）
Assessment Agent（骨架）  ← 无调用关系（潜力等级字段来自档案，不由测评 Agent 写入）
Training Agent（骨架）    ← 无调用关系
Assistant Agent（骨架）   ← 无调用关系
```

| 依赖方向 | 规划中的接口 | 现实 |
|---|---|---|
| 匹配 ← 简历解析 | Resume Agent 解析出 `TalentProfile` → 写 `talents` | **不存在**，`resume_agent.py` 是 TODO |
| 匹配 ← 人才决策 | 画像摘要、潜力等级 | `talents.portrait_summary` / `potential_level` 字段存在，但**无 Agent 写入**（seed 脚本直接造） |
| 匹配 → 测评 | 无 | 无调用 |
| 匹配 → 培训 | 无 | 无调用 |

> **结论**：匹配 Agent 目前是**数据孤岛**——上游靠 seed 脚本喂数据，下游只有自己的查询接口。这是交接后最需要打通的一环。

---

## 4. 业务逻辑与数据流（摘要）

完整版见 **`docs/BUSINESS_FLOW.md`**。这里只给必须记住的三件事：

### 4.1 主链路（8 个环节）

```
JD 解析（Dify→规则） → 画像确认（版本+1） → 召回（向量→SQL） → 四态判定+四维打分
→ 证据构建（每人一包） → 解释生成（Dify→LLM→规则）+ 五层校验 → 组装 → 落库
```

### 4.2 三个必须理解的设计

1. **UNKNOWN 不淘汰，只降权**——档案缺失是数据问题，不是人的问题。
2. **证据 ID 是第一公民**——`EVID-{position_id}-{talent_id}-{seq}`，解释的每条结论都要引用，校验器逐条比对。
3. **`source` / `channel` / `recall_mode` 三处标识**——前端与验收方靠它们识别「这条结果是 AI 还是规则」。

### 4.3 一次跑批的状态流转

见 `BUSINESS_FLOW.md` 第 3 节时序图。关键点：单个候选人解释失败**不中断整批**，该人返回 `explanation_source="rule_fallback"` 并记入 `errors`。

---

## 5. 公共接口与上下游依赖

### 5.1 AgentState

**不存在。** 全项目只有 `MatchingState`（`app/services/matching_pipeline.py`，`TypedDict, total=False`），字段见 `BUSINESS_FLOW.md` 第 4 节。

规划中的 `AgentState` / `WorkflowState`（`docs/plan/02-Agent组.md` D1）**未实现**，Codex 若要加，需同时改 6 个 Agent 的约定。

### 5.2 对外 HTTP 接口（已实现 12 个）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/v1/positions/{id}/jd/parse` | `{jd_text, job_title?}` | `{channel, profile{...}}`，`channel ∈ {dify, rule_fallback}` |
| POST | `/api/v1/positions/{id}/profile/confirm` | `{profile, expected_version, note?}` | `{version, confirmed, profile}` |
| GET | `/api/v1/positions/{id}/profile` | `?version=` | `{version, confirmed, profile}` |
| POST | `/api/v1/matchings/run` | `{position_id, talent_ids?, top_k, min_score, strategy, with_explanation, weights?, requirement_version?}` | `MatchRunResponse`（见 5.3） |
| POST | `/api/v1/matchings/reverse` | `{talent_id, position_ids?, top_k, min_score, strategy, with_explanation, weights?}` | `MatchReverseResponse` |
| GET | `/api/v1/matchings` | `?page&page_size&position_id&talent_id&min_score` | 分页 `{items,total,page,page_size,pages}` |
| GET | `/api/v1/matchings/positions/{id}` | — | 该岗位最新批次 `MatchRunResponse` |
| GET | `/api/v1/matchings/talents/{id}` | — | 历史记录列表 |
| POST | `/api/v1/matchings/explain` | `{position_id, talent_id}` | `MatchExplanation`（不落库） |
| POST | `/api/v1/matchings/warnings/scan` | `{shortage_threshold, long_vacant_days, auto_resolve}` | 扫描结果 |
| GET | `/api/v1/matchings/warnings` | `?status&page...` | 预警分页 |
| POST | `/api/v1/matchings/warnings/{id}/resolve` | `{note?}` | 处理结果 |

**不存在但被文档提到的接口**：

| 路径 | 状态 | 说明 |
|---|---|---|
| `POST /api/v1/agents/tasks` | **不存在** | `agent_router.py` 只有 `GET /agents` 返回 501 |
| `POST /api/v1/agents/chat` | **不存在** | 同上 |
| `GET /api/v1/talents` 等 9 个 | 501 占位 | |
| `GET /api/v1/resumes` 等 6 个 | 501 占位 | |
| `/api/v1/files/*` 3 个 | 501 占位 | **`file_router.py` 文件本身不存在** |

### 5.3 核心 Pydantic Schema（`app/schemas/matching.py`）

```python
# 请求
MatchRunRequest:
    position_id: int
    talent_ids: list[int] | None
    top_k: int = 20            # 1..200
    min_score: float = 0.0     # 0..100
    strategy: Literal["hard","soft","hybrid"] = "hybrid"
    with_explanation: bool = True
    requirement_version: int | None
    weights: dict[str, float] | None   # 本次新增，四维覆盖

# 响应（节选）
MatchRunResponse:
    batch_id: str
    position_id: int
    position_title: str | None
    requirement_version: int
    recall_mode: Literal["vector","sql_fallback"]
    weights_used: dict[str,float] | None   # 本次新增
    matched_at: datetime | None
    stats: MatchStats          # candidates / excluded_by_hard / unknown / needs_review / output
    items: list[MatchItem]
    excluded: list[ExcludedItem]
    errors: list[str]          # 本次新增（降级说明）

MatchItem:
    rank / talent_id / talent_name / decision(HardStatus) / score / dimension_scores
    data_completeness / matched_skills / highlights / gaps
    hard_results: list[HardConditionResult]   # 四态明细
    suggestion / explanation_source / explanation
```

**完整字段以代码为准**，不要照抄本文档——Schema 共 458 行，本次会话新增了 `MatchReverse*` 四个模型。

### 5.4 输入输出 JSON 样例

**JD 解析响应**（降级态）：
```json
{
  "code": 200, "message": "OK", "trace_id": "...",
  "data": {
    "channel": "rule_fallback",
    "profile": {
      "job_title": "后端开发工程师",
      "responsibilities": ["负责平台后端开发"],
      "required_skills": ["Python", "FastAPI", "MySQL"],
      "min_education": "本科", "min_work_years": 3,
      "missing_fields": [], "conflicts": [],
      "review_required": true, "source": "manual", "prompt_version": "rule-v1"
    }
  }
}
```

**匹配响应**（节选）见 `docs/delivery/03_API接口说明.md` 与 `docs/delivery/15_接口交接手册.html`（注意：这两份是上一轮文档，字段可能未覆盖本次新增的 `weights_used`/`errors`）。

### 5.5 与简历解析 Agent 的接口（规划 → 现实）

| 规划 | 现实 |
|---|---|
| Resume Agent 解析简历 → 写 `talents` 主表 + `resumes.parsed_data` | `resume_agent.py` 31 行 TODO，**无任何实现** |
| 匹配读取 TalentProfile Schema | 匹配直接读 `Talent` ORM，字段未与简历产物映射 |
| 共用技能归一化 | 匹配侧有 `matching_skill.normalize_skills`，简历侧无 |

**待确认字段**（交接后需与简历解析负责人对齐）：
- `talents.skill_tags` 的来源与格式（list[str]？逗号分隔？）
- `talents.work_years` 是「总年限」还是「相关年限」
- `talents.education` 的取值枚举（匹配侧按 `高中及以下/中专/大专/本科/硕士/博士` 6 档归一化）
- `talents.portrait_summary` 是 AI 生成还是人工填写——**匹配的证据构建明确排除了它**（不能当事实证据）

### 5.6 与人才测评 Agent 的接口

**无**。匹配的 `quality` 维读的是 `talents.potential_level`（A/B/C/D），这个字段目前由 seed 脚本直接写入，**不由测评 Agent 产生**。

待确认：测评 Agent 是否会写 `potential_level`？若会，匹配的 `quality` 维就有了真实来源；若不会，该维度应视为「演示数据」。

### 5.7 与人才决策 Agent（`talent`）的接口

**无**。`talent_agent.py` 32 行 TODO。匹配不依赖它的输出。

---

## 6. 代码交接（摘要）

逐个文件的实现程度见 **`docs/DEVELOPMENT_STATUS.md`**。这里只列**必须知道的三件事**：

### 6.1 核心文件清单（改动前先读 docstring）

| 文件 | 行数 | 一句话 |
|---|---|---|
| `app/services/evidence_builder.py` | 726 | 把岗位+人才+判定+分数 → 结构化证据包，**纯函数无 IO** |
| `app/services/evidence_verifier.py` | 715 | 五层校验，拦「无证据/证据不存在/术语越界/与判定冲突/拿缺失当具备」 |
| `app/services/matching_pipeline.py` | 693 | 6 个节点 + `run_reverse`，LangGraph 的节点实现都在这 |
| `app/services/jd_parser.py` | 603 | Dify 优先、规则兜底；`normalize_job_profile` 是字段白名单 |
| `app/services/matching_rules.py` | 593 | 四态判定器（学历/年限/技能/模糊条件） |
| `app/services/matching_service.py` | 577 | 编排 + 落库 + 查询 |
| `app/services/explanation_service.py` | 338 | Dify→LLM→规则 三级降级 |
| `app/ai/skills/matching_skill.py` | 422 | 打分纯函数 + 3 个 Skill |
| `app/ai/workflows/matching_workflow.py` | 89 | `NODE_ORDER` 与图构建 |

### 6.2 MATCH-004 结构化证据构建器（用户特别要求核查，已核实）

- **文件**：`app/services/evidence_builder.py`，**726 行**，标记 `*kwq* 新增文件（MATCH-004）`
- **主入口**：`build_match_evidence(position, talent, hard_results=None, dimension_scores=None, *, position_id, talent_id, requirement_version=1, config=None, rule_config=None) -> MatchEvidencePack`
- **性质**：纯函数、无 IO、不调模型
- **三条硬规则**：只搬运不推测 / 不引用模型生成的画像摘要 / 纯函数
- **证据三分类**：`fact`（档案原值）/ `derived`（代码推导）/ `missing`（依据缺失）
- **ID 分配**：两遍策略，第一遍 reserve 已有 ID，第二遍为无效 ID 分配新号（接在最大值后），回填用 `model_copy` 不改入参
- **自检**：`_assert_unique` 发现重复 ID 直接抛 `ParamError`
- **测试**：`tests/unit/test_evidence_builder.py` **19 个用例**，纯函数无 DB
- **已知边界**：职责证据上限 5 条；无项目经历证据（表无该字段）；需要 position_id 与 talent_id 否则报错

### 6.3 关键设计决策（10 条）

见 `DEVELOPMENT_STATUS.md` 第 4 节（DEC-1 ~ DEC-10）。其中最容易被破坏的两条：

- **DEC-4：降级必须如实标注**——任何改动都不得把 `source="manual"` 写成 `dify`；
- **DEC-7：LangGraph 与顺序执行两条路径必须行为一致**——改节点逻辑时两边都要跑测试。

---

## 7. 测试与环境

完整证据见 **`docs/TEST_STATUS.md`**。这里只给结论：

| 项 | 本轮实测 |
|---|---|
| 非集成测试 | **231 个用例，228 通过，3 失败**（全部在 `tests/test_common_layer.py`，根因是 Windows asyncio 测试客户端，非业务缺陷） |
| 集成测试（默认配置） | 37 个用例，**15 分钟未跑完**（单用例实测 3 分 02 秒，原因是真实调用本地 Ollama） |
| 集成测试（关闭外部模型） | **37 个用例，36 通过，1 失败，14.5 秒** |
| 那个失败 | **真实业务回归：离职人才被纳入匹配**（`test_left_status_talent_is_not_matched`，离职者 97 分排第 1，见 `KNOWN_ISSUES.md` P1-0） |
| 单测中匹配相关 | 9 个文件、146 个用例，覆盖 Schema/规则/策略/证据/校验/解析/Dify 适配器/管线 |
| 解释器 | `.venv` = Python **3.10.4**（PATH 的 3.13 无依赖，不要用） |
| 缺失依赖 | `pymilvus` / `redis` / `minio` / `pymupdf` / `python-docx` / `openai` / `langchain` |

**安全测试命令**（不会清库、不调模型）：

```bash
cd "D:/0904开班/agent-system-v1/ai-digital-talent-platform"
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header          # 约 3 秒，146 通过
.venv/Scripts/python.exe -m pytest tests --ignore=tests/integration -q --no-header  # 约 10 秒，228/231
```

**危险命令**（会 DROP 并重建测试库；默认配置下要 15 分钟以上，且执行期间不得跑其它 pytest）：

```bash
.venv/Scripts/python.exe -m pytest tests/integration -q
```

**推荐的集成测试跑法**（14.5 秒，但只覆盖 rule_fallback 通道）：

```bash
LLM_PROVIDER=qwen LLM_API_KEY= DIFY_API_KEY= \
  .venv/Scripts/python.exe -m pytest tests/integration -q
```

---

## 8. Git 与环境信息

| 项 | 值 |
|---|---|
| 当前分支 | `lz_agent` |
| HEAD | `bed2b54c5153a56e2f915a27e4a6af35dedf972a` —— `feat(matching): 岗位人才匹配 Agent 完整实现（MATCH-001~011）` |
| 远程 | `origin/lz_agent` 停在 `f29ac08`（合并 develop 的 PR） |
| 本地与远程 | **本地领先 1 个提交，未推送**；远程无新提交（`HEAD..origin/lz_agent` 为空） |
| 最近提交 | `bed2b54` → `f29ac08`（!7 requirements 补充）→ `6816f44` → `50a0760`（初始化） |
| 已修改未提交 | **19 个**文件（权重/双向匹配/预警/Agent/向量层/配置/测试） |
| 未跟踪 | **6 个**：`reserve_warning.py`、`warning_repository.py`、`schemas/warning.py`、`reserve_warning_service.py`、`docs/sql/2026-11-04_reserve_warnings.sql`、`docs/delivery/18_*.html` |
| Python | 3.10.4（`.venv`） |
| 虚拟环境 | `D:\0904开班\agent-system-v1\ai-digital-talent-platform\.venv` |
| 依赖安装 | `.venv/Scripts/pip.exe install -r requirements.txt`（**注意文件是 UTF-16，见 P2-5**） |
| 启动 | `.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000` |
| 初始化库 | `.venv/Scripts/python.exe scripts/init_db.py` / `scripts/seed_data.py` |
| 端到端自检 | `.venv/Scripts/python.exe scripts/e2e_matching_check.py`（22 项断言） |

**不得再做**（用户明确要求）：
- 不执行 `git commit` / `push` / `merge` / `reset`；
- 不连接生产库；
- 不修改其他 Agent；
- 不删除/覆盖已有文件；
- 不泄露 `.env` 中的 Key 与密码。

> 注：本次交接只读 git，未做任何写操作。第 8 节 P0-2 建议的「先提交」，需由**用户或你**在获得同意后执行。

---

## 9. 阅读顺序（给 Codex 的开工路径）

### 第 1 步：读这 5 份文档（约 30 分钟）

1. **本文** `CODEX_HANDOVER.md`（全局）
2. `docs/BUSINESS_FLOW.md`（链路，重点看第 2.8 证据构建、2.10 五层校验）
3. `docs/DEVELOPMENT_STATUS.md`（文件级进度，重点看第 2.6 骨架清单）
4. `docs/TEST_STATUS.md`（测试证据与命令）
5. `docs/KNOWN_ISSUES.md`（问题与优先级）

### 第 2 步：验证环境（约 10 分钟）

```bash
cd "D:/0904开班/agent-system-v1/ai-digital-talent-platform"
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header      # 应 146 passed 左右
.venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'.');from app.models import Base;print(len(Base.metadata.tables))"  # 应 26
```

### 第 3 步：跑一次真实匹配（约 5 分钟）

```bash
# 1) 起服务
.venv/Scripts/python.exe -m uvicorn main:app --port 8000
# 2) 打开 http://127.0.0.1:8000/docs
# 3) POST /api/v1/auth/login 拿 token（默认管理员见 docs/delivery/10_Swagger测试数据.md）
# 4) POST /api/v1/matchings/run {"position_id": <seed 岗位 id>, "top_k": 5}
# 5) 观察响应里的 recall_mode / explanation_source，确认降级标注正确
```

### 第 4 步：按优先级开工（建议顺序）

| 顺序 | 任务 | 理由 | 预估改动面 |
|---|---|---|---|
| 1 | **提交代码 + 执行两份 DDL** | 零开发量，但不做就没法安全改代码 | 0 行业务代码 |
| 2 | **修离职人才回归（P1-0）** | 已实测复现的业务缺陷：离职者 97 分排第 1；改动仅 1 行 + 1 个单测 | `matching_pipeline.py` 1 行 + 单测 |
| 3 | **补 `POST /agents/tasks`** | 让已实现的 `matching_agent` 有入口，验收硬指标 | 1 个 router + 1 个 service |
| 4 | **双向匹配端到端验证 + 补集成测试** | 功能已写但没跑过，风险最高 | 2 个测试用例 |
| 5 | **修集成测试环境（独立库 + 慢用例标记）** | 现在 15 分钟跑不完、且并行会互相清表 | conftest + pytest.ini |
| 6 | **人才档案最小接口（4 个）** | 打通上游，摆脱 seed 依赖 | 1 个 router + service |
| 7 | Dify 联调（等用户给 Key） | 外部依赖，拿到 Key 才能做 | 改 `DEFAULT_WORKFLOWS` |
| 8 | 语义匹配（向量召回 + 职责相似度维度） | 需求差距最大，但成本最高，建议最后 | 大 |

---

## 10. 交接自检清单

- [x] 文档内容全部以当前真实代码为依据（文件、行数、函数名均核对）
- [x] 区分了「已实现 / 降级实现 / 骨架 / 占位 / 待核实」
- [x] 测试数字为本轮实测（非历史报告引用）
- [x] 集成测试得到三组真实数据：默认配置 15 分钟未完成 / 并发污染 18 errors（无效）/ 关闭外部模型 36 通过 1 失败（14.5s）
- [x] 未修改任何业务代码（离职回归只记录、未修）
- [x] 未删除、未覆盖任何已有文件（5 份文档均为新建）
- [x] 未执行任何 git 写操作（commit/push/merge/reset）
- [x] 未连接生产库（测试用 `ai_talent_platform_test`，开发库 `ai_talent_platform` 未被测试触碰）
- [x] 未修改其他 Agent
- [x] 未输出密码、Token、API Key（`.env` 只列了配置项名，值已脱敏）
- [x] 已确认 `POST /agents/tasks` **不存在**（不是待核实）
- [x] 3 个公共层失败用例的 traceback 已取得（Windows asyncio 测试客户端问题）
- [ ] `test_health_ready` 的断言期望值/实际值对比 —— **待核实**（需 `--tb=long` 展开）
- [ ] 「离职回归是否为唯一回归」—— 未做 `git stash` 前后对比验证，**待核实**

---

## 11. 一句话交给 Codex

> 这个模块的**业务逻辑已经完整且设计克制**（四态、证据锚定、降级如实标注），**缺的不是算法而是入口和数据源**：人才档案没接口、Agent 没路由、Dify 没联调。
> 但有一个必须最先修的东西：**本次会话改写召回时把离职过滤弄丢了**（离职者以 97 分排在推荐第 1），改动只有 1 行，方法在 `KNOWN_ISSUES.md` P1-0。
> 接手后先不要重写打分或证据逻辑——先提交、先修回归、先补入口、先把测试环境隔离出来，再谈语义匹配。
