"""
模块：app/adapters/nl2sql/sql_validator.py
职责：SQL 安全校验（NL2SQL 生成的 SQL 必须先过这里再执行）。

标签：[Vanna组] [P0]（安全红线：仅 SELECT / 表白名单 / 强制 LIMIT；被 [架构组] sql_query 技能依赖）

校验规则（缺一不可）：
1. 只允许单条 SELECT / WITH 开头查询（拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/
   TRUNCATE/CREATE/GRANT 等 DML/DDL/危险函数）；
2. 禁止注释符号（--、/* */、#）与分号多语句；
3. 禁止系统库/系统表（information_schema、pg_catalog、pg_*、mysql、performance_schema、sys）；
4. 表白名单：默认只允许分析库 6 张表（models.analytics.ANALYTICS_TABLE_NAMES），
   WITH 定义的 CTE 名称自动放行；
5. 强制 LIMIT：没有 LIMIT 时自动追加（max_rows，默认 200，上限 1000），
   已有 LIMIT 超限时收敛；
6. 权限注入：inject_data_scope 在「顶层 WHERE」追加数据范围条件（行级权限），
   未带 WHERE 时在 GROUP/HAVING/ORDER/LIMIT 前插入；
7. 校验失败抛 core.exceptions.BusinessRuleError，并记录被拒绝的 SQL（审计日志）。

实现说明：
- 词法级防护：先剥离字符串字面量再做关键字/表名识别，避免字面量误判；
  有 sqlglot 的环境可在后续接入 AST 级校验，本实现零第三方依赖、离线可用。
- 校验通过后仍应走 PgSQL 分析库只读连接（数据库层是最后一道防线）。

已完成：
    - [x] validate_select_sql(sql, allowed_tables, max_rows, apply_limit) -> str
    - [x] inject_limit(sql, max_rows) -> str
    - [x] inject_data_scope(sql, scope_sql) -> str
"""

from __future__ import annotations

import logging
import re

from app.config.settings import settings
from app.core.exceptions import BusinessRuleError
from app.models.analytics import ANALYTICS_TABLE_NAMES

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 200
MAX_ROWS_CEILING = 1000

# DML / DDL / 权限 / 事务 / 危险函数（在剥离字面量后的文本上匹配，词边界防误伤列名）
_FORBIDDEN_RE = re.compile(
    r"\b(?:insert|update|delete|merge|upsert|replace|drop|alter|truncate|create|"
    r"grant|revoke|call|execute|exec|copy|vacuum|comment|lock|prepare|deallocate|"
    r"explain|set|show|pragma|attach|detach|into\s+outfile|load\s+data|"
    r"sleep\s*\(|benchmark\s*\(|pg_sleep\s*\(|generate_series\s*\(|pg_read_file\s*\(|"
    r"pg_ls_dir\s*\(|lo_import\s*\(|lo_export\s*\()",
    re.IGNORECASE,
)
_SYSTEM_SCHEMA_RE = re.compile(
    r"\b(?:information_schema|pg_catalog|pg_stat\w*|pg_shadow|pg_settings|"
    r"mysql|performance_schema|sys)\s*\.",
    re.IGNORECASE,
)
_PG_INTERNAL_TABLE_RE = re.compile(r"\bpg_[a-z_0-9]+\b", re.IGNORECASE)
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*|#[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# FROM / JOIN 后的表引用（可带 schema 前缀、可带引号）
_TABLE_REF_RE = re.compile(
    r'\b(?:from|join)\s+((?:[A-Za-z_][\w]*\s*\.\s*)?[A-Za-z_][\w]*|"[^"]+")',
    re.IGNORECASE,
)
_CTE_RE = re.compile(
    r"\bwith\s+(?:recursive\s+)?([A-Za-z_]\w*)\s+as\s*\(|,\s*([A-Za-z_]\w*)\s+as\s*\(",
    re.IGNORECASE,
)
_TRAILING_LIMIT_RE = re.compile(
    r"\blimit\s+(\d+)(?:\s+offset\s+\d+)?\s*;?\s*$", re.IGNORECASE
)
_TOP_LEVEL_TOKEN_RE = re.compile(
    r"\b(?:where|group\s+by|having|order\s+by|limit|union)\b", re.IGNORECASE
)


