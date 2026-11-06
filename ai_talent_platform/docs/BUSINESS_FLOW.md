# 岗位人才匹配 Agent —— 业务逻辑与数据流

> 文档定位：本文依据**当前仓库真实代码**逐环节拆解「岗位找人才」主链路，并标注每一环节的文件、核心函数、输入输出、调用关系与实现状态。
> 凡与需求文档（`agent-system-v1/需求输出版.html` V1.4）不一致的地方，在第 6 节集中列出，不在此处粉饰。
> 状态图例：【已实现】代码完整且被主链路调用；【降级实现】能跑但外部依赖未接通，如实标注降级；【未实现】代码不存在或仅占位；【待核实】无法从代码确认。
>
> 配套文档：`CODEX_HANDOVER.md`（总览）、`DEVELOPMENT_STATUS.md`（文件级进度）、`TEST_STATUS.md`（测试证据）、`KNOWN_ISSUES.md`（问题清单）

---

## 1. 链路总览

需求文档定义的完整链路共 11 步，落到代码后被组织为 **LangGraph 6 节点流水线 + 前后置服务**：

```
岗位 JD 输入
   ↓  POST /positions/{id}/jd/parse
[JD 解析]  jd_parser.parse_jd ──Dify 优先──→ 规则提取兜底
   ↓  画像草稿（review_required=true）
[画像确认]  position_profile_service.confirm ──→ position_requirements 新增版本
   ↓  （人才档案必须存在，否则候选池为空）
[召回]   matching_pipeline.recall ──Milvus 可用则向量──→ 否则 SQL 全量
   ↓
[条件过滤 + 软性评分]  matching_pipeline.filter_score ──→ MatchStrategy(hard/soft/hybrid)
   ↓  scored / excluded / stats
[证据构建]  matching_pipeline.build_evidence ──→ evidence_builder.build_match_evidence
   ↓  MatchEvidencePack（每人一份）
[解释生成 + 五层校验]  matching_pipeline.explain ──→ explanation_service.generate
                                                └─→ evidence_verifier.verify_explanation
   ↓  MatchExplanation（或规则兜底）
[组装 + 落库]  matching_pipeline.assemble ──→ matching_service._persist → matching_records
   ↓
[查询/报告]  GET /matchings/positions/{id} 等 4 个读接口
```

**实现状态一句话**：8 个环节全部跑得通，其中 **4 个是真链路**（画像确认、四态判定、四维打分、证据+校验），**4 个是如实标注的降级链路**（JD 解析走规则、召回走 SQL、解释走规则/LLM、向量未启用）。

---

## 2. 逐环节详解

### 2.0 前置：岗位与人才数据从哪来

| 项 | 现状 | 状态 |
|---|---|---|
| 岗位创建 | `positions` 表 + `PositionRepository`，但**没有 `POST /positions` 建岗接口**（只有解析/确认/查询 3 个） | 【未实现】 |
| 人才档案 | `talents` 表存在，`/talents` 9 个接口全是 501 占位；演示数据靠 `scripts/seed_data.py` 造 5 人 | 【未实现】 |
| 画像确认 | `POST /positions/{id}/profile/confirm` 真实可用 | 【已实现】 |

> **这是整条链路最大的上游风险**：匹配能跑，是因为它直接读 `Talent` 表；但只要没有人才录入接口，任何新环境都必须先跑 seed 脚本才能演示。

---

### 2.1 环节一：JD 输入与解析

