# 岗位人才匹配 Agent —— 代码开发进度

> 本文以**仓库当前工作区**为准（HEAD `bed2b54` + 未提交改动），逐文件说明实现程度。
> 状态口径：**【已实现】** 有完整逻辑且被主链路调用；**【降级实现】** 逻辑完整但外部依赖未接通，运行时如实降级；**【部分实现】** 有主体但需求要求的字段/分支缺失；**【骨架】** 只有 docstring 与 TODO，无逻辑；**【占位】** 路由返回 501。
> 最近核对时间：2026-11-04 18:40（本地时间）

---

## 1. 总体进度

| 维度 | 数字 |
|---|---|
| 匹配 Agent 相关业务代码 | 约 **6100 行**（14 个核心文件） |
| 已提交（`bed2b54`）文件 | 45 个（含 13 份交付文档与 DDL） |
| 未提交改动 | 19 个修改文件 + 6 个新增文件（本次会话的功能补全与文档） |
| 主链路环节 | 8 个环节：4 个真链路 + 4 个降级链路 |
| 对外接口 | 12 个可用（9 个匹配/画像 + 3 个预警） + 1 个不存在（`/agents/tasks`） |
| 数据库表 | 26 张 ORM 映射（本次新增 `reserve_warnings`） |

---

## 2. 核心文件逐个体检

### 2.1 服务层（业务核心）

| 文件 | 行数 | 状态 | 内含关键类/函数 |
|---|---|---|---|
| `app/services/evidence_builder.py` | 726 | 【已实现】 | `EvidenceBuildConfig`、`_EvidenceIdAllocator`、`build_job_profile`、`_evidence_from_hard_result`、`_preferred_skill_evidences`、`_responsibility_evidences`、`_dimension_evidences`、`_missing_field_evidences`、`build_match_evidence`、`_assert_unique` |
| `app/services/evidence_verifier.py` | 715 | 【已实现】 | `VerifyConfig`、`VerificationResult`、`audit_pack`、`verify_explanation`、`_verify_claim`、`_factual_summary`；三档输出 pass/reject/pending |
| `app/services/matching_pipeline.py` | 693 | 【已实现】 | `MatchingState`、`MatchingPipeline`（`load_context` / `recall` / `_try_vector_recall` / `filter_score` / `build_evidence` / `explain` / `assemble` / `run_reverse`）、`_normalize_weights` |
| `app/services/jd_parser.py` | 603 | 【降级实现】 | `parse_jd`、`extract_by_rules`、`normalize_job_profile`、`_split_sections`、`_extract_skills`、`_extract_salary`、`_normalize_education`、`summarize_missing` |
| `app/services/matching_rules.py` | 593 | 【已实现】 | `HardRuleConfig`、`DEFAULT_HARD_RULE_CONFIG`、`build_profile_view`、`build_evidence_id`、`_judge_education`、`_judge_work_years`、`_judge_required_skills`、`_judge_vague_conditions`、`judge_hard_conditions`、`aggregate_status`、`compute_data_completeness`、`apply_unknown_penalty`、`preferred_skill_hits` |
| `app/services/matching_service.py` | 577 | 【已实现】 | `MatchingService.run` / `run_reverse` / `list_records` / `latest_by_position` / `history_by_talent` / `explain` / `_persist` |
| `app/services/explanation_service.py` | 338 | 【降级实现】 | `ExplanationService.generate`、`_via_dify`、`_via_llm`、`_via_rule`、`_to_explanation`、`ExplanationOutcome` |
| `app/services/reserve_warning_service.py` | 321 | 【已实现】（本次新增） | `scan` / `list` / `resolve`；三类预警判定 |
| `app/services/position_profile_service.py` | 223 | 【已实现】 | `parse` / `confirm` / `get_profile` / `require_confirmed` / `_resolve_version` |

### 2.2 契约层

| 文件 | 行数 | 状态 | 内容 |
|---|---|---|---|
| `app/schemas/matching.py` | 458 | 【已实现】 | `HardStatus`、`Evidence`、`MatchEvidencePack`、`ExplanationClaim`、`RejectedClaim`、`MatchExplanation`、`MatchItem`、`MatchStats`、`MatchRunRequest/Response`、`MatchReverseRequest/Item/Response`（本次新增）、`MatchExplainRequest` |
| `app/schemas/position.py` | 149 | 【已实现】 | `JobProfileParsed`、`JobProfileConfirmRequest`、`JobProfileView`、JD 解析请求/响应 |
| `app/schemas/warning.py` | — | 【已实现】（本次新增） | 预警扫描/列表/解决请求与响应 |

