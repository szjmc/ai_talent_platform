# 数据库 SQL 测试套件

> 标签：[架构组]+[Vanna组] [P1]
> 直接以 **SQL 文件** 交付的数据库测试用例，覆盖 MySQL 业务库与 PgSQL 分析库。
> 不依赖 Python/pytest，用 `mysql` / `psql` 客户端即可运行，也已接入 GitHub Actions CI。

## 1. 覆盖范围

| 数据库 | 实例库名 | 测试库名 | 表数 | 套件 |
|---|---|---|---|---|
| MySQL 8.0（业务库） | `ai_talent_platform` | `ai_talent_platform_test` | 25 | `mysql/` |
| PostgreSQL 16（分析库） | `ai_talent_analytics` | `ai_talent_analytics_test` | 6（4 张 RANGE 分区 + 默认分区） | `pgsql/` |

结构以 `app/models/*`（ORM）为唯一事实来源；`schema/*.sql` 是由 ORM 渲染的 DDL 快照，
模型变更后需重新生成（见 §6）。

## 2. 目录结构

```
tests/sql/
├── README.md                  # 本文件
├── run_mysql_tests.sh         # MySQL 一键入口（CI/本地）
├── run_pgsql_tests.sh         # PgSQL 一键入口（CI/本地）
├── schema/
│   ├── mysql_schema.sql       # 25 表 DDL 快照（不含 CREATE DATABASE）
│   └── pgsql_schema.sql       # 6 表 + 4 默认分区 DDL 快照
├── mysql/
│   ├── 01_schema_assertions.sql   # [P1] 135 项结构断言 + 护栏/结果表/清理过程
│   ├── 02_seed_test_data.sql      # [P1] 25 表种子数据（幂等）
│   ├── 03_constraint_tests.sql    # [P0] 唯一/外键/非空/默认值/乐观锁/级联
│   ├── 04_business_queries.sql    # [P1] RBAC/组织/JSON/匹配/测评/培训/知识库/审计
│   └── 99_cleanup.sql             # 汇总报告 + 失败即报错 + 清理痕迹
└── pgsql/
    ├── 01_schema_assertions.sql   # [P1] 43 项结构断言（含分区/Identity/JSONB/数组）
    ├── 02_seed_test_data.sql      # [P1] 6 表种子数据（幂等）
    ├── 03_analytics_queries.sql   # [P1] NL2SQL 高频分析查询（聚合/窗口/JSONB/数组/upsert）
    ├── 04_constraint_tests.sql    # [P0] CHECK/唯一/非空/分区路由/Identity/边界值
    └── 99_cleanup.sql             # 汇总报告 + 失败即报错 + 清理痕迹
```

执行顺序固定为 `01 → 02 → 03 → 04 → 99`，且必须在**同一会话**内完成
（MySQL 的存储过程、PgSQL 的函数与 `sqltest_results` 结果表需跨文件存活）。
两个 runner 脚本已保证这一点。

## 3. 快速开始

### 方式 A：Docker（推荐本地）

```bash
cd ai_talent_platform
docker compose up -d mysql postgres        # 启动 MySQL 8.0 + PostgreSQL 16

# MySQL：建测试库 + 加载 DDL + 跑全套
BOOTSTRAP=1 MYSQL_PASSWORD=root bash tests/sql/run_mysql_tests.sh

# PgSQL：建测试库 + 加载 DDL + 跑全套
BOOTSTRAP=1 PGPASSWORD=postgres bash tests/sql/run_pgsql_tests.sh
```

### 方式 B：已有实例

```bash
# 库和表已就绪时，去掉 BOOTSTRAP，直接跑：
MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=*** \
  MYSQL_DB=ai_talent_platform_test bash tests/sql/run_mysql_tests.sh

PGHOST=127.0.0.1 PGUSER=postgres PGPASSWORD=*** \
  PGDATABASE=ai_talent_analytics_test bash tests/sql/run_pgsql_tests.sh
```

### 方式 C：GitHub Actions CI