| 项 | 内容 |
|---|---|
| 入口 | `POST /api/v1/positions/{position_id}/jd/parse` |
| 路由 | `app/routers/position_router.py`（`parse_jd` 端点） |
| 服务 | `app/services/position_profile_service.py :: PositionProfileService.parse()` |
| 核心函数 | `app/services/jd_parser.py :: parse_jd(jd_text, *, job_title=None, adapter=None)` |
| 规则实现 | `jd_parser.extract_by_rules(jd_text, job_title)`；字段标准化 `jd_parser.normalize_job_profile()` |
| 输入 | `jd_text`（≥20 字，否则 `ParamError`）、`job_title`（可选提示） |
| 输出 | `JobProfileParsed`（pydantic）：`job_title / responsibilities / required_conditions / preferred_conditions / required_skills / preferred_skills / min_education / min_work_years / location / salary_range / missing_fields / conflicts / review_required / source / prompt_version` |
| 调用关系 | `PositionProfileService.parse` → `jd_parser.parse_jd` → `DifyAdapter.run_job_profile_extraction`（若已配置 Key）→ 失败则 `extract_by_rules` |
| 状态 | 【降级实现】Dify 未配置 → `source="manual"`；响应 `channel="rule_fallback"` |

**关键设计**：
- `source` 只有三种合法值 `dify / llm_fallback / manual`，`parse_jd` 的返回值直接反映真实通道，**禁止把规则结果标成 dify**。
- 解析产物 `review_required=True` 恒为真——即使字段全齐也不能自动生效。
- `normalize_job_profile` 做三件事：白名单字段过滤（Dify 的调试字段不进业务对象）、缺字段记 `missing_fields`（**不补值**）、冲突显式记录（技能同时必备+优先、学历不可识别、年限 >45）。
- `job_title` 缺失直接抛 `ParamError`，不允许用占位名建画像。

**已知偏差**：需求要求「字段保留解析置信度」，代码里**没有置信度字段**（`JobProfileParsed` 无 `confidence`）。

---

### 2.2 环节二：画像确认（版本化）

| 项 | 内容 |
|---|---|
| 入口 | `POST /api/v1/positions/{position_id}/profile/confirm` |
| 服务 | `PositionProfileService.confirm()` |
| 输入 | `JobProfileConfirmRequest`：`profile`（完整画像）、`expected_version`（乐观锁）、`note` |
| 输出 | `JobProfileView`：`position_id / version / confirmed / profile / confirmed_at / note` |
| 调用关系 | 校验 `expected_version == 当前最新版本`（不一致抛 400，防覆盖他人确认）→ `repo.next_requirement_version()` → 写 `position_requirements` 新行 → 同步 `positions.requirements` + `profile_confirmed` + `requirement_version` |
| 状态 | 【已实现】 |

**为什么版本化**：匹配结果要能回答「这条结论当时依据哪一版画像」。`matching_records.requirement_version` 记录的就是这一版号。

**读取画像**：`PositionProfileService.get_profile(position_id, version=None)`；`require_confirmed()` 在匹配前强制校验，未确认直接 400（`画像未确认`）。

---

### 2.3 环节三：人才画像接入

| 项 | 内容 |
|---|---|
| 现状 | **无独立接入环节**。`matching_pipeline.recall` 直接从 `talents` 表取 ORM 对象，字段在 `app/models/talent.py` |
| 服务 | `app/repositories/talent_repository.py :: list_candidates(db, talent_ids=None, limit=N)` |
| 输入 | `talent_ids`（指定候选人）或 `None`（全量） |
| 输出 | `list[Talent]` ORM 对象 |
| 状态 | 【未实现（作为独立能力）】/ 【可用（作为数据读取）】 |

**需求对照**：需求模块二要求「复用 Resume Agent 的 TalentProfile Schema、向量集合、API」——**当前 Resume Agent 是 TODO 骨架（31 行），没有任何可复用接口**。匹配读的是 `talents` 主表字段，尚未与简历解析产物做字段映射。

**待核实**：`talents` 表字段与 Resume Agent 规划中的 `TalentProfile` 字段是否一一对应，需与简历解析负责人对齐。

---

### 2.4 环节四：人才召回

