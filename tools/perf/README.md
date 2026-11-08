# 接口压测 / APM 日志统计 / 数据库性能探针

本目录提供可执行脚本，用于生成**每个接口**的压测报告、APM/日志统计、数据库性能/慢查询/连接池/索引命中探针结果。

> 原则：不伪造结果。没有授权测试环境或数据库连接时，只生成执行计划、探针 SQL、连接检查失败原因或明确标注的 synthetic self-test。历史功能巡检日志不等于本次压测。

## 目录脚本

| 脚本 | 用途 |
|---|---|
| `inventory.py` | 从 FastAPI router 源码静态提取接口清单，不导入应用、不连数据库 |
| `scenarios.py` | 为每个接口生成压测场景骨架和 `fixtures.example.json` |
| `load_run.py` | 每接口 HTTP 压测/计划/self-test，输出 JSON/CSV/Markdown/Word |
| `apm_log_stats.py` | 解析 `app.access` 访问日志、历史 diagnose 日志、uvicorn access，按接口统计错误率/延迟分位/慢 trace |
| `db_perf.py` | MySQL/PgSQL 只读探针：慢 SQL、大表/慢数据、连接池、索引使用/命中代理、Buffer Pool/缓存命中、锁/临时表 |
| `report_docx.py` | Word 报告生成 |
| `run_all.sh` | 一键编排：清单 → 场景 → 计划/压测 → APM → DB 探针 |

## 快速开始

```bash
python -m venv .venv-perf
.venv-perf/bin/pip install httpx pymysql psycopg2-binary sqlalchemy python-docx

# 1. 静态接口清单与每接口场景
.venv-perf/bin/python tools/perf/inventory.py --out deliverables/perf/endpoints.json
.venv-perf/bin/python tools/perf/scenarios.py --endpoints deliverables/perf/endpoints.json --out deliverables/perf/scenarios.json

# 2. 无测试环境时：生成每接口执行计划/报告框架（不是实测）
.venv-perf/bin/python tools/perf/load_run.py --plan --output deliverables/perf/load_plan

# 3. 脚本自检：MockTransport，synthetic，不是项目接口实测
.venv-perf/bin/python tools/perf/load_run.py --self-test --requests 20 --concurrency 4 --output deliverables/perf/load_self_test

# 4. 接入授权测试环境后真实压测
export PERF_TOKEN='<test-access-token>'
# 或 export PERF_USERNAME=... PERF_PASSWORD=...
.venv-perf/bin/python tools/perf/load_run.py \
  --base-url https://test.example.com --allow-remote \
  --fixtures deliverables/perf/fixtures.json \
  --concurrency 10 --duration 30 --rps 5 \
  --output deliverables/perf/load_real

# 5. APM / 日志统计
.venv-perf/bin/python tools/perf/apm_log_stats.py ai_talent_platform/logs/app.log --format app --slow-ms 1000 --out deliverables/perf/apm --docx
# 历史功能巡检日志（无耗时，不计算延迟）
.venv-perf/bin/python tools/perf/apm_log_stats.py ai_talent_platform/docs/delivery/evidence/api_diagnose_20261104.log --format diagnose --out deliverables/perf/apm_historical --docx

# 6. 数据库探针 SQL + 连接检查
.venv-perf/bin/python tools/perf/db_perf.py --emit-sql --check --out deliverables/perf/db_check

# 7. 真实数据库性能/慢查询/索引/连接池
export PERF_MYSQL_DSN='mysql://readonly_user:***@mysql-host:3306/ai_talent_platform'
export PERF_PG_DSN='postgresql://readonly_user:***@pg-host:5432/ai_talent_analytics'
.venv-perf/bin/python tools/perf/db_perf.py \
  --check --pool-test --queries tools/perf/db_queries.example.json \
  --pool-concurrency 20 --pool-duration 10 --pool-size 10 --pool-max-overflow 20 \
  --out deliverables/perf/db_real
```

`db_queries.example.json` 由 `--emit-sql` 生成到输出目录；可复制为 `tools/perf/db_queries.example.json` 或自定义查询文件。

## 安全边界

- 默认只读：`GET` 和明确查询类 `POST` 可执行；写、副作用、认证、破坏性、流式接口需要显式开关。
- 破坏性接口必须同时 `--include-destructive --confirm-destructive YES`，并准备清理/恢复方案。
- 远程目标必须 `--allow-remote`，且只能对已授权测试环境压测。
- 数据库探针只读；`EXPLAIN` 默认不带 `ANALYZE`。`--time-queries` 会执行查询，只允许安全 SELECT。
- 不输出令牌/口令；夹具中的敏感值通过环境变量或受控文件注入。

## 输出物

```text
deliverables/perf/
  endpoints.json              接口清单
  scenarios.json              每接口场景
  fixtures.example.json       夹具模板
  load_plan/                  计划模式：每接口 report.md/result.json/samples.csv + 汇总 + Word
  load_self_test/             synthetic 自检结果
  load_real/                  真实压测结果（接入环境后）
  apm/ 或 apm_historical/     日志统计 JSON/CSV/MD/Word
  db_check/ 或 db_real/       DB 探针 JSON/CSV/MD/Word + mysql_probes.sql + pgsql_probes.sql
```

## 指标口径

- 延迟：客户端端到端毫秒；如响应头存在 `X-Process-Time-Ms`，同时记录服务端处理耗时。
- 分位：P50/P75/P90/P95/P99/Max；错误率按 HTTP 非 2xx 或统一响应 `code` 非 0/200 计算。
- APM：优先解析 `app.access` 的 `METHOD PATH -> STATUS | ms | ip=`；无耗时字段的历史日志只统计次数/状态，不编造延迟。
- MySQL 索引命中：使用 `performance_schema` 索引 IO、`sys.schema_unused_indexes`、Handler 读取代理指标；Handler 比例不是精确命中率。
- PostgreSQL 索引命中：`pg_stat_user_indexes.idx_scan`、`pg_statio_user_indexes` 缓存命中、`seq_scan/idx_scan` 比例。
- 连接池：SQLAlchemy `pool_size/max_overflow/pool_timeout/pool_recycle` 下并发 `SELECT 1`，统计连接获取耗时、查询耗时、错误和 pool.status。
