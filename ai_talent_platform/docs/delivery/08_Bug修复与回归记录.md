# 交付物 9｜Bug 修复与回归记录

> 原则：先定位根因 → 评估影响范围 → 修复 → 补/改用例 → 重跑该模块 → 全量回归。
> 所有代码改动均带 `*kwq*` 注释与中文原因说明；未提交 Git（遵守「不擅自提交/合并/推送」）。

---

## 一、修复总览

| 编号 | 级别 | 一句话 | 状态 |
| --- | --- | --- | --- |
| P0-1 | P0 | 三个 Repository 是骨架（未声明 `model`），任何调用都抛 `NotImplementedError` | 已修 |
| P0-2 | P0 | `MatchingService` 只有 `__init__`、工作流是 TODO、路由是 501 占位 —— 匹配功能整体不可用 | 已修 |
| P1-1 | P1 | `top_k` 失效：`top_k=1` 仍返回 33 条 | 已修 |
| P1-2 | P1 | 显式传 `talent_ids` 时，离职人员仍参与匹配 | 已修 |
| P1-3 | P1 | 档案全空的人被判「年限不足」淘汰 | 已修 |
| P1-4 | P1 | `missing` 证据里带能力术语（等于用「不知道」证明「会」） | 已修 |
| P1-5 | P1 | `java` 命中 `javascript`，产出假证据 | 已修 |
| P1-6 | P1 | JD 解析把薪资串、职责描述词当技能；「优先」整行降级 | 已修 |
| P1-7 | P1 | 淘汰明细不落库，刷新后「谁被淘汰、为什么」全丢 | 已修（MATCH-014） |
| P2-1 | P2 | 「MySQL 数据库设计」被拆成两个硬条件，导致 4/5 候选人被误淘汰 | 已修（MATCH-015） |
| P2-2 | P2 | 匹配记录列表无人才姓名，无法区分记录 | 已修（MATCH-016） |
| P3-1 | P3 | 启动日志硬编码「已实现 3 个模块」 | 已修（MATCH-010） |
| P2-3 | P2 | 全淘汰批次不落库（缺批次表） | **未修**（需 DDL，登记为待办） |
| P2-4 | P2 | 公共层 3 条既有测试失败（非本次引入） | **未修**（属他人模块，已基线复现） |

---

## 二、P0 级

### P0-1 三个 Repository 为骨架，匹配链路无法读库

- **现象**：`TalentRepository/PositionRepository/MatchingRepository` 只有类定义和注释，`model` 未声明；基类泛型 CRUD 一旦调用即 `NotImplementedError`。
- **根因**：模块骨架生成后未实装，`git log` 里也没有后续提交。
- **影响**：召回、画像版本、落库全部不可用；即便 Service 写完也跑不起来。
- **修复**：实装三个仓库（`app/repositories/*.py`）：声明 `model`、字段白名单（`filterable/searchable/sortable`）、`get_by_ids`、`list_candidates`、`latest_requirement`、`next_requirement_version`（用 `func.max` 而非内存计数）、`save_batch`（走 `create_many`，唯一键冲突转 `DuplicateError`）、`latest_batch_id`（按 `matched_at DESC, id DESC`，不用 `max(id)`）、`history_by_talent`。
- **验证**：集成测试 34 例全部真实读库通过；`/health` 显示 MySQL up，启动日志「表结构初始化完成，共 25 张表」。

### P0-2 匹配功能整体不存在（Service 空壳 / 工作流 TODO / 路由 501）