def _reject(sql: str, reason: str) -> None:
    """统一拒绝出口：结构化业务异常 + 记录被拒 SQL（审计）。"""
    logger.warning("NL2SQL 校验拒绝：%s | sql=%.300s", reason, (sql or "").strip())
    raise BusinessRuleError(f"SQL 校验失败：{reason}")


def _strip_literals(text: str) -> str:
    """把单引号字符串字面量替换为等长的 '?'（位置与原文严格对齐，供插入定位）。

    双引号是 PostgreSQL 标识符（表/列名），保留不动。
    """
    return _STRING_LITERAL_RE.sub(lambda m: "?" * len(m.group(0)), text)


def _top_level_positions(stripped: str) -> list[tuple[int, str]]:
    """返回深度 0 处顶层子句关键字的位置与归一化文本（如 ('group by')）。"""
    positions: list[tuple[int, str]] = []
    depth = 0
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            match = _TOP_LEVEL_TOKEN_RE.match(stripped, i)
            if match and _is_word_boundary(stripped, i, match.end()):
                token = " ".join(match.group(0).lower().split())
                positions.append((i, token))
                i = match.end()
                continue
        i += 1
    return positions


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    before_ok = start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_")
    after_ok = end >= len(text) or not (text[end].isalnum() or text[end] == "_")
    return before_ok and after_ok


def _extract_tables(stripped: str) -> set[str]:
    """抽取 FROM/JOIN 后的表引用（跳过函数调用与子查询）。"""
    tables: set[str] = set()
    for match in _TABLE_REF_RE.finditer(stripped):
        after = stripped[match.end() :].lstrip()
        if after.startswith("("):  # 子查询或函数调用（如 unnest(x)）
            continue
        ref = match.group(1)
        if "." in ref:
            schema, name = [part.strip() for part in ref.split(".", 1)]
            allowed_schemas = {"public", str(settings.PGSQL_SCHEMA).lower()}
            if schema.strip('"').lower() not in allowed_schemas:
                continue  # 非本库 schema 的限定引用直接由系统表规则兜底
            tables.add(name.strip('"').lower())
        else:
            tables.add(ref.strip('"').lower())
    return tables


def _extract_cte_names(stripped: str) -> set[str]:
    names: set[str] = set()
    for match in _CTE_RE.finditer(stripped):
        name = match.group(1) or match.group(2)
        if name:
            names.add(name.lower())
    return names


def _clamp_rows(max_rows: int | None) -> int:
    try:
        value = int(max_rows or DEFAULT_MAX_ROWS)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_ROWS
    return max(1, min(value, MAX_ROWS_CEILING))


def inject_limit(sql: str, max_rows: int | None = DEFAULT_MAX_ROWS) -> str:
    """强制 LIMIT：缺省追加，超限收敛（不改写语句其余部分）。"""
    cap = _clamp_rows(max_rows)
    stripped = _strip_literals(sql).rstrip()
    match = _TRAILING_LIMIT_RE.search(stripped)
    if match:
        if int(match.group(1)) > cap:
            head = sql.rstrip()[: match.start()].rstrip()
            suffix = sql.rstrip()[match.end() :].rstrip()
            return f"{head} LIMIT {cap} {suffix}".rstrip()
        return sql.rstrip()
    body = sql.rstrip().rstrip(";").rstrip()
    return f"{body} LIMIT {cap}"


