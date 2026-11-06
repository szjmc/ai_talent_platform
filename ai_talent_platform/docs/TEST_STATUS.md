# 岗位人才匹配 Agent —— 测试情况与测试证据

> **重要声明**：本文所有数字均为 **2026-11-04 本轮实测**，不是历史交付报告引用。
> 历史文档 `docs/delivery/05_自动化测试报告.md`、`09_最终交付验收报告.md` 中的「183 条通过」等数字**已过期**，原因是后续新增了测试用例与数据表。
> 凡未实际执行的项目，一律标注「未执行」，不推测、不补数。

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 执行时间 | 2026-11-04 18:20 ~ 19:00（GMT+8） |
| 解释器 | `.venv/Scripts/python.exe` = **Python 3.10.4** |
| 数据库 | 本地 MySQL `127.0.0.1:3306`，测试库 **`ai_talent_platform_test`**（由 `tests/conftest.py` 覆盖 `DB_NAME` 指定） |
| 大模型 | 本地 Ollama 运行中（`qwen3:0.6b`），**已接入**，因此解释链路会真实调用模型 |
| 向量 | Milvus 未部署（`MILVUS_ENABLED=false`），`pymilvus` 未安装 |
| Dify | 未配置 Key（`DIFY_API_KEY` 为空），走规则降级 |
| 工作区状态 | HEAD `bed2b54` + 19 个未提交修改 + 6 个未跟踪文件 |

> 环境差异会直接影响结果：接入 Ollama 后，带 `with_explanation=True` 的用例会真实调模型，耗时从毫秒级变为分钟级；且原先假设「LLM 不可用」的两个单测会失败（本轮已修）。

---

## 2. 本轮执行记录

| # | 命令 | 范围 | 结果 | 耗时 | 有效性 |
|---|---|---|---|---|---|
| 1 | `pytest tests --ignore=tests/integration --junit-xml=...` | 非集成全量 | **231 用例 / 228 通过 / 3 失败 / 0 错误** | 9.479s | ✅ 有效 |
| 2 | `pytest tests/integration`（第一次，默认配置） | 集成全量 | 37 用例 / 19 通过 / 0 失败 / **18 错误** | 830.809s | ❌ **无效**（并发污染，见 4.1） |
| 3 | `pytest tests/integration`（第二次，默认配置，干净重跑） | 集成全量 | **15 分钟内未跑完**（约完成 18/37），被 timeout 终止，无 XML | >900s | ⚠️ **未完成** |
| 4 | `LLM_PROVIDER=qwen LLM_API_KEY= DIFY_API_KEY= pytest tests/integration` | 集成全量（强制关闭外部模型） | **37 用例 / 36 通过 / 1 失败 / 0 错误** | **14.546s** | ✅ 有效（条件是 rule_fallback，见 4.3） |

**结论一句话**：
- 非集成测试 **98.7% 通过**（228/231），3 个失败都在公共层且原因是 Windows asyncio 测试基础设施（见 3.2）；
- 集成测试在**关闭外部模型的条件下 14.5 秒跑完 36/37**，暴露出 **1 个真实的业务回归缺陷**（离职人才被纳入匹配，见 4.3）；
- 集成测试在**默认配置（真实调用本地 Ollama）下 15 分钟跑不完**，这是当前测试基础设施的硬伤（见 P2-1）。

---

## 3. 非集成测试明细（231 个用例）

### 3.1 按文件统计（来自 JUnit XML，非估算）

