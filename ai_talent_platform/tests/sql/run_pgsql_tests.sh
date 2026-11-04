#!/usr/bin/env bash
# ============================================================================
# run_pgsql_tests.sh — 一键执行 PgSQL 分析库 SQL 测试套件
# 标签：[架构组]+[Vanna组] [P1]（CI/本地统一入口；纯 SQL，不依赖 Python）
#
# 用法：
#   bash tests/sql/run_pgsql_tests.sh                 # 库已建好表时
#   BOOTSTRAP=1 bash tests/sql/run_pgsql_tests.sh     # 顺带建库+加载 DDL 快照
#
# 连接参数（环境变量，默认对齐 docker-compose.yml / settings.py）：
#   PGHOST(127.0.0.1) PGPORT(5432) PGUSER(postgres) PGPASSWORD(postgres)
#   PGDATABASE(ai_talent_analytics_test)
#
# 说明：01-04 与 99 通过 psql 多个 -f 在同一会话内执行（函数/结果表跨文件存活）。
#       -v ON_ERROR_STOP=1 保证任一错误（含 99 的失败即抛异常）非零退出。
#       护栏：库名需含 test，否则 01 直接 RAISE（除非 SET sqltest.allow_non_test_db=on）。
# ============================================================================
set -euo pipefail

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-postgres}"
export PGDATABASE="${PGDATABASE:-ai_talent_analytics_test}"
BOOTSTRAP="${BOOTSTRAP:-0}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PSQL=(psql -v ON_ERROR_STOP=1 --no-psqlrc -X)

if [[ "$BOOTSTRAP" == "1" ]]; then
  echo ">> 建库（若不存在）：$PGDATABASE"
  if ! "${PSQL[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PGDATABASE'" | grep -q 1; then
    "${PSQL[@]}" -d postgres -c "CREATE DATABASE \"$PGDATABASE\";"
  fi
  echo ">> 加载 DDL 快照：schema/pgsql_schema.sql"
  "${PSQL[@]}" -f "$DIR/schema/pgsql_schema.sql"
fi

echo ">> 执行 PgSQL SQL 测试（库：$PGDATABASE）"
"${PSQL[@]}" \
  -f "$DIR/pgsql/01_schema_assertions.sql" \
  -f "$DIR/pgsql/02_seed_test_data.sql" \
  -f "$DIR/pgsql/03_analytics_queries.sql" \
  -f "$DIR/pgsql/04_constraint_tests.sql" \
  -f "$DIR/pgsql/99_cleanup.sql"

echo ">> PgSQL SQL 测试完成"