`.github/workflows/sql-tests.yml` 在改动 `tests/sql/**` 或 `app/models/**` 时自动触发：
拉起 `mysql:8.0` 与 `postgres:16` 服务容器，`BOOTSTRAP=1` 加载 DDL 快照后执行两套测试，
任一失败用例都会让对应 job 变红。也可在 Actions 页手动 `workflow_dispatch`。

## 4. 连接参数（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 地址 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `root` | 对齐 docker-compose |
| `MYSQL_DB` | `ai_talent_platform_test` | 测试库名（须含 `test`） |
| `PGHOST` / `PGPORT` | `127.0.0.1` / `5432` | PgSQL 地址 |
| `PGUSER` / `PGPASSWORD` | `postgres` / `postgres` | 对齐 docker-compose |
| `PGDATABASE` | `ai_talent_analytics_test` | 测试库名（须含 `test`） |
| `BOOTSTRAP` | `0` | `1` 时先建库并加载 `schema/*.sql` |

## 5. 设计约定与安全护栏

- **测试库护栏**：MySQL `01` 用 `sqltest_guard()`（`DATABASE()` 不含 `test` 即 `SIGNAL` 中止）；
  PgSQL `01` 用 `DO` 块（`current_database()` 不含 `test` 即 `RAISE`）。
  PgSQL 如确需在非 test 库运行（例如内嵌引擎），可显式 `SET sqltest.allow_non_test_db = 'on'` 放行。
- **数据隔离**：所有测试数据固定使用 ID 段 **900000–900999**、名称前缀 **`sqltest_`**，
  与业务数据天然隔离；`99` 按该段精确清理，绝不触碰其它数据。
- **幂等可重跑**：`02` 先清理再插入；`04`（MySQL）会重建自身夹具，因此可在 `03` 之后独立重跑。
- **结果落表**：每条用例写入 `sqltest_results(test_id, test_name, passed, detail, ran_at)`；
  `99` 输出分套件汇总 + 失败明细，并在存在失败时抛错（`mysql`/`psql` 非零退出 → CI 红）。
- **失败保留现场**：MySQL `99` 的 `sqltest_fail_if_any()` 在清理**之前** `SIGNAL`，
  因此失败时测试数据与结果表会保留，便于排查；全部通过才清理。
- **负向用例**：MySQL 用 `sqltest_expect_error(...)` 比对错误号（1062/1452/1048/1406/3140/1264/1451）；
  PgSQL 用同名函数比对 SQLSTATE（23514/23505/23502）。"必须失败"的语句若意外成功会记为 FAIL。

## 6. 维护：模型变更后如何同步

1. 改 `app/models/*`（MySQL）或 `app/models/analytics.py`（PgSQL）。
2. 重新生成 DDL 快照：
   - MySQL：用 SQLAlchemy `CreateTable/CreateIndex` 按 `mysql.dialect()` 渲染 `Base.metadata`，
     覆盖 `schema/mysql_schema.sql`（保留文件头注释）。
   - PgSQL：`python scripts/init_pgsql.py --print-ddl > schema/pgsql_schema.sql`（保留文件头）。
3. 若新增表/字段/约束，相应在 `01`（结构断言）、`02`（种子）、`03/04`（行为）补充用例。
4. 本地 `BOOTSTRAP=1` 跑通两套，再提交（CI 会再次校验）。

## 7. 用例编号速查

| 前缀 | 含义 | 优先级 |
|---|---|---|
| `SCH-` | 结构断言（表/字段/主键/唯一/外键/索引/默认值/类型/分区/Identity） | P1 |
| `DAT-` | 种子数据自检 | P1 |
| `CON-` | 约束行为（唯一/外键/非空/长度/JSON/数值/默认值/乐观锁/逻辑删除/级联/CHECK/分区路由） | P0 |
| `BIZ-` | MySQL 业务查询（RBAC/组织/技能 JSON/匹配/简历/测评/培训/知识库/会话/审计/分页） | P1 |
| `ANA-` | PgSQL 分析查询（快照趋势/宽表数组/测评窗口/培训行为/upsert/分区兜底） | P1 |