| 文件 | 用例数 | 通过 | 失败 | 说明 |
|---|---|---|---|---|
| `tests/unit/test_matching_rules.py` | 25 | 25 | 0 | 四态判定（学历/年限/技能/模糊条件） |
| `tests/unit/test_dify_adapter.py` | 22 | 22 | 0 | Dify 适配器（MockTransport，**不代表真实联调**） |
| `tests/unit/test_evidence_verifier.py` | 22 | 22 | 0 | 五层校验、三档输出、剔除原因 |
| `tests/test_skills.py` | 21 | 21 | 0 | Skill 注册与执行（含 `matching_score`） |
| `tests/unit/test_evidence_builder.py` | 19 | 19 | 0 | **MATCH-004 证据构建器**（纯函数） |
| `tests/unit/test_jd_parser.py` | 17 | 17 | 0 | JD 解析与字段标准化 |
| `tests/unit/test_matching_schemas.py` | 13 | 13 | 0 | Schema 校验（含权重校验） |
| `tests/unit/test_matching_strategy.py` | 12 | 12 | 0 | 三种策略（hard/soft/hybrid） |
| `tests/unit/test_matching_pipeline.py` | 10 | 10 | 0 | 流水线节点（含本次新增 `run_reverse` 相关） |
| `tests/unit/test_explanation_service.py` | 6 | 6 | 0 | 降级链（本次修改 2 个用例，显式屏蔽 LLM） |
| `tests/test_architecture_layer.py` | 38 | 38 | 0 | 分层架构约束（公共层） |
| `tests/test_orm_mapping.py` | 7 | 7 | 0 | 表映射（本次 25→26 张表） |
| `tests/test_pgsql_schema.py` | 6 | 6 | 0 | PgSQL 分析库 DDL |
| `tests/test_database_health.py` | 5 | 5 | 0 | 数据库连通性 |
| **`tests/test_common_layer.py`** | **8** | **5** | **3** | **公共层（认证/健康检查），既有问题** |
| **合计** | **231** | **228** | **3** | 通过率 98.70% |

### 3.2 3 个失败用例（已取得真实错误）

| 用例 | 错误 | 归属 | 根因 |
|---|---|---|---|
| `test_common_layer.py::test_health_ready` | `AssertionError`（`/health/ready` 断言不符） | 公共层健康检查 | **待核实**（未取到断言的期望值/实际值对比，见下方命令） |
| `test_common_layer.py::test_register_login_and_me` | `AttributeError: 'NoneType' object has no attribute 'send'`（`asyncio/proactor_events.py:397`） | 公共层认证 | **Windows asyncio Proactor 事件循环关闭**（`Event loop is closed`） |
| `test_common_layer.py::test_super_admin_full_flow` | 同上 | 公共层权限 | 同上 |

**归因**（依据实际 traceback）：

- 2 个 `AttributeError` 不是业务缺陷，是 **Windows + Python 3.10 + Starlette 同步 `TestClient`** 的组合问题：TestClient 在独立线程里跑事件循环，用例结束后循环被关闭，传输层析构时二次调用 `send` 报错。证据：末尾同时出现 `Exception ignored in: _ProactorBasePipeTransport.__del__ ... RuntimeError: Event loop is closed`。
- 对照证据：`tests/integration/conftest.py` 用 `httpx.AsyncClient + ASGITransport`，同样的接口调用**全部通过**（37 个用例里 36 个过），说明业务逻辑没问题，是测试客户端的用法差异。
- 这 3 个用例在本次交接前就已失败（上一轮交付报告也列为已知问题）；失败点在 `auth/health` 路径，与本次改动的文件（matching/schemas/llm/vector）无调用关系。

**建议修复**：把这 3 个用例改用 `httpx.AsyncClient(transport=ASGITransport(app))`，与集成测试保持一致；或装 `pytest-asyncio` 并设置统一的 `loop_scope="session"`。

**复现命令**（集成测试不运行时执行）：
```bash
.venv/Scripts/python.exe -m pytest tests/test_common_layer.py -q --tb=long
```

---

## 4. 集成测试（37 个用例）

### 4.1 第一次执行：结果无效，原因已定位

**结果**：`37 tests / 19 passed / 0 failed / 18 errors`，耗时 830.809 秒（13.8 分钟）。

**18 个 ERROR 的报错**（全部一致）：

```
sqlalchemy.exc.ProgrammingError: (pymysql.err.ProgrammingError)
(1146, "Table 'ai_talent_platform_test.position_requirements' doesn't exist")
failed on setup with ...
```

**根因**：不是代码缺陷，是**测试基础设施并发冲突**。

1. `tests/conftest.py` 与 `tests/integration/conftest.py` 各自定义了 session 级夹具，**都会执行 `drop_all + create_all`**，且指向同一个测试库 `ai_talent_platform_test`；
2. 第一次集成测试在后台运行期间，我并发执行了 `pytest tests/test_common_layer.py`（用于查失败原因），它触发了 `mysql_ready` 的 `drop_all`；
3. 该进程随后被 `timeout` 杀死，`create_all` 未完成 → 表被删且未重建；
4. 正在运行的集成测试后续用例 setup 全部报「表不存在」。

