# 03. 3 组（Vanna NL2SQL / 数据决策 + 在线智能测评，4 人）— 20 个工作日计划

> 隶属：[AI 能力层与业务层分组开发总计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md) ｜ **必读边界红线**：[五大研发组职责边界与防重复开发总规范 §6.3](../group_boundaries_and_tasks.md#63-3-组vanna-组-vanna组nl2sql统计看板--在线智能测评)
> 人力：4 人（NL-A 训练语料 + 在线测评模块 `assessments` / NL-B SQL 安全校验 / NL-C Vanna 适配与图表推断 / NL-D 看板导出接口与 PgSQL 宽表 ETL）

---

## 当前 MVP 实施记录（2026-11-04）

当前工作区已有单轮问数API、SQL AST校验/限行、可信范围、独立PgSQL只读执行、40条训练示例、五类图表配置、结构化解读，以及PDF/Word生成、报告列表和下载代码；接口状态见 [接口文档·分析模块](../接口文档.md#api-analytics)。NULL对齐、图名优先、负值绘图与绘图失败报错已有专项验证；报告渲染/写盘移出事件循环，表格注明返回行列与摘要范围。上述说明仅记录已有实现，不代表下方20日计划全部完成，亦不代表真实服务/前端/性能验收通过。

页面结果复用已按确认的方案A接入：公共Redis保存JSON结果，TTL复用REDIS_DEFAULT_TTL（默认300秒）；仅有问数/导出权限且data_scope=all的用户获得query_id/expires_at。报告按query_id读取本人有效结果，不调用模型或重查分析数据；question旧模式仍重新问数。身份、角色、组织或分析策略变化后拒绝使用旧快照；Redis故障时问数保留、快照明确降级，无进程内/文件回退。真实Redis联调尚未验收。

确定性分析已增加：学历/年龄/部门快照分布、技能逐项去重分布、测评得分汇总、月度人才存量趋势及两个显式快照的结构对比；同类多值OR、跨字段AND，仅接受实际数据源支持的筛选。按用户确认，月度存量同比/环比缺期/零分母返回无法计算，不含人才流动/流失。接口与边界见接口文档。新增3条技能/组合筛选/测评语料，默认示例由40增至43；SQL校验器仅增加受控UNNEST内置节点支持，其他函数及部门边界不放宽。真实库执行及真实模型准确率仍待验收。

培训完成率已按用户要求暂停，暂不扩表或新增迁移。匹配率已确认最新批次达标记录数/总记录数，默认建议70分；仓库说明按岗位matched_at选最新批次。用户已选择先由匹配/数据组提供公开数据接口，Vanna随后接入；本轮不修改匹配模块或直查MySQL，对接要求已补入接口文档。尚待实施或确认：ETL与真实分析数据、完整overview、匹配数据接口就绪后的接入、季度/年度及其他指标同比环比、完整行/字段权限、公共审计接入、部门范围快照、历史报告权限变化策略、报告任务化、Dify边界及联调。人才流动/流失按用户要求不在本次范围；线程隔离不等同于后台任务，也不能据此承诺5秒性能指标。

## 本组速览

- **组别**：3 组 `[Vanna组]`（标签：`标签：[Vanna组] [P0|P1]`，全仓现存 **16 个 `.py` 文件**）
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **⚠️ 已落地冻结底座警告（以下文件已由 `[架构组]` 100% 实现并通过单测，本组直接复用，严禁重写！）**：
  - ✅ `app/models/{analytics,assessment}.py`（PgSQL 6 张宽表 ORM 与 MySQL 测评 4 张表 ORM 均已落地）
  - ✅ `app/infrastructure/database/analytics_schema.py`、`scripts/init_pgsql.py`、`app/infrastructure/database/postgres.py`（`pg_session()` / `get_pg_session_factory()`）
  - ✅ `app/ai/skills/sql_skill.py`（`SqlQuerySkill` 已完成，**严禁修改**！本组 `VannaAdapter.ask(question, *, user_scope=..., max_rows=...)` 对齐其签名即可自动接通）
- **本组端到端核心边界（NL2SQL 数据决策 + 在线测评两大垂直闭环）**：
  1. 全平台唯一的 NL2SQL 安全校验器 `sql_validator.py` 与查询适配器 `vanna_adapter.py`；
  2. 全平台唯一的 MySQL → PgSQL 6 张分析宽表增量同步 ETL 脚本 `scripts/sync_analytics.py`（使用 `get_pg_session_factory()` + `ON CONFLICT DO UPDATE` 幂等提交）；
  3. 数据决策全模块接口 `analytics_service.py` + `schemas/analytics.py` + `analytics_router.py`（当 `PGSQL_ENABLED=false` 时看板自动降级查 MySQL 聚合；报告导出走 `[架构组] FileService` 落盘）；
  4. **在线人才智能测评闭环（`assessments`）**：`assessment_{repository,service,router}.py` + `schemas/assessment.py`（题库抽题、防泄密开考剔除 `answer`、超时检测与客观题规则判分、维度得分汇总供给 `fact_assessment_history`，主观题评分与能力报告通过 `await dispatch("assessment", ...)` 委托 `[Agent组]`）。

---

## 一、人员分工

| 角色 | 负责 |
| --- | --- |
| NL-A（训练 + 测评模块） | `scripts/train_vanna.py`（≥40 条语料）+ `assessment_{repository,service,router}.py` + `schemas/assessment.py` |
| NL-B（安全） | `sql_validator`（语法、白名单表、只读、LIMIT、数据范围注入） |
| NL-C（适配与图表） | `vanna_adapter.ask`、图表推断、insight 生成 |
| NL-D（看板接口与宽表 ETL） | `analytics_service`、`analytics_router`、`schemas/analytics.py`、导出、`scripts/sync_analytics.py` |

## 二、每日任务（D1–D20，每行末尾是当日验收标准）

| 天 | NL-A 训练 | NL-B 安全 | NL-C 适配/图表 | NL-D 接口/宽表 | 当日验收 |
| --- | --- | --- | --- | --- | --- |
| D1 | 盘点分析场景 20 条（人员结构、流失率、培训完成率…） | 列出可查表白名单：分析库 6 表（`fact_talent_snapshot` / `fact_assessment_history` / `fact_training_track` / `fact_behavior_event` / `dim_organization` / `dim_talent_wide`） | Vanna/模型选型确认（Vanna + qwen 或 OpenAI 兼容） | 与 1/2 组对齐 `analytics` 接口契约 | 场景清单 + 白名单签字 |
| D2 | DDL 语料准备（PgSQL 6 张宽表） | `validate_select_sql`：仅 SELECT、无子查询套 DDL、拒绝 `;` 多语句 | `vanna_adapter` 初始化（配置/训练数据加载） | PgSQL 宽表 6 张建表（配合数据库文档第七节） | 校验器单测 15 条（含注入样例） |
| D3 | 示例 SQL 语料 40 条 | `inject_limit`、`inject_data_scope`（部门/角色过滤） | `generate_sql(question, context)` 跑通 | 宽表 ETL 脚本 1（人员结构） | 单表问数可生成正确 SQL |
| D4 | 业务术语词典（「骨干」「流失率」「达标率」） | 只读连接（PgSQL 只读账号）验证 | SQL → 执行 → columns/rows | 宽表 ETL 2（培训） | 端到端：问题→SQL→数据 |
| D5 | 训练 1 轮（DDL+SQL+术语），记录准确率基线 | 表名/字段白名单从 information_schema 动态取 | 图表类型推断（时间→折线、枚举→柱状、占比→饼图） | 宽表 ETL 3（测评） | 20 条场景准确率 ≥ 60% |
| D6 | 错误 SQL 收集 + 修正（few-shot 回灌） | `validate` 覆盖率与漏测补齐 | `insight` 生成（LLM 解读数据） | 宽表 ETL 4（匹配） | 准确率 ≥ 75% |
| D7 | Vanna 训练持久化（chromadb/pgvector 存储位置评审） | 行级权限：非管理员强制带 `organization_id` 条件 | 图表配置输出规范 `{type,x_field,y_field,title,series}` | 宽表 ETL 5–6（流失/人才库） | 图表配置被前端 Demo 渲染 |
| D8 | 与 Skills 联调：`sql_skill.query_database` | 与架构组安全评审（SQL 逃逸） | `VannaAdapter.ask(question, user_scope) -> NL2SQLResponse` | `analytics_service` 方法骨架 | `sql_skill` 可用 |
| D9 | 训练数据版本化（v1 快照） | 超时/大查询拦截（扫描行数上限） | 多轮追问（上下文表结构） | `analytics_router`：`overview/chat/chart/export` | 接口 `/docs` 可见 |
| D10 | 准确率 ≥ 85% 冲刺 | 审计：每次 NL2SQL 记录 question/sql/耗时/用户 | 结果缓存（同问同权限 5 分钟） | 导出（Excel/CSV，异步任务） | 审计记录可查 |
| D11 | 补充行业口径（HR 指标定义） | 恶意问法演练 20 条（删库/拖库/越权） | 失败兜底：SQL 生成失败 → 提示 + 推荐相近问题 | `analytics_service` 全方法 | 恶意问法 0 命中 |
| D12 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 报告合并 |
| D13 | 增量训练流程文档 | 校验器 fuzz 测试 | 图表推断边界（空数据/单列） | 看板接口（预置 6 个） | 空数据不报 500 |
| D14 | 训练评估集（50 题）固化 | 性能：校验开销 < 10ms | Vanna 并发（Semaphore=5） | 大查询异步化（任务号轮询） | P95 问数 < 8s |
| D15 | 准确率报告 | 审计与告警（异常 SQL 告警） | insight 质量抽检 20 条 | 导出限流 | 报告 |
| D16 | 与 4 组联调：Dify 也可做 NL2SQL 的边界划分 | 降级：Vanna 不可用时直接 LLM 生成 SQL 仍过校验器 | 降级演练 | 降级演练 | 无 500 |
| D17 | 文档：如何新增训练语料 | 文档：安全管理说明 | 文档：图表规范 | 文档：宽表口径 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | `pytest tests/test_nl2sql.py` |
| D19 | 演示：自然语言问数 | 演示：注入被拦截 | 演示：图表 + 解读 | 演示：导出 | 演示脚本 |
| D20 | 终验 | 终验 | 终验 | 终验 | 总计划 §九 验收清单 |

## 三、本组要写的代码（文件级）

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/adapters/nl2sql/sql_validator.py` | ✅ 已实现（零依赖词法校验，`sqlglot` 可选未启用） | `validate_select_sql(sql, allowed_tables, max_rows, apply_limit)->str`、`inject_limit(sql, max_rows=200)`、`inject_data_scope(sql, scope_sql)`；禁止多语句/DDL/DML/系统表 |
| `app/adapters/nl2sql/vanna_adapter.py` | ✅ 已实现（vanna 未安装时「依赖未就绪」降级） | `ask(question, user_scope, max_rows)->NL2SQLResponse{sql, columns, rows, chart, insight, elapsed_ms}`；内部固定顺序：generate → validate → 行级数据范围 → execute(只读 PgSQL) → chart → insight |
| `scripts/train_vanna.py` | `vanna` | 导入 DDL（分析库 6 表）/ 指标口径文档 / 示例 SQL / 业务术语；`--reset` 重建 |
| `app/services/analytics_service.py` | ✅ 已实现 | `overview()`、`distribution()`、`trend()`、`nl2sql()`、`generate_report()`(+`reports()`)；调 Service 层而非直连 SQL |
| `app/routers/analytics_router.py` | ✅ 已实现 | 6 个接口：`GET /analytics/overview`、`GET /analytics/distribution`、`GET /analytics/trend`、`POST /analytics/nl2sql`、`POST /analytics/report`、`GET /analytics/reports`，挂 `analytics:read` / `analytics:export` |
| `app/models/analytics.py` + `scripts/init_pgsql.py` | ✅ 已实现 | 6 张分析宽表 ORM 与分区建表已落地（只读复用，严禁重建）；本组新增 `scripts/sync_analytics.py` 实现 MySQL → PgSQL 幂等增量同步 ETL |
| `app/ai/skills/sql_skill.py` | ✅ 架构组已实现 | `SqlQuerySkill` 已完成，只读无需修改；内部自动动态导入本组 `VannaAdapter.ask(question=..., user_scope=..., max_rows=...)` |
| `app/repositories/assessment_repository.py`、`app/schemas/assessment.py`、`app/services/assessment_service.py`、`app/routers/assessment_router.py` | — | **在线智能测评闭环（P1）**：`QuestionForCandidateSchema` 物理剔除 `answer` 防泄密、超时标记 `overtime`、单选/多选规则计分、调 `dispatch("assessment")` 生成主观题评分与报告、联动 `[RAG组] TalentService` 更新评级，实现 `/assessments` 下 6 个接口 |
| `tests/test_analytics_channel.py`（✅，含校验器/适配器/服务/ETL/工作流全链路） | — | 校验器安全用例 + 适配器 mock 链路 + 概览/分布/趋势/报告任务单测（新增测试文件必须加 `标签：[Vanna组] [P1]`） |

---

## 四、本组依赖开关与第一个 PR

```bash
LLM_PROVIDER=qwen
LLM_API_KEY=sk-xxx
PGSQL_ENABLED=true
PGSQL_HOST=127.0.0.1
PGSQL_DB=ai_talent_analytics
NL2SQL_MAX_ROWS=1000
NL2SQL_TIMEOUT_SECONDS=30
```

**第一个 PR（D2–D3 即可开，且应最先合并）**：`feat(nl2sql): SQL 安全校验器`
内容：`adapters/nl2sql/sql_validator.py` + 30 条反例测试（DDL/DML/多语句/系统表/非白名单表/LIMIT 爆炸），预计 250 行。**安全先行，任何 NL2SQL 链路都必须先过这个 PR。**

**分析库只读账号**：NL2SQL 只连 `PGSQL_*` 的只读账号，**禁止**打 MySQL 业务主库。