| 项 | 内容 |
|---|---|
| 节点 | `MatchingPipeline.recall(state)`（`matching_pipeline.py:251`） |
| 输入 | `state["talent_ids"]`、`self.recall_limit`（默认上限，见 `DEFAULT_RECALL_LIMIT`） |
| 输出 | `{"candidates": list[Talent], "recall_mode": "sql_fallback" | "vector", "errors": [...]}` |
| 向量分支 | `MatchingPipeline._try_vector_recall(state)`（`matching_pipeline.py:306`）→ `EmbeddingService.embed_query` → `MilvusAdapter.search` |
| 状态 | 【降级实现】`MILVUS_ENABLED=false` → 恒为 `sql_fallback` |

**实现细节**：
- **指定 `talent_ids` 时走 `TalentRepository.get_by_ids()`（直接回表），该路径只过滤 `is_deleted`、不过滤 `status`** → 离职人员会被纳入匹配，这是本次会话引入的回归缺陷（实测离职者 97 分排第 1），详见 `KNOWN_ISSUES.md` P1-0；全库召回走 `list_candidates(statuses=("active","reserve"))`，过滤正确。
- 向量分支的启用条件是「Milvus 已启用 + 有向量数据 + 相似度 ≥ `MIN_VECTOR_SCORE`」，任一不满足即回退 SQL，并在 `errors` 中留痕。
- 候选池触及 `recall_limit` 时写一条 warning（「可能存在未参与计算的人员」），不静默截断。
- `recall_mode` 会落库到 `matching_records.recall_mode`，重放历史批次时能区分向量召回与降级。

**当前真实行为**：Milvus 未部署（`MILVUS_ENABLED=false`），`pymilvus` 也未安装 → 100% 走 SQL 全量召回。演示库只有 5 人，此时代码里的向量召回收益为零。

---

### 2.5 环节五：条件过滤（四态判定）

| 项 | 内容 |
|---|---|
| 节点 | `MatchingPipeline.filter_score(state)`（`matching_pipeline.py:357`） |
| 判定器 | `app/services/matching_rules.py :: judge_hard_conditions(talent, position, *, config, position_id, talent_id)` |
| 策略 | `app/patterns/strategy.py`：`HardFilterStrategy` / `SoftMatchStrategy` / `HybridStrategy`，工厂 `MatchStrategyFactory.create(name, weights, rule_config)` |
| 输入 | 候选人列表 + `position_view`（`load_context` 产出的岗位画像视图） + 权重 |
| 输出 | `scored`（通过者，含 `score / dimensions / hard_results / highlights / gaps`）、`excluded`（FAIL 者及原因）、`stats` |
| 状态 | 【已实现】 |

**四态语义**（`app/schemas/matching.py :: HardStatus`）：

| 状态 | 含义 | 处理 |
|---|---|---|
| `PASS` | 有明确证据证明满足 | 进入评分 |
| `FAIL` | 有充分证据证明不满足 | 淘汰，写入 `excluded` |
| `UNKNOWN` | 档案缺少必要信息 | **不淘汰**，降权（`apply_unknown_penalty`），计入 `stats.unknown` |
| `NEEDS_REVIEW` | 条件模糊，需人工判断 | 不淘汰，标注待复核 |

**判定覆盖的条件类型**：学历（`_judge_education`）、工作年限（`_judge_work_years`）、必备技能（`_judge_required_skills`）、模糊条件（`_judge_vague_conditions`，仅当文本未被结构化条件覆盖时）。聚合函数：`aggregate_status` / `is_excluded` / `has_unknown` / `count_by_status`。

**一个反直觉但正确的设计**（代码注释记录）：`required_skills=FAIL` 时**不会**因为「技能全中」而通过——张伟 Python/FastAPI/MySQL 全中仍被淘汰的用例，正是为了固定「硬条件优先于技能重合度」这一业务口径。

---

### 2.6 环节六：语义/软性匹配

| 项 | 内容 |
|---|---|
| 现状 | **没有独立的语义相似度环节**。软性部分由四维打分中的 `skill` 维（技能集合重合度）+ `quality` 维（潜力等级）近似承担 |
| 代码位置 | `app/ai/skills/matching_skill.py :: compute_match_score()` |
| 状态 | 【未实现（真正意义上的语义匹配）】 |

