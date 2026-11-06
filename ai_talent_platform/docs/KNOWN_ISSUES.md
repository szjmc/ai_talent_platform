# 岗位人才匹配 Agent —— 遗留问题与风险清单

> 依据：2026-11-04 对当前工作区的代码核对 + 本轮真实测试执行。
> 排序原则：P0 = 不解决就无法交付/会丢数据；P1 = 核心能力缺失；P2 = 测试与工程缺口；P3 = 优化项。
> 每条包含：**原因 / 影响 / 建议方案**。凡无法确认的标注「待核实」。

---

## P0 —— 阻断级（动手写新功能前必须先处理）

### P0-1　两次 DDL 只在开发库执行，测试/生产库无表结构

**原因**
- `docs/sql/2026-11-04_matching_records_trace.sql`：给 `matching_records` 加 5 个可空列（`requirement_version` / `recall_mode` / `hard_results` / `explanation` / `stats`）。
- `docs/sql/2026-11-04_reserve_warnings.sql`：建 `reserve_warnings` 表（本次新增）。

**影响**
- 测试/生产库跑匹配落库会直接报 `Unknown column`，整批失败（不是降级，是 500）。
- 预警三个接口 100% 报错（表不存在）。
- 新环境虽然有 `create_all` 可建出表，但**存量环境只能靠手工执行 SQL**。

**建议方案**
1. 把两份 SQL 纳入发布清单，由 DBA 在测试/生产分别执行；
2. 执行后跑 `scripts/init_db.py --check` 或 `GET /system/info` 比对 `orm_tables` 是否含 `reserve_warnings`；
3. 长期：引入 Alembic 迁移，禁止再靠手写 SQL 演进表结构。

---

### P0-2　27 项改动未提交（19 修改 + 6 新增），且本地提交未推送

**原因**
- 当前 HEAD `bed2b54`（匹配 Agent 主体）**只在本地**，`origin/lz_agent` 仍停在 `f29ac08`；
- 工作区另有 19 个修改文件、6 个未跟踪文件（本次会话的双向匹配、权重、预警、Agent 外壳、向量层、Dify 文档）。

**影响**
- 一次误操作（`git checkout .` / 磁盘故障）即丢失本轮全部功能；
- 其他人拉不到代码，无法评审、无法并行开发；
- 交接给 Codex 后，若它直接改代码，会与这批未提交改动混杂，**无法区分谁改了什么**。

**建议方案**
1. 先 `git add -A && git commit`（`.env` 已在 `.gitignore`，不会入库），建议按逻辑拆 3~4 个提交：权重+双向匹配 / 预警 / 向量层 / 文档；
2. `git push origin lz_agent`；
3. 推送前确认远程没有新提交（当前 `HEAD..origin/lz_agent` 为空，可直接 push）。

---

### P0-3　Dify 两条工作流的契约未联调，属「假接通」风险

**原因**
- `app/adapters/dify/dify_adapter.py` 的 `DEFAULT_WORKFLOWS` 里，`required_inputs=("jd_text",)` 与 `("evidence_pack",)` 均标注「暂定·待联调」；
- 单测 22 个用例全绿，但用的是 `httpx.MockTransport`，**只证明适配器自身行为正确，不证明真实服务可用**。

**影响**
- 联调时若 Dify 侧变量名/输出结构不同，解析会静默降级为规则结果，业务方看到的是「AI 没生效」而非报错；
- 这是当前最大的「验收失真」来源。

**建议方案**
1. 把 `docs/delivery/18_Dify组对接需求文档.html` 发给 Dify 组，要求书面回复 6 个确认项；
2. 拿到 Base URL + 2 个 API Key 后填入 `.env`，用真实 JD 跑 `POST /positions/{id}/jd/parse`，确认响应 `channel="dify"`；
3. 用真实证据包跑 `POST /matchings/explain`，确认 `source="dify"` 且 `rejected_claims` 率可接受；
4. **在契约冻结前，不要在任何验收材料里写「Dify 已接通」**。

---

### P0-4　人才档案接口全为 501 占位，匹配的上游数据入口不存在

**原因**
- `app/routers/router.py` 的 `_STUB_MODULES` 包含 `talents`，走 `placeholder_router`，返回 501；
- 匹配直接读 `Talent` 表，但没有任何接口能创建人才。

**影响**
- 任何新环境必须跑 `scripts/seed_data.py` 才能演示；
- HR 无法自助录入人才，产品闭环在第一步就断；
- 需求「模块二 人才档案接入」实际未交付。