### 2.3 接入层

| 文件 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `app/routers/matching_router.py` | 243 | 【已实现】 | 9 个端点：run / reverse / list / positions / talents / explain / warnings×3 |
| `app/routers/position_router.py` | — | 【已实现】 | 3 个端点：jd/parse、profile/confirm、profile GET |
| `app/routers/agent_router.py` | 66 | 【占位】 | **只有 `GET /agents` 返回 501**；docstring 里列的 chat/tasks 等 5 个接口**均未实现** |
| `app/routers/router.py` | — | 【已实现】 | 已实现模块 `auth/users/organizations/positions/matchings`；其余 8 个走 `placeholder_router`（501） |

### 2.4 AI 能力层

| 文件 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `app/ai/skills/matching_skill.py` | 422 | 【已实现】 | `compute_match_score`（纯函数）+ 3 个 Skill：`matching_score` / `position_search` / `candidate_list` |
| `app/ai/agents/matching_agent.py` | 128 | 【已实现】（本次新增） | `MatchingAgent(BaseAgent)`：`build_prompt` / `parse_output` / `persist`，注册为 `matching` |
| `app/ai/agents/base_agent.py` | 222 | 【已实现】 | `BaseAgent` 抽象：`run` / `stream` / `describe` / `prompt_version` |
| `app/ai/prompts/matching_prompt.py` | 46 | 【已实现】（本次新增） | 版本化提示词：`render_run_summary` 等 |
| `app/ai/workflows/matching_workflow.py` | 89 | 【已实现】 | `NODE_ORDER`（6 节点）、`build_matching_graph`、`run_matching`（LangGraph 优先，ImportError 顺序兜底） |
| `app/ai/core/llm_manager.py` | — | 【已实现】（本次修复） | `get_llm` / `get_embedding_llm` / `chat` / `embed` / `generate`；修了 embed 模型未生效的 bug |
| `app/ai/core/model_selector.py` | 148 | 【已实现】（本次修改） | `select_model(purpose)` / `select_embedding_model()`，本次增加 provider 感知（Ollama 不再收到 `qwen-plus`） |
| `app/ai/rag/embedding_service.py` | 84 | 【已实现】（本次重写） | `EmbeddingService.embed_query / embed_texts / verify_dimension` |

### 2.5 适配器与基础设施

| 文件 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `app/adapters/dify/dify_adapter.py` | 480+ | 【已实现（契约待联调）】 | `DifyAdapter.run_workflow / run_job_profile_extraction / run_match_explanation`；`DEFAULT_WORKFLOWS` 的 `required_inputs` 标注「暂定·待联调」 |
| `app/adapters/llm/ollama_adapter.py` | — | 【已实现】（本次修改） | 增加 `think: false` 与 `num_predict` 保护，解决 qwen3 思考模型返回空内容 |
| `app/adapters/vector_store/milvus_adapter.py` | — | 【已实现（未启用）】（本次重写） | pymilvus 封装；`MILVUS_ENABLED=false` 时抛 `MilvusUnavailableError`，调用方回退 SQL |
| `app/infrastructure/vector/milvus.py` | — | 【已实现（未启用）】（本次重写） | 连接管理 `get_milvus` / `run_milvus` / `MilvusUnavailableError` |
| `app/models/reserve_warning.py` | — | 【已实现】（本次新增） | `ReserveWarning` ORM，表 `reserve_warnings` |
| `app/repositories/warning_repository.py` | — | 【已实现】（本次新增） | 预警分页/去重/状态更新 |
| `app/models/matching.py` | — | 【已实现】 | `MatchingRecord`（含 MATCH-009 的 5 个追溯列） |

### 2.6 明确是骨架的文件（无业务逻辑）

以下 14 个文件只有 docstring + `TODO(负责人)`，共约 400 行，**不要误认为是已实现能力**：