- **现象**：`matching_service.py` 只有 `__init__`；`matching_workflow.py` 全是 `# TODO`；`/api/v1/matchings` 与 `/api/v1/positions` 返回 501；`/system/info` 的 `implemented_modules` 只有 3 个模块。
- **根因**：上一轮只交付了「方案与骨架」，未落地实现（这正是任务书要求「不得仅凭开发报告判断」的原因）。
- **修复**：
  - 新增 `matching_pipeline.py`（LangGraph 6 节点）、`matching_rules.py`、`evidence_builder.py`、`evidence_verifier.py`、`explanation_service.py`、`jd_parser.py`、`position_profile_service.py`；
  - 实装 `MatchingService`（run/get_latest/page_list/history/explain/_persist）；
  - 重写 `matching_router.py`、`position_router.py`，并在 `router.py` 把两个前缀从占位切到真实路由；
  - `matching_workflow.py` 用 `StateGraph` 编排，langgraph 不可用时降级为顺序执行。
- **验证**：`/system/info` → `implemented_modules=['auth','users','organizations','positions','matchings']`；OpenAPI 41 条路径含 8 条匹配链路接口；端到端脚本 22 项断言 PASS。

---

## 三、P1 级（业务正确性）

### P1-1 `top_k` 失效

- **现象**：`top_k=1` 的请求返回了 33 条结果。
- **根因**：截断只做在 `build_evidence` 节点（为解释准备数据），`assemble` 节点仍遍历全量 `scored`。
- **影响**：前端分页/首屏性能失真，且「前 N 名」语义不成立。
- **修复**：在 `filter_score` 节点统一 `filtered = filtered[:top_k]`，让下游所有节点只看截断后的集合。
- **回归**：`test_top_k_and_min_score_only_affect_output`（集成）+ 端到端 `top_k=5` 返回 3 条。

### P1-2 离职人员仍参与匹配

- **现象**：显式传 `talent_ids` 时，`status=left` 的人出现在结果里。
- **根因**：状态过滤只写在 `elif statuses:` 分支（全库召回才生效），显式 ID 分支跳过了过滤。
- **影响**：可能把已离职人员推荐给岗位，属业务事故级问题。
- **修复**：`list_candidates` 中状态过滤独立生效，不再依赖召回方式。
- **回归**：`test_left_status_talent_is_not_matched`。

### P1-3 档案全空的人被「年限不足」淘汰

- **现象**：王强（`work_years=0`、学历/技能为空）被判 `min_work_years=FAIL` 直接淘汰。
- **根因**：`talents.work_years` 是 `NOT NULL DEFAULT 0`，无法区分「应届生 0 年」与「档案没填」；默认规则把 0 当事实。
- **影响**：违反「缺失不得当作不满足」，会把资料不全的候选人误杀。
- **修复**：匹配链路统一 `MATCH_RULE_CONFIG = HardRuleConfig(zero_work_years_as_review=True)` —— 0 年转 NEEDS_REVIEW（人工确认是否应届），并在完整度里体现（王强 33%）。
- **回归**：`TestMissingFieldsAreUnknown::test_missing_work_years_is_unknown`、端到端「档案缺失者（王强）未被硬条件默认淘汰」PASS。

### P1-4 `missing` 证据带能力术语

- **现象**：审计日志告警「missing 证据带有能力术语」——UNKNOWN 学历时把「本科」写进了 `capacity_terms`。
- **根因**：证据构建器对所有证据统一提取能力术语，未区分 `evidence_type`。
- **影响**：等于允许「用缺失证明具备」，是解释可信度的致命漏洞。
- **修复**：`capacity_terms=[] if evidence_type == "missing" else _capacity_terms(...)`。
- **回归**：`test_missing_fields_do_not_fabricate_evidence`、`test_missing_evidence_cannot_prove_ability`。

### P1-5 `java` 命中 `javascript`

- **现象**：候选人技能 `javascript` 被判为满足岗位要求的 `Java`，产出假证据。
- **根因**：技能匹配用子串包含，英文短词被长词吞掉。
- **影响**：技术栈判断错误，且会生成一条「看起来有据」的假证据。
- **修复**：英文技能改为**词元精确相等**（按非字母数字切分后比较），中文保留子串匹配（中文无词边界）。
- **回归**：`test_java_must_not_match_javascript`、`test_exact_english_skill_still_matches`（防过度收紧）。

### P1-6 JD 解析噪声