**建议方案**
1. 优先实现 `POST /talents`、`GET /talents`、`GET /talents/{id}`、`PUT /talents/{id}` 四个最小接口（其余 5 个可后置）；
2. 与简历解析 Agent 对齐字段映射后再做导入，避免二次返工；
3. 在 `/talents` 可用前，`seed_data.py` 必须保持可重复执行（幂等）。

---

## P1 —— 核心功能缺失

### P1-0（★最高优先）　离职人才会被纳入匹配 —— 本次会话引入的回归，已实测复现

**原因**
- `app/services/matching_pipeline.py :: recall()` 的「指定候选人」分支当前是：
  ```python
  if talent_ids:
      candidates = await self.talent_repo.get_by_ids(self.db, talent_ids)
  ```
- `TalentRepository.get_by_ids()` **只过滤 `is_deleted`，不过滤 `status`**；
- 原实现走 `list_candidates(talent_ids=...)`，其 `statuses=("active","reserve")` 过滤对「显式指定 ID」同样生效——`talent_repository.py` 的注释明确写着这是 MATCH-008 的修复点（「原实现只在全库召回时过滤状态，显式传 talent_ids 时不做状态判断，会把离职人员拉进来」）；
- 本次会话为接入向量召回改写 `recall()` 时，把该分支换成了 `get_by_ids`，**等于回退了 MATCH-008 的修复**。

**影响（已实测）**
- 集成测试 `test_left_status_talent_is_not_matched` **失败**：离职者（`status="left"`、8 年经验）以 **97 分排在推荐结果第 1**；
- 任何 `/matchings/run` 显式传 `talent_ids` 且含离职/非在职人员的场景，都会把离职者推荐给 HR；
- `stats.candidates` 虚高，跑批统计失真；
- 演示时若被业务方看到「离职者 97 分」，会直接质疑系统可信度。

**为什么测试没拦住**
- `tests/unit/test_matching_pipeline.py`（10 个用例）**没有任何**关于离职/`left`/`get_by_ids` 的断言；
- 只有集成测试覆盖此场景，而集成测试默认跑不完（见 P2-1），问题长期被掩盖。

**建议方案（改动量 1 行 + 1 个单测）**
```python
# app/services/matching_pipeline.py :: recall()
if talent_ids:
    candidates = await self.talent_repo.list_candidates(
        self.db, talent_ids=talent_ids, statuses=("active", "reserve")
    )
```
或给 `get_by_ids` 增加 `statuses` 参数（注意别影响其它调用方）。
补单测：`talent_ids=[离职者ID]` → `candidates == []`、`stats.candidates == 0`。

> 本次交接按要求未修改业务代码，**此问题仍存在于工作区**，Codex 接手后应作为第一优先修复。

---

### P1-1　`POST /agents/tasks` 等 5 个 Agent 接口不存在，`matching_agent` 无入口

**原因**
- `app/routers/agent_router.py` 只有 `GET /agents`（返回 501 占位）；
- docstring 里列的 `POST /agents/chat`、`POST /agents/chat/stream`、`GET /agents/conversations`、`POST /agents/tasks` **全部未实现**；
- `matching_agent.py` 已实现并注册（`agent_registry` 有 `matching`），但没有任何 HTTP 路径能调用 `dispatch()`。

**影响**
- 「Agent」这一层能力对外不可见，只能通过 `/matchings/run` 这类业务接口间接使用；
- 需求与计划文档（`docs/plan/02-Agent组.md` D10）明确要求 `POST /agents/tasks` 可用，当前验收无法通过。

**建议方案**
1. 实现 `POST /agents/tasks`：入参 `{type, payload}`，内部 `agent._resolve(type).run(payload)`，返回 `{task_id, status, result}`；
2. 同步实现 `GET /agents/tasks/{task_id}`（即使先返回内存态）；
3. 补一条集成测试：`type="matching"` 返回 200 且结果可落库；
4. SSE（`/agents/chat/stream`）可后置，但要在文档中明确「未实现」。

---

### P1-2　双向匹配只验证到模块导入，未做端到端调用

**原因**
- 本次新增 `POST /matchings/reverse` 后，只执行了「模块导入 + `MatchReverseRequest` 校验 + 方法存在性检查」；
- 没有真实 HTTP 请求，也没有集成测试覆盖。

**影响**
- 运行时可能因岗位仓库方法签名、画像版本读取、`MatchReverseItem` 字段缺失而 500；
- 交接材料若写「双向匹配已实现」，属于只验证了编译期。