**需求对照**：需求 5.2 要求「项目经验相关性、岗位职责相似度、行业经验、可迁移能力、成果证据可信度」等维度——当前四维中**没有对应实现**，职责相似度仅作为证据文本存在，未参与打分。

**这是与需求差距最大的一处**，必须让接手方明确：现有打分是**规则化四维**，不是语义匹配。

---

### 2.7 环节七：综合评分

| 项 | 内容 |
|---|---|
| 函数 | `app/ai/skills/matching_skill.py :: compute_match_score(talent, position, weights=None)`（纯函数，可单测） |
| 默认权重 | `app/patterns/strategy.py :: MATCH_DEFAULT_WEIGHTS = {skill: 0.4, experience: 0.3, education: 0.2, quality: 0.1}` |
| 权重来源优先级 | 请求 `weights`（本会话新增）> 岗位 `requirements.weights` > `MATCH_DEFAULT_WEIGHTS`，统一经 `_normalize_weights` 归一化到 1.0 |
| 输出 | `{total_score, dimensions{skill,experience,education,quality}, weights, highlights, gaps}` |
| 状态 | 【已实现】 |

**四个维度的计算公式**（代码事实）：

| 维度 | 公式 |
|---|---|
| `skill` | `100 × |T∩P| / |P|`；岗位无技能要求时记 80（不因缺技能扣分） |
| `experience` | 达标 = 100；每差 1 年 −15，下限 0；无要求时 `min(100, 60 + 年限×8)` |
| `education` | 达标 = 100；每差一级 −25，下限 0；无要求时 80 |
| `quality` | 潜力等级 A/B/C/D → 95/80/65/50；缺省 70 |

**总分** = Σ(维度分 × 权重)，保留 2 位小数，落库为 `Numeric(5,2)`。

**需求偏差**：需求要求「评分服务记录规则版本、各维度权重、分项分数及缺失项处理方式」——权重与分项分已落库（`dimension_scores` JSON），**规则版本号没有单独字段**（仅代码内 `MATCH_RULE_CONFIG` 常量）。

---

### 2.8 环节八：结构化匹配证据（MATCH-004，重点）

> 用户特别要求核查此模块，以下为逐点核实结果。

| 项 | 内容 |
|---|---|
| 文件 | `app/services/evidence_builder.py`（**726 行**，本模块最大文件） |
| 主函数 | `build_match_evidence(position, talent, hard_results=None, dimension_scores=None, *, position_id=None, talent_id=None, requirement_version=1, config=None, rule_config=None) -> MatchEvidencePack` |
| 性质 | **纯函数，无 IO**：不查库、不调模型、不读缓存 |
| 输入 | 岗位数据（ORM/dict/`JobProfileParsed`）、人才 ORM、四态判定结果、维度分、画像版本 |
| 输出 | `MatchEvidencePack`：`position_id / talent_id / requirement_version / job_profile_summary / hard_results / dimension_scores / data_completeness / evidences[]` |
| 调用方 | `MatchingPipeline.build_evidence`（`matching_pipeline.py:415`），只对 `scored[:top_k]` 构建，避免为不展示的人白算 |
| 状态 | 【已实现】，单测 19 用例（`tests/unit/test_evidence_builder.py`） |

**内部结构（真实函数清单）**：

