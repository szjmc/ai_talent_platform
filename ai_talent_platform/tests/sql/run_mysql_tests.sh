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
#   MYSQL_FORCE(0)  置 1 时 mysql 加 --force（遇错继续，便于一次性收集全部错误，仅诊断用）
#
# 说明：01-04 与 99 在同一个 mysql 会话内顺序执行（存储过程/结果表需跨文件存活）。
#       任一语句报错或存在失败用例时，脚本以非零码退出（CI 变红）。
#       失败时把 mysql 的 ERROR 行转成 GitHub ::error:: 注解，便于在 PR/API 直接查看。
# ============================================================================
set -uo pipefail

HOST="${MYSQL_HOST:-127.0.0.1}"
PORT="${MYSQL_PORT:-3306}"
USER="${MYSQL_USER:-root}"
PASSWORD="${MYSQL_PASSWORD:-root}"
DB="${MYSQL_DB:-ai_talent_platform_test}"
BOOTSTRAP="${BOOTSTRAP:-0}"
MYSQL_FORCE="${MYSQL_FORCE:-0}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MYSQL_PWD="$PASSWORD"

MYSQL=(mysql --host="$HOST" --port="$PORT" --user="$USER" --default-character-set=utf8mb4 --comments)
FORCE_ARGS=()
[[ "$MYSQL_FORCE" == "1" ]] && FORCE_ARGS=(--force)

LOGTMP="$(mktemp)"
trap 'rm -f "$LOGTMP"' EXIT

# 运行一条 mysql 命令并把输出落日志；失败时把 ERROR 行转成注解
run_mysql() {
  local label="$1"; shift
  echo ">> $label"
  "${MYSQL[@]}" "$@" > "$LOGTMP" 2>&1
  local rc=$?
  cat "$LOGTMP"
  if [[ $rc -ne 0 ]]; then
    echo "::group::$label 失败明细"
    grep -E 'ERROR|SIGNAL' "$LOGTMP" | head -60 | while IFS= read -r line; do
      echo "::error::${line}"
    done
    echo "::endgroup::"
  fi
  return $rc
}

if [[ "$BOOTSTRAP" == "1" ]]; then
  run_mysql "建库（若不存在）：$DB" \
    -e "CREATE DATABASE IF NOT EXISTS \`$DB\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" || exit $?
  run_mysql "加载 DDL 快照：schema/mysql_schema.sql" --database="$DB" < "$DIR/schema/mysql_schema.sql" || exit $?
fi

SQLTMP="$(mktemp)"
trap 'rm -f "$LOGTMP" "$SQLTMP"' EXIT
cat "$DIR/mysql/01_schema_assertions.sql" \
    "$DIR/mysql/02_seed_test_data.sql" \
    "$DIR/mysql/03_constraint_tests.sql" \
    "$DIR/mysql/04_business_queries.sql" \
    "$DIR/mysql/99_cleanup.sql" > "$SQLTMP"

run_mysql "执行 MySQL SQL 测试（库：$DB）" --database="$DB" "${FORCE_ARGS[@]}" < "$SQLTMP" || exit $?

echo ">> MySQL SQL 测试完成"