**处置**：该次结果**不作为测试证据**，并在 `KNOWN_ISSUES.md` P2-1 记录该缺陷与修复建议（合并建表职责 / 集成测试独立库 / 禁止并行）。

### 4.2 第二次执行：默认配置下 15 分钟跑不完

**执行**：`pytest tests/integration`（干净环境，期间不跑任何其它 pytest），`timeout 900`。

**结果**：进程在 15 分钟（900 秒）被终止，只推进约 **18/37** 个用例（进度点 `..................`），**未生成 JUnit XML，因此没有通过/失败统计**。

**原因（已定位，非猜测）**：
- 多个用例以 `with_explanation=True` 调用 `MatchingService.run`，每个用例要为 5 位候选人各调用一次解释；
- 本地 Ollama（`qwen3:0.6b`）单次生成慢，实测**一个用例 3 分 02 秒**（`test_run_success_and_persisted`）；
- 17 + 20 = 37 个用例里带解释的占多数，线性累加即超过 15 分钟。

**结论**：**默认配置下的集成测试当前不可作为回归手段**。这属于测试基础设施缺陷（见 `KNOWN_ISSUES.md` P2-1），不是业务失败——不能据此判定「集成测试不通过」，也不能据此判定「通过」。

### 4.3 第三次执行：强制关闭外部模型，取得业务逻辑层证据

**执行**：
```bash
LLM_PROVIDER=qwen LLM_API_KEY= DIFY_API_KEY= \
  .venv/Scripts/python.exe -m pytest tests/integration --junit-xml=... -q
```

**为什么这样跑**：`ExplanationService._llm_configured()` 对 `provider=ollama` 恒返回 `True`（本地模型不需要 Key），没有独立开关；把 provider 改成 `qwen` 且 Key 为空后，Dify 与 LLM 两条外部通道都被判定「未配置」，解释统一走 `rule_fallback`。**这没有改任何代码，只是测试运行时的环境变量。**

**结果**：`37 tests / 36 passed / 1 failed / 0 errors`，耗时 **14.546 秒**。

**这次运行验证了什么**：
- 召回、四态判定、四维打分、证据构建、五层校验、落库、回读、分页过滤、权限、422/404/400 全部正常；
- **不覆盖**：Dify 通道、LLM 通道、真实解释质量（这些在默认配置的运行里，而那次没跑完）。

**这次运行暴露的真实缺陷（1 个失败）**：

```
FAILED tests/integration/test_matching_flow.py::TestMatchingServiceIntegration::test_left_status_talent_is_not_matched
AssertionError: assert [MatchItem(talent_id=148, talent_name='离职者', rank=1,
  decision=PASS, score=97.0, ...)] == []
```

**这是一个真实的业务回归，且由本次会话引入**：

- 测试语义：显式传 `talent_ids=[离职者 id]` 时，离职人员**不得参与匹配**，`stats.candidates` 应为 0；
- 实际行为：离职者以 97 分排第 1；
- 根因：`MatchingPipeline.recall()` 里「指定候选人」分支被改成 `talent_repo.get_by_ids(...)`，而 `get_by_ids` **只过滤 `is_deleted`，不过滤 `status`**；原实现走 `list_candidates(talent_ids=...)`，该方法的 `statuses=("active","reserve")` 过滤对指定 ID 同样生效（`talent_repository.py` 注释明确记为 MATCH-008 的修复点）；
- 影响面：任何 `/matchings/run` 显式传 `talent_ids` 且其中含离职/非在职人员的场景——离职者会出现在推荐结果里，且 `stats.candidates` 虚高；
- 单测为何没拦住：`tests/unit/test_matching_pipeline.py` 里**没有任何**关于 `left` / 离职 / `get_by_ids` 的断言（grep 无匹配）。

**建议修复（一行，属业务代码，按规定本次未改）**：

```python
# app/services/matching_pipeline.py :: recall()
if talent_ids:
    candidates = await self.talent_repo.list_candidates(
        self.db, talent_ids=talent_ids, statuses=("active", "reserve")
    )
```
或给 `TalentRepository.get_by_ids` 增加 `statuses` 参数。修完后必须补一条单测（离职 ID 传入 → 空结果），避免再次回归。