| 函数 | 行 | 职责 |
|---|---|---|
| `EvidenceBuildConfig` | 108 | 证据颗粒度配置：claim 长度上限、职责证据最多 5 条、是否含职责/维度/缺失证据 |
| `_EvidenceIdAllocator` | 182 | 证据 ID 分配器，格式 `EVID-{position_id}-{talent_id}-{seq}` |
| `build_job_profile` | 239 | 岗位 → `JobProfileParsed` 视图转换（复用 `matching_rules`，不重写判定） |
| `_evidence_from_hard_result` | 341 | 四态判定结果 → 证据（PASS/FAIL/UNKNOWN/NEEDS_REVIEW 各自措辞不同） |
| `_preferred_skill_evidences` | 394 | 优先技能命中 → 证据 |
| `_responsibility_evidences` | 445 | 岗位职责 vs 人才经历 → 证据（最多 5 条） |
| `_dimension_evidences` | 514 | 维度分 → 「分数怎么来的」证据（`derived` 类） |
| `_missing_field_evidences` | 563 | 档案缺失字段 → `missing` 类证据 |
| `build_match_evidence` | 603 | 主入口：两遍 ID 分配 → 各类证据组装 → 唯一性自检 → 回填 ID |
| `_assert_unique` | 720 | 自检：ID 重复即抛 `ParamError`（重复会把结论挂到错误证据上） |

**三条硬规则（模块 docstring 原文，已确认代码遵守）**：

1. **只搬运、不推测**：`source_value` 必须是档案原始值；字段缺失产出 `type="missing"` 证据，**绝不补默认值、绝不合成技能**。
2. **不引用模型生成的文字**：`talent.portrait_summary` 是 AI 生成内容，被**明确排除**在证据来源之外。
3. **纯函数、无 IO**：数据由 Service 取好传入。

**`evidence_type` 三分类**（MATCH-004 新增，`app/schemas/matching.py:79`）：

| 值 | 含义 |
|---|---|
| `fact` | 档案/岗位原始字段值，未做推断 |
| `derived` | 由事实经确定性代码计算得出（如四态结论、维度分） |
| `missing` | 判断所依据的数据在档案中不存在 |

**ID 分配的两遍策略（易错点，已核实）**：
- 第一遍：登记 `hard_results` 里已有的 `evidence_id`（`_EvidenceIdAllocator.reserve`），非本批次前缀或已被占用的判为无效；
- 第二遍：为无效/空 ID 分配新号，序号接在最大值之后，**绝不与既有冲突**；
- 回填用 `model_copy(update=...)`，**不修改入参对象**。

**上游依赖**：`matching_rules.judge_hard_conditions`（判定）、`matching_rules.compute_data_completeness`（完整度）、`matching_skill.normalize_skills`（技能归一化）——均复用不重写。

**已知边界（诚实记录）**：
- `build_match_evidence` 需要 `position_id` 与 `talent_id`，取不到直接抛 `ParamError`（无 ID 无法生成可校验的 ID）。
- 职责证据上限 5 条，超出部分丢弃——**长 JD 的职责覆盖不完整**，属证据颗粒度取舍，不是 bug。
- 证据里不含「项目经历」维度证据（`talents` 表无结构化项目字段），需求中的「项目经验相关性」无法举证。

---

### 2.9 环节九：推荐理由（解释生成）

| 项 | 内容 |
|---|---|
| 节点 | `MatchingPipeline.explain(state)`（`matching_pipeline.py:450`） |
| 服务 | `app/services/explanation_service.py :: ExplanationService.generate(pack)` |
| 通道顺序 | Dify（`_via_dify`）→ LLM（`_via_llm`）→ 规则（`_via_rule`），任一成功即返回 |
| 并发 | 节点内 `_one(talent_id, pack)` 并发限流（`asyncio.Semaphore`），单条失败不影响整批 |
| 输入 | `MatchEvidencePack` |
| 输出 | `ExplanationOutcome(result: MatchExplanation, channel: "dify"|"llm_fallback"|"rule_fallback", errors[])` |
| 状态 | 【降级实现】当前 `channel="rule_fallback"` 或 `llm_fallback`（Ollama 已接通时可走 llm） |

**`MatchExplanation` 结构**：`summary / highlights[] / gaps[] / risks[] / interview_suggestions[] / pending_verification[] / rejected_claims[] / source / prompt_version`，其中 claim 为 `{text, evidence_ids[min 1], claim_type: fact|inference}`。

---

### 2.10 环节十：五层校验（防编造）