- **现象（修复前真实输出）**：`required=['性能优化','Python','FastAPI','MySQL','k-35k']`；出现 `k-35k` 这种假技能；含「优先」的整行技能被一起降级为优先。
- **根因**：① 全行取 ASCII token，未剔薪资；② 按行判断「优先」，未按分句。
- **影响**：岗位画像失真 → 后续所有匹配结论都建立错前提。
- **修复**：只在 `_SKILL_LINE_KEYWORDS` 命中的行取技能；剔除薪资串；过滤纯数字/版本号；按分句（`、，,；;`）判断归属。
- **回归（修复后真实输出）**：`必备=['Python','FastAPI','MySQL'] 优先=['Redis']`；`test_noise_tokens_are_not_skills`、`test_preferred_half_sentence_does_not_downgrade_required_skills`。

### P1-7 淘汰明细不落库（MATCH-014）

- **现象**：`GET /matchings/positions/1` 返回的 `excluded` 恒为 `[]`，与刚执行完的结果不一致。
- **根因**：`_persist` 只写 `items`，淘汰信息仅存在于当次响应；`get_latest` 硬编码 `excluded=[]`。
- **影响**：页面刷新后无法回答「他为什么没出现」，与「结果可追溯」要求冲突。
- **修复**：淘汰明细写入批次 `stats.excluded`（复用既有 JSON 列，**不新增列、不动表结构**）；`get_latest` 反序列化为 `ExcludedItem`，单条脏数据跳过不 500。
- **回归**：新增 `test_latest_restores_excluded_details`；端到端新增「回读淘汰名单/原因文本一致」两项断言，均 PASS。

---

## 四、P2 / P3 级

### P2-1 「MySQL 数据库设计」被拆成两个硬条件（MATCH-015）

- **现象**：JD 写「熟悉 MySQL 数据库设计与优化」，解析出 `required=['Python','FastAPI','MySQL','数据库设计']`；结果 **5 名候选人里 4 名被判 `required_skills=FAIL`**，连 Python/FastAPI/MySQL 全中的张伟也被淘汰。
- **根因**：`_CN_SKILL_TERMS` 里的抽象能力短语（数据库设计/性能优化…）与具体技术词同句时被各自计为一个硬门槛；人才档案的 `skill_tags` 几乎不会出现字面「数据库设计」→ 必然 FAIL。
- **影响**：过度淘汰（误杀），比「漏招」更伤业务信任。
- **修复**：新增 `_CAPABILITY_PHRASES`；**同句已有具体技术词时**，能力短语不再单独作为技能（原文仍保留在 `required_conditions` 供 HR 复核）；同句只有能力短语时照常保留。
- **回归**：`test_capability_phrase_not_promoted_when_clause_has_tech`、`test_capability_phrase_kept_when_standalone`；端到端复跑：`excluded_by_hard` 从 4 降为 2，张伟 99.5 / 陈晨 98.0 回到结果中。

> 同批还修了两个「条件噪声」：薪资行不再进「待复核条件」；已抽出技能的句子不再重复登记为模糊条件（否则人人被打上 NEEDS_REVIEW，复核清单失去信噪比）。用例：`test_salary_line_is_not_a_condition`、`test_skill_covered_line_is_not_a_condition`、`test_skill_only_jd_has_no_missing_condition`。

### P2-2 列表无人才姓名（MATCH-016）

- **现象**：`GET /matchings` 首条 `talent_name` 为空，HR 只能看到批次号与分数。
- **根因**：`MatchHistoryItem` 没有人才字段，`page_list` 只解引用岗位标题。
- **修复**：Schema 增加 `talent_id`/`talent_name`（可选字段，向后兼容）；`page_list` 与 `get_history_by_talent` 批量补名。
- **回归**：`test_list_matchings_page_structure` 增加「姓名非空」断言；端到端输出 `talent=王强`。

### P3-1 启动日志硬编码（MATCH-010）