def inject_data_scope(sql: str, scope_sql: str) -> str:
    """把行级数据范围条件拼接到「顶层查询」（子查询 / CTE 内的 WHERE 不动）。

    :param scope_sql: 形如 ``org_path LIKE '%/12/%'`` 的条件片段；
                      空片段原样返回。
    """
    scope_sql = (scope_sql or "").strip().strip(";")
    if not scope_sql:
        return sql
    stripped = _strip_literals(sql)
    # 去掉尾部 LIMIT（外层注入后由 inject_limit 重新统一收敛）
    limit_match = _TRAILING_LIMIT_RE.search(stripped)
    body_sql = sql.rstrip()[: limit_match.start()].rstrip() if limit_match else sql.rstrip()
    body_stripped = _strip_literals(body_sql)

    where_pos: tuple[int, int] | None = None
    tokens = _top_level_positions(body_stripped)
    for index, (pos, token) in enumerate(tokens):
        if token == "where":
            where_pos = (pos, index)
            break

    if where_pos is not None:
        pos, index = where_pos
        # 顶层 WHERE 之后的第一个结束型子句（group by / having / order by / limit / union）
        ends = [
            p
            for p, t in tokens[index + 1 :]
            if t in {"group by", "having", "order by", "limit", "union"}
        ]
        insert_at = ends[0] if ends else len(body_stripped)
        return (
            f"{body_sql[:insert_at]} AND ({scope_sql}) {body_sql[insert_at:]}".rstrip()
        )

    # 没有顶层 WHERE：在第一个结束型子句前插入 WHERE，否则追加到末尾
    ends = [p for p, t in tokens if t in {"group by", "having", "order by", "limit", "union"}]
    insert_at = ends[0] if ends else len(body_stripped)
    return f"{body_sql[:insert_at]} WHERE ({scope_sql}) {body_sql[insert_at:]}".rstrip()


def validate_select_sql(
    sql: str,
    allowed_tables: set[str] | tuple[str, ...] | list[str] | None = None,
    *,
    max_rows: int | None = DEFAULT_MAX_ROWS,
    apply_limit: bool = True,
) -> str:
    """校验 NL2SQL 生成的 SQL，返回规范化（含 LIMIT）的只读查询。

    :param allowed_tables: 表白名单；None 时使用分析库 6 张表。
    :param max_rows: LIMIT 上限（同时作为超限收敛值）。
    :param apply_limit: 是否在返回前注入 LIMIT（数据范围注入流程中先关掉，
                        注入完成后再调用 inject_limit）。
    :raises BusinessRuleError: 任何一条规则不满足。
    """
    if not sql or not sql.strip():
        _reject(sql, "SQL 不能为空")

    stripped = _strip_literals(sql)

    if _BLOCK_COMMENT_RE.search(stripped) or _LINE_COMMENT_RE.search(stripped):
        _reject(sql, "禁止 SQL 注释（--、/* */、#）")

    if ";" in stripped.rstrip().rstrip(";"):
        _reject(sql, "禁止多语句（分号）")
    body = stripped.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", body, re.IGNORECASE):
        _reject(sql, "仅允许 SELECT / WITH 开头的只读查询")

    if _FORBIDDEN_RE.search(body):
        _reject(sql, "包含 DML/DDL/危险语句关键字")
    if _SYSTEM_SCHEMA_RE.search(body) or _PG_INTERNAL_TABLE_RE.search(body):
        _reject(sql, "禁止访问系统库 / 系统表")

    whitelist = {
        str(t).lower()
        for t in (allowed_tables if allowed_tables is not None else ANALYTICS_TABLE_NAMES)
    }
    cte_names = _extract_cte_names(body)
    referenced = _extract_tables(body)
    illegal = sorted(
        t for t in referenced if t not in whitelist and t not in cte_names
    )
    if illegal:
        _reject(sql, f"表白名单之外的表：{', '.join(illegal)}")

    if apply_limit:
        return inject_limit(sql, max_rows)
    return sql.rstrip().rstrip(";").rstrip()


__all__ = [
    "DEFAULT_MAX_ROWS",
    "MAX_ROWS_CEILING",
    "inject_data_scope",
    "inject_limit",
    "validate_select_sql",
]