**建议方案**
1. 用 seed 数据发一次真实请求：`POST /api/v1/matchings/reverse {"talent_id": <张伟 id>}`；
2. 断言：`positions_evaluated > 0`、`items` 按分数倒序、`excluded` 带原因、`skipped` 不为空时含 reason；
3. 补 2 个集成用例（正常 + 人才不存在 404），写入 `tests/integration/test_matching_api.py`。

---

### P1-3　语义匹配未实现，现有打分只有 4 个规则维度

**原因**
- `compute_match_score` 只有 `skill`（技能集合交集比）、`experience`（年限差）、`education`（学历级差）、`quality`（潜力等级）；
- 需求 5.2 要求的「项目经验相关性、岗位职责相似度、行业经验、可迁移能力、成果可信度」无对应实现；
- `talents` 表无结构化项目经历字段，向量召回也未启用。

**影响**
- 两个技能相同但项目复杂度差异很大的人，得分可能一致；
- 业务方若按「语义匹配」预期验收，会认为系统「不懂业务」。

**建议方案**
1. 短期：在 `MatchItem` / 解释中明确写「当前为规则匹配，不含语义相似度」，避免误解；
2. 中期：启用 Milvus + bge-m3，把「职责-经历」相似度作为第 5 维（需业务确认权重）；
3. 长期：补齐项目经历结构化字段，否则语义维度无原料。

---

### P1-4　储备预警是简化版，缺编制/继任数据模型

**原因**
- `reserve_warning_service.py` 只用「岗位 + 匹配记录 + 人才状态」三类数据；
- 需求 7.1 要求的「岗位编制、招聘计划、继任关系」在 ORM 中不存在。

**影响**
- 预警只能回答「这个岗位多久没匹配到人 / 候选池够不够」，答不了「编制缺几人、有无接班人」；
- 业务方按需求验收会判定「预警不完整」。

**建议方案**
1. 与业务确认第一阶段是否接受简化口径（当前实现是「零产出/储备不足/长期空缺」三类）；
2. 若不接受，需先补 `position_headcount`、`succession_plan` 两张表及数据来源；
3. 无论哪种，都要在接口文档中写明「预警依据的数据域」。

---

### P1-5　无任务状态机，长任务只能同步等待

**原因**
- 需求 04 定义了 `CREATED → PARSING → WAITING_CONFIRMATION → RETRIEVING → FILTERING → SCORING → EXPLAINING → COMPLETED`；
- 代码里没有任何任务表/状态字段，`POST /matchings/run` 是同步请求-响应。

**影响**
- 一次跑批 20 人 × 解释（当前 Ollama 实测单用例 3 分钟）会触发网关超时；
- 前端无法展示进度，失败无法续跑。

**建议方案**
1. 新增 `matching_tasks` 表（`task_id / status / progress / batch_id / error`）；
2. `POST /matchings/run` 改为「立即返回 task_id → 后台执行」；
3. 保留同步模式作为 `?sync=true`（测试与演示用）。

---

## P2 —— 测试与工程缺口

### P2-1　集成测试全量极慢；且两个 conftest 会互相清表，不能并行跑任何其他测试

**原因（两个独立问题）**

1. **慢**：`with_explanation=True` 的用例会真实调用本地 Ollama。**本轮实测 `test_run_success_and_persisted` 单用例 3 分 02 秒**；全量 37 个用例 **830.8 秒（13.8 分钟）**。

2. **会互相清表**（比原判断更精确）：
   - `tests/conftest.py` 导入时通过 `os.environ` 把 `DB_NAME` 覆盖为 **`ai_talent_platform_test`**（独立测试库，**不是开发库**，这一点是好的）；
   - 但 `tests/conftest.py::mysql_ready` 和 `tests/integration/conftest.py::prepare_db` **两个 session 级夹具都会执行 `drop_all + create_all`**，且指向**同一个测试库**；
   - 因此在集成测试运行期间再启动任何其他 pytest 进程，会二次 `drop_all`，把正在跑的表删掉。

**本轮实测证据（三组数据，互相印证）**：

| 运行条件 | 结果 | 耗时 |
|---|---|---|
| 默认配置（真实调 Ollama） | 15 分钟内未跑完（约 18/37），被 timeout 终止 | >900s |
| 默认配置 + 并发跑了另一个 pytest | 37 用例 / 18 errors（表被删） | 830s |
| `LLM_PROVIDER=qwen LLM_API_KEY=`（强制 rule_fallback） | **37 用例 / 36 通过 / 1 失败** | **14.5s** |