| 分类 | 文件 | 行数 |
|---|---|---|
| Agent | `app/ai/agents/resume_agent.py` | 31 |
| Agent | `app/ai/agents/talent_agent.py` | 32 |
| Agent | `app/ai/agents/assessment_agent.py` | 30 |
| Agent | `app/ai/agents/training_agent.py` | 30 |
| Agent | `app/ai/agents/assistant_agent.py` | 30 |
| 提示词 | `app/ai/prompts/resume_prompt.py` | 31 |
| 提示词 | `app/ai/prompts/assessment_prompt.py` | 31 |
| 提示词 | `app/ai/prompts/assistant_prompt.py` | 31 |
| 工作流 | `app/ai/workflows/resume_workflow.py` | 31 |
| 工作流 | `app/ai/workflows/analysis_workflow.py` | 24 |
| 工作流 | `app/ai/workflows/graph_builder.py` | 26 |
| RAG | `app/ai/rag/document_loader.py` | 21 |
| RAG | `app/ai/rag/document_splitter.py` | 20 |
| RAG | `app/ai/rag/retriever.py` | 23 |
| RAG | `app/ai/rag/rag_pipeline.py` | 25 |
| NL2SQL | `app/adapters/nl2sql/vanna_adapter.py` | 27 |
| NL2SQL | `app/adapters/nl2sql/sql_validator.py` | 27 |

> `matching_agent.py` 是唯一已实现的 Agent；其余 5 个 Agent 全是骨架，`bootstrap_ai()` 用延迟导入 + 异常吞掉的方式跳过它们，所以启动不会报错，但 `agent_registry` 里只有 `matching` 一个。

---

## 3. 本次会话（HEAD 之后）的改动明细

> 背景：上一轮已完成 MATCH-001~011 并提交为 `bed2b54`；本次会话按「推荐顺序」继续补功能，**未提交**。

### 3.1 新增文件（6 个，均未跟踪）

| 文件 | 作用 |
|---|---|
| `app/models/reserve_warning.py` | 储备预警 ORM（表 `reserve_warnings`） |
| `app/repositories/warning_repository.py` | 预警数据访问 |
| `app/schemas/warning.py` | 预警请求/响应契约 |
| `app/services/reserve_warning_service.py` | 三类预警扫描、去重、人工解决 |
| `docs/sql/2026-11-04_reserve_warnings.sql` | 建表 DDL（**尚未在测试/生产库执行**） |
| `docs/delivery/18_Dify组对接需求文档.html` | 给 Dify 组的契约文档 |

### 3.2 修改文件（19 个）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `app/schemas/matching.py` | ① `MatchRunRequest` 增加 `weights` + 校验器；② `MatchRunResponse` 增加 `weights_used`；③ 追加 `MatchReverseRequest/Item/Skipped/Response` | 需求「权重可配」与「双向匹配」 |
| `app/services/matching_pipeline.py` | ① `MatchingState` 增加 `weights`；② `_normalize_weights` 提取为模块函数；③ `load_context` 应用权重覆盖；④ `recall` 接入向量分支 + `MIN_VECTOR_SCORE`；⑤ 新增 `run_reverse` | 权重贯通 + 向量召回 + 双向匹配。**⚠️ 其中 ④ 把「指定候选人」分支从 `list_candidates` 换成了 `get_by_ids`，后者不过滤 `status`，导致离职人才被纳入匹配（已实测，见 `KNOWN_ISSUES.md` P1-0）** |
| `app/services/matching_service.py` | ① `run()` 传 weights、响应回传 `weights_used`；② 新增 `run_reverse()` | 同上 |
| `app/routers/matching_router.py` | 新增 `POST /matchings/reverse`；挂 3 个预警端点 | 对外暴露新能力 |
| `app/ai/agents/matching_agent.py` | 从 TODO 骨架重写为完整实现（128 行） | 让 `matching` 类型可被调度 |
| `app/ai/prompts/matching_prompt.py` | 从空文件重写为版本化提示词 | Agent 需要 prompt |
| `app/config/prompt_config.py` | 注册 `matching_run_summary` 模板 | 同上 |
| `app/ai/core/model_selector.py` | 按 provider 选择模型（Ollama 用本地模型名） | 切本地模型后原策略表会给出不存在的模型名 |
| `app/ai/core/llm_manager.py` | 修 `embed()`：向量模型真正生效 | 原代码算出 `target_model` 却没用，Ollama 下会用对话模型做 embedding |
| `app/adapters/llm/ollama_adapter.py` | 增加 `think: false`；`num_predict` 不足时不再返回空 | qwen3 是思考模型，tokens 被思考吃光 |
| `app/adapters/vector_store/milvus_adapter.py` | 从骨架重写（pymilvus 封装 + 降级异常） | 向量召回代码就绪 |
| `app/infrastructure/vector/milvus.py` | 从骨架重写（连接管理） | 同上 |
| `app/ai/rag/embedding_service.py` | 从骨架重写（84 行） | 同上 |
| `app/patterns/registry.py` | 注册 `vector_store_registry` | 适配器注册 |
| `app/models/__init__.py` | 注册 `ReserveWarning` | 表元数据 |
| `app/config/settings.py` | 增加 `LLM_EMBED_MODEL` / `LLM_EMBED_DIM`，默认 provider 支持本地 | 本地模型接入 |
| `app/core/permissions.py` | 加 `StrEnum` 兼容垫片（3.10） | `.venv` 是 Python 3.10，`StrEnum` 是 3.11+ |
| `tests/unit/test_explanation_service.py` | 2 个用例显式屏蔽 LLM 通道 | 接上 Ollama 后原用例测不到规则兜底分支 |
| `tests/test_orm_mapping.py` | 表数断言 25 → 26，清单加 `reserve_warnings` | 新增表是合法变更 |