- **现象**：新增 positions/matchings 后，启动日志仍打印「已实现 3 个模块」，排查时容易误判路由未切换。
- **修复**：改为与 `/system/info` 同源的动态数量。仅文案，不影响路由注册。

### P2-3 全淘汰批次不落库（**未修，需 DDL**）

- **现象**：全员被硬条件淘汰（或 `min_score` 过滤后无人入围）时，`_persist` 无行可写 → 该 `batch_id` 查不到，回读 404。
- **根因**：`matching_records` 以 `talent_id` 为主维度，没有独立的批次表；`score`、`talent_id` 均非空。
- **处理**：按「涉及数据库结构变更必须先汇报」的约定，**本次不擅自加表**；在代码中显式 `logger.warning` 记录该情形，并用用例 `test_all_excluded_batch_is_not_persisted` 把行为固定下来；DDL 建议写入验收报告（P2-1）。
- **下一步**：DBA 审批后新增 `matching_batches(batch_id PK, position_id, requirement_version, stats JSON, excluded JSON, matched_at)`。

### P2-4 公共层 3 条既有失败（**未修，非本次引入**）

- **现象**：`tests/test_common_layer.py` 的 `test_health_ready`、`test_register_login_and_me`、`test_super_admin_full_flow` 失败。
- **定位**：`git worktree add ../baseline-check HEAD`（基线 `f29ac08`）在同环境复跑 → **同样 3 failed**，确认与本次改动无关；worktree 已 `remove --force` 清理。
- **处理**：属公共层（他人模块），按规范不擅自修改，只登记。

---

## 五、测试基础设施问题（不是业务 Bug，但必须记录）

| 问题 | 根因 | 处理 |
| --- | --- | --- |
| `ImportError: cannot import name 'StrEnum'` | `py -3.10` 建的环境，代码用 3.11+ 的 `StrEnum` | 换 Python 3.13 环境（`D:/0904开班/.venv-match313`） |
| `rm -rf .venv` 被安全策略拦截 | 删除 7000+ 文件需二次确认 | 改为在仓库外新建环境，不删既有目录 |
| `MissingGreenlet`（`user.roles.append`） | async ORM 懒加载在同步上下文触发 | `flush()` + `refresh(attribute_names=["roles"])` 后整体赋值（与 `UserRepository.set_roles` 同法） |
| `RuntimeError: Event loop is closed` | 公共 conftest 的引擎跨事件循环复用 | 集成测试自建 `NullPool` 独立引擎（文件头注明原因与影响范围） |
| 单元测试被公共 conftest 拖入 MySQL 依赖 | `pytest.ini` 的 `--confcutdir` 未限定 | 运行单元测试加 `--confcutdir=tests/unit` |
| `scripts/create_admin.py` 报「角色不存在」 | 首次建库未初始化 `RoleEnum` | 脚本内先补齐全部内置角色 |
| 测试预期写错 2 处 | 断言「pending 含学历」实际写在 `detail`；改画像时 Redis 同时出现在必备与优先触发冲突拦截 | **改用例不改业务**：断言改为 `detail` 含「缺失类证据」；测试里先清空 `preferred_skills`，并把「冲突被拦截」作为校验生效的证据保留在注释中 |

---

## 六、回归结果（最终）

```bash
# 单元 + 集成
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" -m pytest tests/unit tests/integration -o addopts="" -q
# 178 passed, 1 warning in 5.42s

# 全仓（含其他模块）
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" -m pytest tests -o addopts="" -q
# 260 passed, 3 failed（均为既有失败，已基线复现）

# 端到端（真实进程 + 真实库）
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" scripts/e2e_matching_check.py
# 全部检查通过：JD 解析 → 画像确认 → 匹配 → 落库 → 回读 → 解释 全链路真实跑通。
```

| 回归项 | 修复前 | 修复后 |
| --- | --- | --- |
| 匹配相关用例 | 0（不存在） | 178 passed |
| 端到端断言 | — | 22/22 PASS |
| `excluded_by_hard`（真实 5 人） | 4（误杀） | 2（正确淘汰） |
| 淘汰原因回读 | 空数组 | 与执行结果逐字一致 |
| 列表姓名 | `null` | 正确显示 |

