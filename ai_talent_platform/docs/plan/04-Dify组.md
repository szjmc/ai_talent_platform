# 04. 4 组（Dify AI 应用中台 + 个性化智能培训，2 人）— 20 个工作日计划

> 隶属：[AI 能力层与业务层分组开发总计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md) ｜ **必读边界红线**：[五大研发组职责边界与防重复开发总规范 §6.4](../group_boundaries_and_tasks.md#64-4-组dify-组-dify组dify-低代码网关--个性化智能培训)
> 人力：2 人（DF-A Dify 适配器/SSE 转换 + 培训计划与进度服务 / DF-B 集成探针/同步脚本 + 培训路由与低代码督办工作流）

---

## 本组速览与严格边界（防重复必读！）

- **组别**：4 组 `[Dify组]`（标签：`标签：[Dify组] [P0|P1|P2]`，全仓现存 **7 个 `.py` 文件 + 3 个待新建文件**）
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **本组端到端核心边界（Dify 低代码网关 + 个性化培训督办闭环）**：
  1. 封装 Dify HTTP/SSE 异步适配器 `app/adapters/dify/dify_adapter.py` 与流式协议归一化解析器 `app/adapters/dify/sse.py`；
  2. 提供可选连通性探针 `app/adapters/dify/health.py` 与工作流/知识库单向镜像同步脚本 `scripts/sync_dify_workflows.py`；
  3. 配合 `[Agent组]` 在 `app/services/agent_service.py` 中维护唯一的双模路由开关（配置了 `DIFY_APP_ID_*` 优先走 Dify，未配置或故障时无缝回退本地 `BaseAgent`）；
  4. **个性化智能培训与学习督办闭环（`trainings`）**：`training_{repository,service,router}.py` + `schemas/training.py`（课程与计划 CRUD、学习进度单调递增校验与 `suspicious` 防挂机刷课标记、滞后计划预警 `GET /trainings/warnings`；AI 选课与滞后催办支持优先走 `DifyAdapter.run_workflow`、未启用 Dify 时自动回退 `await dispatch("training", ...)`）。
- **本组严禁重复开发清单**：
  - ❌ **严禁**新建 `/api/v1/dify/*` 等业务层重复路由，严禁把 Dify 变成硬依赖（未配 `DIFY_API_KEY` 时 `/api/v1/trainings/*` 与 `/api/v1/agents/*` 必须 100% 正常工作）；
  - ❌ **严禁**新建或重写 `app/models/training.py`（4 张培训 ORM 表已落地 ✅）或接管 `/api/v1/knowledge/*`；
  - ❌ **严禁**在 Dify 侧另建绕过 `[Vanna组] sql_validator` 的数据库直连查询，严禁在 `TrainingService` 手写 Prompt 直调大模型；
  - ❌ **严禁**自写重试与熔断工具（必须直接复用 `[架构组] app.ai.utils.retry_handler` 中的 `ai_retry` 与 `SimpleCircuitBreaker`）。

---

## 一、人员分工

| 角色 | 负责 |
| --- | --- |
| DF-A（适配器 + 培训核心） | `dify_adapter.py`、`sse.py`、`training_repository.py`、`training_service.py` |
| DF-B（集成运维 + 培训接口） | `adapters/dify/health.py`、`scripts/sync_dify_workflows.py`、`schemas/training.py`、`training_router.py`、Dify 培养推荐与滞后督办工作流 DSL |

## 二、每日任务（D1–D20，每行末尾是当日验收标准）

| 天 | DF-A 适配器 | DF-B 集成/运维 | 当日验收 |
| --- | --- | --- | --- |
| D1 | 读 Dify API 文档，确认 4 个能力签名 | Docker 部署 Dify（api/worker/web/db/redis），记录版本 | Dify 控制台可访问，健康检查绿 |
| D2 | `__init__`（httpx.AsyncClient、Bearer、超时、trace 头） | 建 2 个应用：通用问答 / 简历解析；导出 DSL 备份 | 单测：走 MockTransport 返回固定 JSON |
| D3 | `chat(app_id, query, conversation_id, user)` | 配置模型供应商（通义/OpenAI 兼容），额度与并发 | 真实对话返回答案 + conversation_id |
| D4 | `run_workflow(workflow_id, inputs, user)`（阻塞） | 建工作流 1：简历解析（输入文件 URL → 结构化 JSON） | 工作流跑通并回传 JSON |
| D5 | SSE 解析（`stream` 逐行 `data:`，事件类型 `message/workflow_finished/error`） | 断线重连与 Dify 侧 conversation 复用 | 流式输出逐 token 到前端 |
| D6 | `upload_document / list_documents(dataset_id)` | 建知识库 dataset，导入 20 份制度文档 | 文档列表可查、可删除 |
| D7 | 错误映射（Dify 4xx/5xx → `ExternalServiceError`，附 request_id） | 与 1 组对齐：Dify 知识库 vs 自建 Milvus 的边界（谁做索引） | 边界决策写入文档 |
| D8 | 回调/日志查询接口（Dify logs） | 与 2 组对齐：Dify 工作流 vs LangGraph 的边界 | 边界决策签字 |
| D9 | 统一入口 `DifyClient` 注册到 `Registry("dify")` | Prompt 同步：`prompt_config` ←→ Dify Prompt（单向覆盖） | `prompt_config` 支持远端覆盖 |
| D10 | 并发与限流（Semaphore + 429 退避） | 配额监控（按应用统计调用量） | 压测 50 QPS 不雪崩 |
| D11 | 与 `agent_router` 的 SSE 转发打通 | 演示应用 3 个（问答/解析/分析）上线 | `/agents/chat/stream` 走 Dify 可用 |
| D12 | 集成报告 #1 | 集成报告 #1 | 报告 |
| D13 | 健康检查 `check_dify()`（供 `/health/ready` 扩展） | Dify 备份/恢复演练 | `/health/ready` 含 dify 状态 |
| D14 | 重试与熔断（复用 `ai/utils/retry_handler`） | 日志对齐（trace_id → Dify request_id） | 排障演练 15 分钟定位问题 |
| D15 | 文件上传大文件分片 | 知识库增量同步脚本 | 同步脚本幂等 |
| D16 | 降级：Dify 不可用 → 回退本地 Agent/RAG | 降级演练 | 无 500 |
| D17 | 文档：Dify 应用如何接新 Agent | 文档：运维手册 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | CI 加入 Dify Mock 用例 | `pytest tests/test_dify.py` |
| D19 | 演示 | 演示 | 演示脚本 |
| D20 | 终验 | 终验 | 总计划 §九 验收清单 |

## 三、本组要写的代码（文件级）

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/adapters/dify/dify_adapter.py` | `httpx` | `DifyAdapter(base_url, api_key)`；`chat()`、`chat_stream()`、`run_workflow()`、`run_workflow_stream()`、`upload_document()`、`list_documents()`、`delete_document()`；统一 `_request()` 错误映射与重试 |
| `app/adapters/dify/sse.py` | **新增** | Dify SSE 事件解析器（`event/data` 行），产出 `async Iterator[dict]` |
| `app/config/settings.py` | 修改 | 补 `DIFY_APP_ID_CHAT/DIFY_APP_ID_RESUME/DIFY_DATASET_ID/DIFY_TIMEOUT/DIFY_MAX_RETRIES` |
| `app/infrastructure/database/health.py` 或 `app/adapters/dify/health.py` | **新增** | `check_dify()` 返回与 `check_mysql` 同构的 `{status, reason, message, elapsed_ms}` |
| `app/services/agent_service.py` | 跨组协同 | 双模路由分支（与 `[Agent组]` 同文件，PR 须 2 组组长联合 review） |
| `scripts/sync_dify_workflows.py` | **新增**（P2） | 本地已索引知识文档 → Dify dataset 单向同步与工作流可用性巡检 |
| `app/repositories/training_repository.py`、`app/schemas/training.py`、`app/services/training_service.py`、`app/routers/training_router.py` | 🔧 本组实现（P1） | **智能培训与督办闭环**：课程/计划管理、学习进度单调递增与 `suspicious` 防刷标记、滞后计划预警、Dify 工作流 / `dispatch("training")` 双模推课，实现 `/trainings` 下 9 个接口 |
| `tests/test_dify.py`、`tests/test_training.py` | — | `httpx.MockTransport` 全量 mock + 培训防刷进度单测（新增测试文件必须加 `标签：[Dify组] [P1]`） |

---

## 四、本组依赖开关与第一个 PR

```bash
DIFY_BASE_URL=http://127.0.0.1/v1
DIFY_API_KEY=app-xxx
DIFY_TIMEOUT=60
DIFY_MAX_RETRIES=2
DIFY_APP_ID_CHAT=
DIFY_APP_ID_RESUME=
DIFY_DATASET_ID=
```

**第一个 PR（D2–D3 即可开）**：`feat(dify): Dify 适配器与 SSE 解析`
内容：`adapters/dify/dify_adapter.py`、`adapters/dify/sse.py` + `tests/test_dify.py`（`httpx.MockTransport` 全 mock），预计 300 行。

**边界约定**：Dify 负责 Prompt 管理 / 低代码应用发布 / 知识库运维；**业务 Agent、RAG 精度、NL2SQL 安全仍由 1/2/3 组自建**。两者重叠的部分在 D7–D8 评审后写入文档，不得双写。
