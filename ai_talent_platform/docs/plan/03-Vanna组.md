# 03. 3 组（Vanna.ai NL2SQL，4 人）— 20 个工作日计划

> 隶属：[AI 能力层分组开发计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md)
> 人力：4 人（NL-A 训练 / NL-B 安全 / NL-C 适配图表 / NL-D 接口宽表）

---

## 本组速览

- **组别**：3 组
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **每日 15 分钟站会**，每周五集成日；D12 / D20 为大节点验收
- **跨组契约不可私改**：LLM / Skill / Agent / 向量库 / NL2SQL / SSE 见 [总计划 §八](../team_sprint_plan.md)
- **代码骨架与验收命令**：[分组代码实现规范](../team_code_spec.md)
- **组内约定**：每个 PR 描述必须包含「改了什么 / 表结构有无变更 / 需要谁 review / 如何验证」

---

## 一、人员分工

| 角色 | 负责 |
| --- | --- |
| NL-A（训练） | `scripts/train_vanna.py`、DDL/术语/示例 SQL 语料、Vanna 服务化 |
| NL-B（安全） | `sql_validator`（语法、白名单表、只读、LIMIT、数据范围注入） |
| NL-C（适配与图表） | `vanna_adapter.ask`、图表推断、insight 生成 |
| NL-D（业务接口） | `analytics_service`、`analytics_router`、导出、与 PgSQL 宽表 |

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
| `app/adapters/nl2sql/sql_validator.py` | `sqlglot` | `validate_select_sql(sql, allowed_tables)->str`、`inject_limit(sql, max_rows=1000)`、`inject_data_scope(sql, scope_sql)`；禁止多语句/DDL/DML/系统表 |
| `app/adapters/nl2sql/vanna_adapter.py` | `vanna` | `ask(question, user_scope)->NL2SQLResponse{sql, columns, rows, chart, insight, elapsed_ms}`；内部固定顺序：generate → validate → execute(只读 PgSQL) → chart → insight |
| `scripts/train_vanna.py` | `vanna` | 导入 DDL（分析库 6 表）/ 指标口径文档 / 示例 SQL / 业务术语；`--reset` 重建 |
| `app/services/analytics_service.py` | — | `overview()`、`nl2sql_ask()`、`chart_config()`、`export()`、`dashboard()`；调 Service 层而非直连 SQL |
| `app/routers/analytics_router.py` | — | 6 个接口：`GET /analytics/overview`、`GET /analytics/distribution`、`GET /analytics/trend`、`POST /analytics/nl2sql`、`POST /analytics/report`、`GET /analytics/reports`，挂 `analytics:read` / `analytics:export` |
| PgSQL 宽表 DDL + 模型 | — | 6 张分析宽表（见 `docs/database_design.md` 第七节），只读账号 |
| `app/ai/skills/sql_skill.py` | — | （与架构组共建）`query_database(question)`，内部捕获 `SQLValidationError` → `BusinessRuleError` |
| `tests/test_nl2sql.py` | — | 校验器 30 条 + 适配器 mock 10 条 |

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