### 3.3 新功能验证记录（本次会话）

| 功能 | 验证方式 | 结果 |
|---|---|---|
| 本地 Ollama 对话 | `LLMManager.chat(purpose="matching")` | ✅ 返回可读文本，tokens 正常 |
| 本地向量 | `LLMManager.embed(["..."])` | ✅ dim=1024（与 bge-m3 一致） |
| 权重可配 | Schema 层单测：合法通过 / 未知维度拒绝 / 负数拒绝 / 全 0 拒绝 / 归一化 4:3:2:1 | ✅ 全部符合预期 |
| 双向匹配 | 模块导入 + `MatchReverseRequest` 校验 + `run_reverse` 存在性 | ✅ 导入通过（**未做端到端 HTTP 调用**，见 `KNOWN_ISSUES.md` P1-2） |
| Agent 注册 | `bootstrap_ai()` → `agent_registry.keys()` | ✅ 含 `matching`；prompt 渲染正常 |
| 预警模块 | 表注册 + Schema 默认值 + service/router 导入 | ✅ 通过（**接口未实跑**，表未建） |
| 向量降级 | `get_milvus()` 返回 None，`has_collection` 抛 `MilvusUnavailableError` | ✅ 降级正确 |

---

## 4. 关键设计决策（接手前必须理解）

| 编号 | 决策 | 原因 | 代价 |
|---|---|---|---|
| DEC-1 | **证据 ID 锚定一切**：解释的每条结论必须引用 `EVID-{pid}-{tid}-{seq}` | 只靠 prompt 约束无法阻止模型编造，必须让 Python 可校验 | 模型输出受限，需在 Dify prompt 里反复强调 |
| DEC-2 | **三档而非二档**：pass / reject / pending | 「信息不足」不等于「错」，不能当事实展示也不能直接丢弃 | 前端要区分三种展示形态 |
| DEC-3 | **UNKNOWN 不淘汰，只降权** | 档案缺失是数据问题，不是候选人问题；默认淘汰会误杀 | 需要 HR 看到 `pending_verification` |
| DEC-4 | **降级必须如实标注**：`source` / `channel` / `recall_mode` 三处标识 | 假接通比没接通更危险，会让验收失真 | 前端必须能渲染 3 种来源 |
| DEC-5 | **画像确认即新版本**，`expected_version` 乐观锁 | 防止两个人同时确认导致互相覆盖 | 前端确认前必须带版本号 |
| DEC-6 | **双向匹配复用同一打分内核**，只反转召回方向 | 需求明确要求「不复制两套算法」 | 反向场景下 `hard_results` 语义需向使用者解释清楚 |
| DEC-7 | **LangGraph 优先、顺序执行兜底** | langgraph 缺失时不能整条链路挂掉 | 两条路径需同时维护，行为必须一致 |
| DEC-8 | **纯函数化证据构建**（无 IO） | 可单测、可复现、可离线跑 | Service 层要负责把数据取全 |
| DEC-9 | **`matching_records` 不建物理外键** | 记录可重建、与主数据生命周期不一致 | 存在孤儿记录风险，靠 Service 校验 |
| DEC-10 | **不引入 SQLite**（团队规范） | 避免测试与生产 SQL 方言差异 | 单测必须做成纯函数或 mock，集成测试必须连 MySQL |

