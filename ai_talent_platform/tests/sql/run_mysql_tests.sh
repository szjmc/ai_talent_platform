#!/usr/bin/env bash
# ============================================================================
# run_mysql_tests.sh — 一键执行 MySQL 业务库 SQL 测试套件
# 标签：[架构组] [P1]（CI/本地统一入口；纯 SQL，不依赖 Python）
#
# 用法：
#   bash tests/sql/run_mysql_tests.sh                 # 库已建好表时
#   BOOTSTRAP=1 bash tests/sql/run_mysql_tests.sh     # 顺带建库+加载 DDL 快照
#
# 连接参数（环境变量，默认对齐 docker-compose.yml / settings.py）：
#   MYSQL_HOST(127.0.0.1) MYSQL_PORT(3306) MYSQL_USER(root) MYSQL_PASSWORD(root)
#   MYSQL_DB(ai_talent_platform_test)
#
# 说明：01-04 与 99 在同一个 mysql 会话内顺序执行（存储过程/结果表需跨文件存活）。
#       任一语句报错或存在失败用例时，脚本以非零码退出（CI 变红）。
# ============================================================================
set -euo pipefail

HOST="${MYSQL_HOST:-127.0.0.1}"
PORT="${MYSQL_PORT:-3306}"
USER="${MYSQL_USER:-root}"
PASSWORD="${MYSQL_PASSWORD:-root}"
DB="${MYSQL_DB:-ai_talent_platform_test}"
BOOTSTRAP="${BOOTSTRAP:-0}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MYSQL_PWD="$PASSWORD"

MYSQL=(mysql --host="$HOST" --port="$PORT" --user="$USER" --default-character-set=utf8mb4 --comments)

if [[ "$BOOTSTRAP" == "1" ]]; then
  echo ">> 建库（若不存在）：$DB"
  "${MYSQL[@]}" -e "CREATE DATABASE IF NOT EXISTS \`$DB\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  echo ">> 加载 DDL 快照：schema/mysql_schema.sql"
  "${MYSQL[@]}" --database="$DB" < "$DIR/schema/mysql_schema.sql"
fi

echo ">> 执行 MySQL SQL 测试（库：$DB）"
cat "$DIR/mysql/01_schema_assertions.sql" \
    "$DIR/mysql/02_seed_test_data.sql" \
    "$DIR/mysql/03_constraint_tests.sql" \
    "$DIR/mysql/04_business_queries.sql" \
    "$DIR/mysql/99_cleanup.sql" \
  | "${MYSQL[@]}" --database="$DB"

echo ">> MySQL SQL 测试完成"