---

## 七、第二轮接口巡检新增修复（MATCH-017 ~ MATCH-021）

> 起因：交付后按「先跑真实接口再下结论」重新巡检一遍，用 `scripts/diagnose_api.py` 打真实 HTTP（22 项检查）。
> 结论：主链路无 500、无契约破坏；但发现 5 个「不报错却给错数据」的问题，全部已修并补用例。

| 编号 | 级别 | 现象（真实复现） | 根因 | 修复 |
| --- | --- | --- | --- | --- |
| MATCH-017 | P2 | 结果条目没有名次，前端只能用数组下标当 `#1/#2` | `MatchItem` 无 `rank` 字段 | 新增 `rank`，执行与回读均由后端生成（回读按分数序 enumerate） |
| MATCH-018 | P2 | 条目只有逐项四态，没有整体结论，各端自己聚合易打架 | `MatchItem` 无 `decision` 字段 | 新增 `decision`，按 `FAIL > NEEDS_REVIEW > UNKNOWN > PASS` 统一聚合；回读复用已落库的 `hard_results`，不重算 |
| MATCH-019 | P1 | `strategy=soft` 时 `stats.unknown=0 / needs_review=0`，同一批人切到 hybrid 却是 1/1 —— 数字前后矛盾 | `SoftMatchStrategy` 只打分不判定，`hard_results` 为空 | soft 也调用 `judge_hard_conditions`，产出四态明细但**不淘汰**（`excluded` 仍为 0）；`stats` 改为直接按 `hard_results` 统计，不再依赖策略布尔标记 |
| MATCH-020 | P2 | 指定 `requirement_version=999` 返回 400「该岗位尚未确认岗位画像」，但该岗位明明有 v8 且已确认 | `require_confirmed` 把「版本不存在」和「画像未确认」合并成同一个分支 | 指定版本查不到时抛 `NotFoundError`（404）并带上版本号；只有「无任何画像」才提示未确认 |
| MATCH-021 | P2 | `GET /matchings?position_id=1` 与不加参数返回同一批数据（`total` 相同） | Router 未声明 `Query` 参数，FastAPI 静默丢弃未知 query string | 新增 `position_id` / `talent_id` / `min_score` 三个过滤参数，走仓库白名单 + `score >=` 条件 |

### 修复验证

```bash
# 过滤真实生效（修复前：43 / 43 / 43）
GET /matchings?talent_id=1        -> total=13，且每条 talent_id 均为 1
GET /matchings?position_id=987654 -> total=0（修复前返回全表 43）
GET /matchings?min_score=99.99    -> 条数 <= 全量

# 版本不存在（修复前：400 未确认）
POST /matchings/run {"requirement_version": 999}
-> 404 岗位画像版本不存在（position_id=1, version=999）

# soft 策略统计（修复前：unknown=0）
POST /matchings/run {"strategy":"soft"}
-> items=5 unknown=1 needs_review=1 excluded=0
```

### 回归结果

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| `tests/unit + tests/integration` | 178 passed | **183 passed**（新增 5 例） |
| 接口巡检 `scripts/diagnose_api.py` | 20/21（3 项问题） | **22/22，未发现问题** |
| 端到端 `scripts/e2e_matching_check.py` | 22/22 | 22/22 |

---

## 八、未做的部分（明确声明）

1. **未提交 Git**：所有改动留在工作区（分支 `lz_agent`），`git diff` 完整可查；按规范不擅自 commit/merge/push。
2. **未改公共层**：`test_common_layer` 的 3 条既有失败、`app/core/*` 的公共行为均未触碰。
3. **未动他人的未提交改动**：改动前先 `git status` 确认了 13 个修改文件 + 7 个新增文件的边界，未覆盖 `app/models/talent.py`、`app/routers/talent_router.py` 等他人模块。