| 项 | 内容 |
|---|---|
| 文件 | `app/services/evidence_verifier.py`（**715 行**） |
| 主函数 | `verify_explanation(explanation, pack)`；前置 `audit_pack(pack)` |
| 性质 | 确定性校验，**不调大模型**，不让 LLM 自评 |
| 状态 | 【已实现】，单测 22 用例 |

**校验分层（顺序不可颠倒）**：

| 层 | 名称 | 拦什么 |
|---|---|---|
| A | 证据包自审 `audit_pack` | 弹药本身不合法（ID 格式错、越界字段），摘出可用索引 |
| B | 引用校验 | `evidence_id` 不存在 / 不属于当前 (岗位, 人才) |
| C | 语义边界校验 | `missing` 证据用于下能力结论；能力术语越界（白名单+黑名单） |
| D | 事实一致性校验 | 年限数值高于档案；正面结论与 FAIL/UNKNOWN 冲突 |

**三档输出**：`pass`（保留）/ `reject`（移除 + 写入 `rejected_claims`，含 `reason` 五枚举）/ `pending`（移除 + 写入 `pending_verification`）。

**`rejected_claims.reason` 枚举**：`no_evidence` / `evidence_not_found` / `term_out_of_scope` / `conflict_with_hard_result` / `missing_evidence_misuse`。

**必须如实告知的局限**（模块 docstring 原文）：能力术语检查是「黑名单+白名单」，**不是语义等价证明**。它拦得住「高并发」「架构设计」这类高危术语，**拦不住换个说法的委婉表述**（如把「写过定时任务」说成「具备复杂调度设计能力」）。真正的语义校验需领域本体或人工抽检，属后续迭代。

---

### 2.11 环节十一：能力差距分析

| 项 | 内容 |
|---|---|
| 产出位置 | ① `compute_match_score().gaps` = 岗位技能集 − 人才技能集（前 10 条）；② 解释的 `gaps[]`（带证据的差距结论）；③ `hard_results` 中 FAIL/UNKNOWN 项 |
| 状态 | 【已实现（技能差距）】/ 【部分实现（解释性差距）】 |

**缺口**：需求要求「差距要能追溯到具体项目/职责」，当前只到技能集合差集层面。

---

### 2.12 环节十二：组装与匹配报告

| 项 | 内容 |
|---|---|
| 节点 | `MatchingPipeline.assemble(state)`（`matching_pipeline.py:482`） |
| 服务 | `MatchingService.run()` → `run_matching(pipeline, state)`（LangGraph 优先，ImportError 时顺序执行兜底） → `_persist()` |
| 输出 | `MatchRunResponse`：`batch_id / position_id / position_title / requirement_version / recall_mode / weights_used / matched_at / stats / items[] / excluded[]` |
| 落库 | `matching_records`（唯一键 `batch_id + position_id + talent_id`） |
| 状态 | 【已实现】 |

**落库字段（含 MATCH-009 新增 5 列）**：`score / dimension_scores / highlights / gaps / suggestion / strategy / model_name / matched_by / matched_at` + `requirement_version / recall_mode / hard_results / explanation / stats`。

**读接口（4 个）**：`GET /matchings`（分页+过滤）、`GET /matchings/positions/{id}`（最新）、`GET /matchings/talents/{id}`（历史）、`POST /matchings/explain`（单对解释，不落库）。

---

### 2.13 附加链路一：双向匹配（人才找岗位）

| 项 | 内容 |
|---|---|
| 入口 | `POST /api/v1/matchings/reverse` |
| 服务 | `MatchingService.run_reverse(payload)`（`matching_service.py:192` 附近） |
| 流水线 | `MatchingPipeline.run_reverse(state)`（`matching_pipeline.py:556`） |
| 策略 | 对「有已确认画像的开放岗位」逐个复用 `filter_score` 的打分内核（**判定方向不变**：仍是「人才是否满足岗位要求」），反转的是召回方向与输出形态 |
| 输出 | `MatchReverseResponse`：`talent_id / talent_name / positions_evaluated / skipped[] / recall_mode / weights_used / items[]（适合的岗位）/ excluded[]（硬条件 FAIL 的岗位及原因）` |
| 落库 | **不落库**（只读试算，路由注释已声明） |
| 状态 | 【已实现（本次会话新增）】 |