**关键结论**：**同一套测试，只因是否调用外部模型，耗时从 14.5 秒变成 15 分钟以上**。这说明慢的根因是模型调用，不是数据库或业务逻辑。

**影响**
- 全量集成测试需 15 分钟以上，CI 无法接受，开发者实际不会跑；
- **同一测试库 + 双份 drop/create = 任何并行测试都会互相破坏**，会得到假 ERROR（上一轮 18 个错误即如此），排查成本极高；
- 因为集成测试跑不动，P1-0 的离职回归长期没被发现。

**建议方案**
1. **合并建表职责**：`tests/conftest.py::mysql_ready` 只负责建库建表，`tests/integration/conftest.py::prepare_db` 改为只 `create_all`（不 `drop_all`）或直接复用前者；
2. 集成测试改用**独立库名**（如 `ai_talent_platform_it`），与单测的 `ai_talent_platform_test` 彻底隔离；
3. 需要外部模型的用例加 `@pytest.mark.slow` 默认 skip，或在用例内 monkeypatch `_llm_configured` 返回 False——按本轮实测，这能把 15 分钟压到 15 秒；
4. 加 `pytest-timeout`（`--timeout=120`），避免单用例无限等待；
5. CI 拆分单元/集成两个 job，并约定「集成测试期间不得并行跑其它 pytest」。

---

### P2-2　3 个公共层失败用例（已取得真实错误，属测试客户端问题）

**原因（已核实 traceback）**
- `test_register_login_and_me`、`test_super_admin_full_flow`：
  `AttributeError: 'NoneType' object has no attribute 'send'`（`asyncio/proactor_events.py:397`），末尾伴随 `Exception ignored in: _ProactorBasePipeTransport.__del__ ... RuntimeError: Event loop is closed`；
  这是 **Windows + Python 3.10 + Starlette 同步 `TestClient`** 的组合问题：TestClient 在独立线程跑事件循环，用例结束后循环被关闭，传输层析构二次调用 `send`。
- `test_health_ready`：`AssertionError`，`/health/ready` 返回与断言不符——**具体期望/实际值待核实**（需 `--tb=long` 展开）。

**对照证据**：`tests/integration/conftest.py` 用 `httpx.AsyncClient + ASGITransport`，同样的接口调用 36/37 通过，说明业务逻辑没问题，差异在测试客户端用法。

**影响**
- 每次全量测试都是「3 failed」，新人无法判断是否自己引入；掩盖真实回归。

**建议方案**
1. 把这 3 个用例改成 `httpx.AsyncClient(transport=ASGITransport(app))`，与集成测试一致；
2. 或装 `pytest-asyncio` 并统一 `loop_scope="session"`；
3. 在修复前先 `xfail` 并写明原因，让「3 failed」变成「3 xfailed」，基线清晰。

---

### P2-3　无覆盖率、无 CI、无 lint 门禁

**原因**
- 仓库无 `.github/workflows`、无 `.gitlab-ci.yml`、无 `Makefile`、无 `pyproject.toml`；
- 计划文档要求「覆盖率 ≥ 70%」，无任何覆盖率报告。

**影响**
- 无法量化测试有效性；
- 代码风格与类型问题只能靠人工 review。

**建议方案**
1. 加 `pytest-cov`，先出一份基线报告（不设阈值）；
2. 加最小 CI：`ruff check` + `pytest tests/unit`（3 分钟内可跑完）；
3. 覆盖率阈值在有基线后再设，避免一上线就红。

---

### P2-4　未覆盖的测试场景

| 场景 | 现状 | 风险 |
|---|---|---|
| 双向匹配正常/异常 | 无集成测试 | P1-2 |
| 预警扫描/解决 | 无任何测试 | 接口未实跑 |
| `matching_agent` 端到端 | 无测试 | 无 HTTP 入口，无法测 |
| 权重非法的 422 响应 | 仅 Schema 层验证，无 HTTP 层 | 错误码可能被全局处理器改写 |
| 并发跑批（同一岗位同时两次） | 无测试 | 唯一键冲突可能 500 |
| Milvus 不可用时的召回降级 | 仅手工验证 | 无自动化回归 |
| 大数据量（>1000 人）性能 | 无测试 | `recall_limit` 截断行为未验证 |

---

### P2-5　`requirements.txt` 是 UTF-16LE + BOM

**原因**：历史某次 PowerShell `>` 重定向写成 UTF-16。git 视为二进制，diff 不可读。