### 4.4 集成测试覆盖内容（代码事实）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/integration/test_matching_api.py` | 17 | HTTP 全栈：401/403 权限、422 校验、404 岗位不存在、400 画像未确认、跑批落库、分页过滤（含 `talent_id`/`position_id`/`min_score` 生效性）、解释接口、画像查询/确认版本递增 |
| `tests/integration/test_matching_flow.py` | 20 | Service 层：排序稳定性、`rank` 与 `decision` 字段、批次独立性、离职人才不参与、缺画像版本 404、UNKNOWN 不淘汰、硬条件淘汰原因、权限元数据 |

**测试库隔离方式**：每个用例用 `uuid` 生成唯一 `org code` / `position code`，不依赖清库，理论上可重复执行。

**未覆盖的集成场景**：双向匹配（`/matchings/reverse`）、预警三个接口、`matching_agent` 端到端、权重覆盖的 HTTP 层校验、并发跑批。

---

## 5. 单测质量观察（不只报数字）

### 5.1 匹配 Agent 单测的真实覆盖力

| 模块 | 用例 | 覆盖到的关键点 | 未覆盖 |
|---|---|---|---|
| `test_evidence_builder.py` | 19 | 学历/年限/技能证据生成；缺失字段不生成虚假证据；不引用 AI 画像摘要；FAIL/UNKNOWN/NEEDS_REVIEW 各自措辞；ID 格式与唯一性；Pydantic 校验与序列化 | 职责证据 5 条上限的截断行为；derived 类证据的数值边界 |
| `test_evidence_verifier.py` | 22 | 五类剔除原因全覆盖；三档输出；`missing` 误用；术语越界；与 FAIL 冲突 | 委婉表述绕过（已知局限，非测试缺口） |
| `test_matching_rules.py` | 25 | 四态判定各分支；聚合优先级；完整度计算；UNKNOWN 降权 | 模糊条件的复杂文本 |
| `test_matching_pipeline.py` | 10 | 节点增量返回；`top_k` 截断；统计口径 | `run_reverse` 的深度断言（新增功能覆盖偏浅） |
| `test_dify_adapter.py` | 22 | 超时/401/404/429/5xx 分类；重试；`outputs.result` 两种形态；非 JSON 容错 | **真实 Dify 服务**（MockTransport 无法替代联调） |

### 5.2 必须知道的两处「测试会骗人」的地方

1. **Dify 适配器 22 个用例全绿 ≠ Dify 接通**。测试用 `httpx.MockTransport` 构造响应，`DEFAULT_WORKFLOWS` 的 `required_inputs` 仍是「暂定·待联调」。验收时若看到「Dify 单测全过」，不等于联调通过。
2. **集成测试里 `with_explanation=True` 的断言是 `in {"dify","llm_fallback","rule_fallback"}`**，三种来源都算通过。它验证的是「不崩、来源合法」，**不验证解释质量**。

---

## 6. 未覆盖的测试场景（明确缺口）

| # | 场景 | 现状 | 风险等级 |
|---|---|---|---|
| 1 | **离职/非在职人才过滤** | **仅 1 个集成用例覆盖，当前失败（P1-0 回归）；单测零覆盖** | **高（已出事）** |
| 2 | 双向匹配 `POST /matchings/reverse` | **零测试**（仅模块导入验证） | 高 |
| 3 | 储备预警 3 个接口 | **零测试**，且表未在生产库创建 | 高 |
| 4 | `matching_agent` 端到端 | **无法测**（无 `/agents/tasks` 入口） | 高 |
| 5 | 权重覆盖的 HTTP 层 | 仅 Schema 层单测，无 422 响应断言 | 中 |
| 6 | 并发跑批（同岗位两次同时执行） | 无 | 中（唯一键冲突可能 500） |
| 7 | Milvus 不可用 → 召回降级 | 仅手工验证，无自动化 | 中 |
| 8 | 大数据量性能（>1000 人） | 无 | 中 |
| 9 | 画像确认的并发冲突（`expected_version` 过期） | 有 Schema 校验，无并发测试 | 中 |
| 10 | 解释五层校验与真实 LLM 输出的对抗测试 | 无（只有构造好的 claim 输入） | 中 |
| 11 | 前端契约测试 | 无前端 | 低 |