---

## 5. 依赖与环境实况

| 项 | 事实 |
|---|---|
| 解释器 | `.venv` = **Python 3.10.4**（PATH 上的 3.13 无项目依赖；anaconda base 3.13 缺多数依赖） |
| 已安装关键包 | `langgraph` ✅、`httpx 0.28.1` ✅ |
| **未安装** | `pymilvus` ❌、`redis` ❌、`minio` ❌、`pymupdf(fitz)` ❌、`python-docx` ❌、`openai` ❌、`langchain` ❌ |
| 影响 | 向量适配器虽已实现，**当前环境 import 即失败**（被 try/except 捕获后降级）；文件存储只有本地盘；RAG 全链路不可用 |
| `requirements.txt` | **UTF-16LE + BOM 编码**（PowerShell 重定向产物），git 当二进制处理；pip 读取可能异常，**建议转 UTF-8** |
| 本地模型 | Ollama 运行中：`qwen3:0.6b`（对话）、`bge-m3`（向量，1024 维） |
| 数据库 | MySQL 127.0.0.1:3306 可连（`ai_talent_platform`）；PgSQL / Redis / Milvus 均 `false` |

---

## 6. 未实现清单（按影响排序）

| # | 未实现项 | 影响 |
|---|---|---|
| 1 | `/agents/tasks`、`/agents/chat` 等 5 个 Agent 接口 | `matching_agent` 无 HTTP 入口，Agent 能力无法被外部触发 |
| 2 | `/talents` 9 个接口（501 占位） | 无法自助建人才，任何新环境必须跑 seed |
| 3 | `/resumes` 6 个接口（501）+ 简历解析 Agent（骨架） | 匹配上游数据入口缺失 |
| 4 | `/files` 3 个接口（`file_router.py` 文件不存在） | 无文件上传能力 |
| 5 | 语义匹配（项目经验/职责相似度/行业经验维度） | 打分只有 4 个规则维度，与需求 5.2 差距最大 |
| 6 | 任务状态机（CREATED→…→COMPLETED） | 长任务只能同步等待，无进度查询 |
| 7 | 向量召回实际启用（集合未建、pymilvus 未装） | `recall_mode` 恒为 `sql_fallback` |
| 8 | 储备预警的编制/继任数据模型 | 预警只覆盖「岗位+匹配记录+人才状态」三类 |
| 9 | 解析置信度字段 | 需求要求保留，Schema 无此字段 |
| 10 | 评分规则版本号落库 | 只有权重与分项，规则版本靠常量 |
| 11 | 审计日志 | 有权限校验，无操作审计表 |
| 12 | Dify 两条工作流真实联调 | 适配器就绪，契约「待联调」 |

---

## 7. 待验证项（代码在但未实跑）

| 项 | 为什么没验证 | 建议验证方式 |
|---|---|---|
| `POST /matchings/reverse` | 只做了导入与 Schema 校验 | 用 seed 数据发一次真实请求，确认返回岗位列表与 `excluded` 原因 |
| 预警三个接口 | 表未在库中创建 | 先执行 `docs/sql/2026-11-04_reserve_warnings.sql`，再调 scan |
| 向量召回分支 | pymilvus 未装、Milvus 未部署 | 装 pymilvus + 起 Milvus + 建集合，观察 `recall_mode="vector"` |
| `matching_agent` 端到端 | 无 HTTP 入口 | 补 `POST /agents/tasks` 后测 `type="matching"` |
| 集成测试全量 | 见 `TEST_STATUS.md`（LLM 调用导致单用例 3 分钟） | 建议加 `--timeout` 或测试内强制 `rule_fallback` |

---

## 8. 与历史交付文档的关系

`docs/delivery/01~18` 是**上一轮交付时**的文档，其中 `09_最终交付验收报告.md` 声称「183 条测试通过」。本次实测总数已变为 231（非集成）+ 37（集成）= 268，原因是后续新增了测试与表。

**请勿直接引用历史报告的数字**，一切以 `TEST_STATUS.md` 的本次实测结果为准。历史文档的价值在于业务背景与接口示例，其测试口径已过期。