**影响**
- `pip install -r requirements.txt` 在部分环境会解析失败；
- 新人 `pip install` 报错后误以为依赖缺失。

**建议方案**
1. 转 UTF-8（无 BOM）后提交，单独一个 commit 说明「编码修正，内容不变」；
2. 同时补 `pymilvus / redis / minio / pymupdf / python-docx` 等**代码已引用但未声明**的包；
3. 校验方式：`python -c "open('requirements.txt',encoding='utf-8').read()"` 不报错。

---

## P3 —— 优化与债务

### P3-1　`matching_records` 孤儿记录风险
- **原因**：DEC-9 决定不建物理外键，靠 Service 校验存在性。
- **影响**：岗位/人才删除后，历史匹配记录仍指向不存在的 ID，列表页可能显示空白姓名。
- **建议**：读接口 LEFT JOIN 时容忍缺失并回填「已删除」；或加定期清理任务。

### P3-2　证据颗粒度受限
- **原因**：职责证据最多 5 条（`EvidenceBuildConfig.max_responsibility_evidence`），项目经历无结构化字段。
- **影响**：长 JD 的职责覆盖不完整，解释可能漏掉关键要求。
- **建议**：按 `required_conditions` 数量动态调整上限；补项目经历字段后再扩展。

### P3-3　能力术语校验是黑名单，不是语义校验
- **原因**：`evidence_verifier` 用白名单+黑名单拦截「高并发/架构设计」等术语。
- **影响**：换个说法（「写过定时任务」→「具备复杂调度设计能力」）能绕过。
- **建议**：短期接受（模块 docstring 已如实声明）；长期引入领域本体或人工抽检。

### P3-4　权重只在请求/岗位级，无全局配置与版本
- **原因**：`MATCH_DEFAULT_WEIGHTS` 是常量，岗位 `requirements.weights` 可配，请求可覆盖。
- **影响**：无法追溯「某次跑批用的是哪一版权重」，只能从 `weights_used` 反查。
- **建议**：`matching_records` 增加 `rule_version` 字段，落库时写入。

### P3-5　无 Dockerfile / docker-compose 只有 MySQL
- **原因**：`docker-compose.yml` 只编排了数据库依赖，无应用服务。
- **影响**：新环境部署靠手工装 Python 3.10 + 依赖，与「一键启动」差距大。
- **建议**：补 Dockerfile（注意 Python 3.10 与 `StrEnum` 垫片的关系，升级 3.11+ 后可移除垫片）。

### P3-6　前端为零
- **原因**：仓库无任何前端代码。
- **影响**：所有能力只能通过 Swagger / curl 演示。
- **建议**：不在本 Agent 范围内，但需在验收材料中明确「无可视化界面」。

### P3-7　`StrEnum` 兼容垫片是临时方案
- **原因**：`.venv` 为 Python 3.10，`StrEnum` 是 3.11+；`app/core/permissions.py` 加了 `try/except ImportError` 垫片。
- **影响**：垫片行为与原生 `StrEnum` 在 `__str__` 上可能有细微差异（已按 `str, Enum` 实现）。
- **建议**：统一升级到 Python 3.11+ 后删除垫片，并在 `pyproject.toml` 声明 `requires-python`。

### P3-8　历史交付文档的测试数字已过期
- **原因**：`docs/delivery/09_最终交付验收报告.md` 写「183 条测试通过」，当前实测已是 268 条。
- **影响**：验收时双方数字对不上。
- **建议**：以 `docs/TEST_STATUS.md` 为准，历史报告加一行「数字已过期，见 TEST_STATUS.md」。

---

## 附：风险热力图（按「发生概率 × 影响」）

| 风险 | 概率 | 影响 | 等级 |
|---|---|---|---|
| 未提交代码丢失 | 中 | 极高 | P0-2 |
| 测试/生产库缺列导致 500 | 高 | 高 | P0-1 |
| Dify 契约不符导致静默降级 | 高 | 高 | P0-3 |
| 无人才录入导致无法演示 | 高 | 中 | P0-4 |
| **离职人才被推荐给 HR（已发生）** | **已复现** | **高（业务可信度）** | **P1-0** |
| 集成测试清表 + 15 分钟跑不动 | 高 | 中 | P2-1 |
| 双向匹配运行时 500 | 中 | 中 | P1-2 |
| Agent 能力无入口被判定未交付 | 高 | 中 | P1-1 |
| 语义匹配缺失被判定不达标 | 中 | 高 | P1-3 |