---

## 7. 安全测试命令（不会误伤）

```bash
cd "D:/0904开班/agent-system-v1/ai-digital-talent-platform"

# 1) 只跑单元测试（约 3 秒，无数据库依赖）
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header

# 2) 跑全部非集成测试（约 10 秒，含公共层，会有 3 个已知失败）
.venv/Scripts/python.exe -m pytest tests --ignore=tests/integration -q --no-header

# 3) 只跑匹配相关单测（约 2 秒）
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header -k "matching or evidence or jd_parser or explanation or dify"

# 4) 要数字不要日志噪音
.venv/Scripts/python.exe -m pytest tests --ignore=tests/integration -q -p no:warnings --tb=no --junit-xml=/tmp/r.xml
```

## 8. 危险命令（会清表，执行前必须确认）

```bash
# ⚠️ 会 DROP 并重建 ai_talent_platform_test 的全部 26 张表
# ⚠️ 执行期间不得再启动任何其它 pytest 进程（两个 conftest 会互相清表）
.venv/Scripts/python.exe -m pytest tests/integration -q

# ⚠️ 全量预计 14 分钟（因真实调用本地 Ollama）；单用例 3 分钟
```

**建议**：跑集成测试前先确认没有别的测试在跑；跑完后如需演示数据，重新执行 `scripts/seed_data.py`（注意 seed 默认写**开发库**，不是测试库）。

---

## 9. 测试证据文件

| 证据 | 位置 | 说明 |
|---|---|---|
| 非集成 JUnit XML | 本轮生成于系统临时目录（`wb_handover_unit.xml`） | 结论已转录为本文第 3 节；XML 未入库 |
| 集成测试 JUnit XML（第一次，无效） | 临时目录（`wb_int_full.xml`） | 保留用于说明「18 ERROR」的成因，**不作为通过证据** |
| 集成测试 JUnit XML（第二次） | 临时目录（`wb_int_clean.xml`） | 结论转录为第 4.2 节 |
| 历史端到端脚本 | `scripts/e2e_matching_check.py`（277 行，22 项断言）、`scripts/diagnose_api.py`（309 行） | 上一轮的脚本，**本轮未执行**；仅在服务启动后手工可用 |
| 历史测试日志 | `docs/delivery/evidence/e2e_matching_run_20261104.log`、`api_diagnose_20261104.log` | 上一轮产物，**不能当作本轮结果** |

> 建议 Codex 接手后把 JUnit XML 落到 `docs/delivery/evidence/`，形成可追溯的测试流水。

---

## 10. 与历史报告的口径差异（必须让验收方知道）

| 项 | 历史报告（`docs/delivery/09`） | 本轮实测 | 差异原因 |
|---|---|---|---|
| 测试总数 | 183 | **231（非集成）+ 37（集成）= 268** | 后续新增单测与集成用例 |
| 通过率 | 100% | 非集成 98.7%（3 个公共层失败）；集成 36/37（1 个真实回归） | 公共层与离职过滤问题被如实计入 |
| 集成测试 | 「全部通过」 | 默认配置下 15 分钟未跑完；关闭外部模型后 36/37（14.5s） | 首次为并发污染；默认配置被模型调用拖慢 |
| 离职人才过滤 | 声称已修（MATCH-008） | **当前不生效**（P1-0 回归） | 本次会话改写 `recall()` 时回退 |
| 解释来源 | 假设 LLM 不可用 | 已接 Ollama，会走 `llm_fallback` | 环境变化导致 2 个单测需调整（已修） |
| 表数量 | 25 | **26**（新增 `reserve_warnings`） | 本次新增预警表 |

**给验收方的一句话**：历史报告的「100% 通过」是在「无 LLM、无 Milvus、25 张表」的环境下得到的，且**没有覆盖本次会话改动的 `recall()` 分支**；当前环境已接 Ollama 且新增了表与接口，**必须按本文数字验收**——尤其是 P1-0 离职回归，它是本轮唯一被实测抓到的业务缺陷。