**本次会话新增时的口径**：`with_explanation` 默认 `false`——一次反查可能评估几十个岗位，批量解释成本高；关掉的是 Dify/LLM 通道，规则解释不受影响。

---

### 2.14 附加链路二：岗位储备预警

| 项 | 内容 |
|---|---|
| 入口 | `POST /matchings/warnings/scan`、`GET /matchings/warnings`、`POST /matchings/warnings/{id}/resolve` |
| 模型 | `app/models/reserve_warning.py :: ReserveWarning`（表 `reserve_warnings`） |
| 服务 | `app/services/reserve_warning_service.py`（321 行） |
| 三类预警 | 零产出（岗位长期无匹配结果）、储备不足（候选人数 < 阈值）、长期空缺（开放天数 > 阈值） |
| 状态 | 【已实现（本次会话新增）】/ 表结构【待审批落库】（DDL 见 `docs/sql/2026-11-04_reserve_warnings.sql`） |

**注意**：需求模块五要求预警基于「编制 / 在岗 / 招聘计划 / 人才储备 / 继任关系」五类数据源，当前实现只用了「岗位 + 匹配记录 + 人才状态」三类，**编制与继任关系数据模型不存在**，属简化版。

---

## 3. 数据流（端到端一次跑批的真实时序）

```
HR → POST /matchings/run {position_id, talent_ids?, top_k, min_score, strategy, with_explanation, weights?}
  │
  ├─ MatchingService.run()
  │   ├─ 校验岗位 & 画像已确认（require_confirmed，否则 400）
  │   ├─ 生成 batch_id（uuid hex）
  │   └─ 组装 MatchingState（入参 + weights + errors=[]）
  │
  ├─ run_matching()  ← LangGraph 编译或顺序执行
  │   ├─ load_context   读岗位 + 画像（指定版本或最新）→ position_view + weights
  │   ├─ recall         向量(SQL 降级) → candidates[] + recall_mode
  │   ├─ filter_score   四态判定 → scored[] / excluded[] / stats
  │   ├─ build_evidence 每人一份 MatchEvidencePack（仅 top_k）
  │   ├─ explain        Dify→LLM→规则 + verify_explanation（并发限流）
  │   └─ assemble       合并 items[]（rank/score/dimensions/hard_results/explanation）
  │
  ├─ MatchingService._persist()  逐条 INSERT matching_records（含 5 个追溯列）
  │
  └─ MatchRunResponse → 前端展示
```

**中途失败的行为**：单个候选人的解释失败 → 该人仍返回，`explanation_source="rule_fallback"`，`errors` 记录原因，**不中断整批**；岗位画像未确认 / 岗位不存在 → 整批 400/404。

---

## 4. MatchingState 字段字典（LangGraph 状态）

| 分组 | 字段 | 说明 |
|---|---|---|
| 入参 | `position_id` / `talent_ids` / `top_k` / `min_score` / `strategy` / `with_explanation` / `requirement_version` / `weights` / `batch_id` / `matched_by` | `total=False`，节点只返回增量 |
| 中间 | `position_title` / `profile` / `profile_version` / `position_view` / `recall_mode` / `candidates` / `scored` / `excluded` / `stats` / `packs` / `explanations` | 节点间传递 |
| 输出 | `items` / `excluded` / `errors` | `assemble` 汇总 |

> **没有统一的 AgentState**：全项目只有 `MatchingState`。规划中的 `AgentState` / `WorkflowState`（见 `docs/plan/02-Agent组.md` D1 任务）**不存在**。

---

## 5. 接口清单（本 Agent 真实可用）

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | `/positions/{id}/jd/parse` | JD 解析（草稿） | 已实现（Dify 降级） |
| POST | `/positions/{id}/profile/confirm` | 画像确认（新版本） | 已实现 |
| GET | `/positions/{id}/profile` | 画像查询 | 已实现 |
| POST | `/matchings/run` | 执行匹配 | 已实现（召回/解释降级） |
| POST | `/matchings/reverse` | 人才找岗位 | 已实现（本次新增，不落库） |
| GET | `/matchings` | 记录分页 + 过滤 | 已实现 |
| GET | `/matchings/positions/{id}` | 岗位最新结果 | 已实现 |
| GET | `/matchings/talents/{id}` | 人才历史 | 已实现 |
| POST | `/matchings/explain` | 单对解释 | 已实现 |
| POST | `/matchings/warnings/scan` | 预警扫描 | 已实现（本次新增） |
| GET | `/matchings/warnings` | 预警列表 | 已实现（本次新增） |
| POST | `/matchings/warnings/{id}/resolve` | 解决预警 | 已实现（本次新增） |
| POST | `/agents/tasks` | 执行指定 Agent 任务 | **不存在**（`agent_router.py` 只有 `GET /agents` 占位，返回 501） |

> 最后一行是重点：规划文档（`docs/plan/02-Agent组.md` D10）要求「`POST /agents/tasks` 可用」，当前**未实现**。`matching_agent` 类已实现并注册进 `agent_registry`，但没有任何 HTTP 入口能触发它。

---

## 6. 与需求文档（V1.4）的偏差对照

| 需求条款 | 要求 | 代码现状 | 判定 |
|---|---|---|---|
| 模块一 字段置信度 | 保留解析置信度 | `JobProfileParsed` 无 confidence 字段 | 未实现 |
| 模块二 人才接入 | 复用 Resume Agent Schema/API | Resume Agent 是 TODO 骨架 | 未实现 |
| 5.2 软性维度 | 6 类软性能力维度 | 只有 4 维规则打分，无语义/项目/行业维度 | 未实现 |
| 5.2 权重可配 | 权重支持配置 | 岗位配置 + 请求覆盖（本次新增） | 已实现 |
| 5.3 评分口径 | 记录规则版本 | 权重/分项已落库，无规则版本号字段 | 部分实现 |
| 06 双向匹配 | 复用统一引擎、不占位 | `run_reverse` 复用打分内核 | 已实现 |
| 06 双向差异 | 输出适配岗位及差距 | reverse 返回 gaps/excluded 原因 | 已实现 |
| 07 储备预警 | 基于编制/在岗/招聘/储备/继任 | 只用了岗位+匹配记录+人才状态 | 部分实现 |
| 04 任务状态机 | CREATED→PARSING→…→COMPLETED | **无任务状态机**，同步请求-响应 | 未实现 |
| 12 权限审计 | 匹配需权限 + 审计 | 权限已挂（`require_permission`），审计日志未实现 | 部分实现 |
| 13 验收 | 真实人才档案 + 向量链路可用 | 人才档案接口 501，向量未启用 | 未达成 |

---

## 7. 待核实事项（无法从代码确认）

1. `talents` 表字段与 Resume Agent 规划的 `TalentProfile` 是否对齐——需简历解析负责人确认。
2. `positions` 表由谁写入、有无建岗接口规划——当前只能靠 seed 或手工 SQL。
3. 需求「一票否决项由业务方确认」——`HardRuleConfig` 的默认口径（`DEFAULT_HARD_RULE_CONFIG`）是否已获业务确认，代码中无记录。
4. 向量维度定为 1024（`LLM_EMBED_DIM` / bge-m3 实测一致），但 `talent_vectors` 集合尚未创建，索引类型（IVF_FLAT 或 HNSW）未定。
5. `matching_records` 分区键为 `matched_at`，测试/生产库是否已建分区——DDL 只在开发库执行过。
