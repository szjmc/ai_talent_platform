"""SQL AST 校验与范围过滤：只读六表、函数白名单、逐个物理表隔离、强制限行。

标签：[Vanna组] [P0]（安全红线：仅 SELECT / 表白名单 / 强制 LIMIT；被 [架构组] sql_query 技能依赖）

校验规则（缺一不可）：
1. 只允许单条 SELECT（拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT 等）；
2. 禁止注释符号（--、/* */）与分号多语句；
3. 禁止系统库/系统表（information_schema、mysql、performance_schema、sys）；
4. 表白名单：只允许查询业务表（talents、positions、organizations、assessments...）；
5. 强制 LIMIT：没有 LIMIT 时自动追加（settings 或调用方指定，默认 200）；
6. 权限注入：追加数据范围条件（行级权限），由调用方传入 where 片段；
7. 字段级脱敏：手机号 / 身份证等字段查询时替换为脱敏表达式（可选）。
实现要点：
- 用 sqlglot 或正则做语法级校验（建议 sqlglot，能解析 AST）；
- 校验失败抛 core.exceptions.BusinessRuleError，并记录被拒绝的 SQL（审计）；
- 校验通过后仍在「只读账号」的连接上执行（数据库层的最后一道防线）。
待实现清单：
    - [ ] validate_select_sql(sql, allowed_tables) -> str（返回规范化 SQL）
    - [ ] inject_limit(sql, max_rows)
    - [ ] inject_data_scope(sql, scope_sql)
=========================================================================
权限范围为服务端生成的字典，不接收客户端 SQL 片段。依赖 sqlglot 延迟导入，
缺少依赖时应用仍可启动；任何校验失败都不得进入查询执行器。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.config.settings import settings
from app.core.exceptions import AIError, BusinessRuleError, PermissionDeniedError
from app.core.permissions import get_data_scope, has_permission

# 只开放分析库六表；MySQL 主库表和系统表均不属于问数数据源。
ALLOWED_TABLES = frozenset({
    "fact_talent_snapshot", "fact_assessment_history", "fact_training_track",
    "fact_behavior_event", "dim_organization", "dim_talent_wide",
})
# 有意采用封闭函数集合；新增函数必须有对应测试。
SAFE_FUNCTIONS = frozenset({
    "AND", "OR", "NOT", "TIMESTAMP_TRUNC", "COUNT", "SUM", "AVG", "MIN", "MAX", "ABS", "ROUND", "FLOOR", "CEIL",
    "COALESCE", "NULLIF", "CAST", "TRY_CAST", "EXTRACT", "DATE_TRUNC",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "LOWER", "UPPER", "LENGTH",
    "CHAR_LENGTH", "SUBSTRING", "TRIM", "CONCAT", "ARRAY_SIZE",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "IF", "CASE", "UNNEST",
})


class SQLValidationError(BusinessRuleError):
    """生成的 SQL 不符合只读查询约束。"""

    message = "SQL 校验未通过"


def _parser():
    """延迟取得解析器、AST 节点类型和作用域遍历器，未安装时给出业务错误。"""
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope
    except ImportError as exc:
        raise AIError("NL2SQL 依赖未就绪：请安装项目声明的 sqlglot") from exc
    return sqlglot, exp, traverse_scope


def _schema() -> str:
    """确保问数 schema 与分析库配置一致，并可安全用作服务端 SQL 标识符。"""
    value = settings.NL2SQL_ALLOWED_SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value) or value != settings.PGSQL_SCHEMA:
        raise AIError("NL2SQL_ALLOWED_SCHEMA 必须与合法的 PGSQL_SCHEMA 一致")
    return value


def _parse(sql: str):
    """把单条 PostgreSQL 查询解析成 AST（语法树），拒绝空值/注释/多语句。

    这里只检查输入外形和根节点；表、函数和写操作还须在后续全树检查。
    SQL 能解析不代表数据库一定能执行，也不表示它已通过权限校验。
    """
    sqlglot, exp, _ = _parser()
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise SQLValidationError("SQL 为空或超过长度限制")
    if any(token in sql for token in ("--", "/*", "*/")):
        raise SQLValidationError("SQL 不允许注释")
    # Vanna 常输出一个末尾分号；其他分号一律拒绝（包括多语句）。
    sql = sql.strip().removesuffix(";").rstrip()
    if ";" in sql:
        raise SQLValidationError("只允许单条 SELECT")
    try:
        items = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError("SQL 语法无法解析") from exc
    if len(items) != 1 or not isinstance(items[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError("只允许 SELECT / WITH 查询")
    return items[0]


def _physical_tables(tree):
    """找出各个查询作用域中的真实数据表，区分物理表与 CTE/派生表别名。

    例如 WITH t AS (...) 中的 t 是查询别名，真正需要过滤的是内部的物理表。
    同一物理表在不同查询位置出现时，需要分别校验和过滤。
    """
    _, exp, traverse_scope = _parser()
    found = []
    try:
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    found.append(source)
    except Exception as exc:
        raise SQLValidationError("查询表作用域无法解析") from exc
    return found


def validate_select_sql(sql: str, allowed_tables: set[str] | None = None) -> str:
    """检查整棵语法树并返回带明确 schema 的规范化 SQL。

    覆盖 CTE（WITH临时结果）、UNION（结果合并）及嵌套查询，拒绝写操作、
    未授权函数/类型和非白名单表。allowed_tables 只可缩小默认六表范围。
    失败抛 SQLValidationError；通过后仍须注入用户范围、限行并使用只读连接。
    """
    _, exp, _ = _parser()
    tree = _parse(sql)
    schema = _schema()
    # 与默认白名单取交集，调用方不能通过传参开放业务主库或额外表。
    allowed = ALLOWED_TABLES if allowed_tables is None else ALLOWED_TABLES.intersection(allowed_tables)
    forbidden = {"Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command",
                 "Into", "Lock", "Copy", "Transaction", "Set", "Use", "Grant",
                 "Revoke", "Merge", "Lateral", "TableSample", "Values", "Dot",
                 "Parameter", "Placeholder", "SessionParameter", "Pragma"}
    # 检查所有层级，不能只看开头是 SELECT 就信任整条 SQL。
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise SQLValidationError("查询包含不允许的操作")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SQLValidationError("MVP 不支持递归 CTE")
        # SELECT 中的函数也可能产生副作用，因此函数同样采用白名单。
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            # sqlglot将SELECT中的PgSQL UNNEST规范化为Explode节点；只映射该
            # 具体内置节点，不把未知的匿名EXPLODE函数加入白名单。
            if type(node) is exp.Explode:
                name = "UNNEST"
            if name not in SAFE_FUNCTIONS:
                raise SQLValidationError(f"查询使用了未授权函数{name}")
        # 类型转换也限制在明确范围内，不接受任意自定义/系统对象类型。
        if isinstance(node, exp.DataType):
            if node.sql(dialect="postgres").upper().split("(")[0] not in {
                "TEXT", "VARCHAR", "CHAR", "INT", "INTEGER", "BIGINT", "SMALLINT",
                "DECIMAL", "NUMERIC", "DOUBLE PRECISION", "REAL", "FLOAT", "BOOLEAN",
                "DATE", "TIMESTAMP", "TIMESTAMPTZ", "INTERVAL",
            }:
                raise SQLValidationError("查询使用了未授权类型转换")
    tables = _physical_tables(tree)
    if not tables:
        raise SQLValidationError("查询必须引用分析白名单表")
    for table in tables:
        if not isinstance(table.this, exp.Identifier) or table.name not in allowed:
            raise SQLValidationError("查询引用了非白名单表")
        if table.catalog or (table.db and table.db != schema):
            raise SQLValidationError("禁止跨 schema / 数据库访问")
        # 始终限定 schema，不能依赖 search_path 解析同名临时表。
        table.set("db", exp.to_identifier(schema))
    return tree.sql(dialect="postgres")


def checked_scope(user_scope: Mapping[str, Any] | None) -> dict[str, Any]:
    """验证身份并重新计算权限范围，返回规范化的内部范围字典。

    user_scope 应来自 Service/Skill 的可信上下文；不接受用户输入的范围 SQL。
    data_scope 以角色配置为准，不信任传入的同名字段。本人映射未实现、
    身份无效或受限角色缺部门时抛 PermissionDeniedError，不放宽到全量。
    """
    if not isinstance(user_scope, Mapping) or type(user_scope.get("user_id")) is not int or user_scope["user_id"] <= 0:
        raise PermissionDeniedError("问数需要已认证用户上下文")
    # 菜单/接口权限与行级数据范围是两回事：先确认能问数，再计算能看哪些行。
    roles = list(user_scope.get("roles") or [])
    if not has_permission(roles, "analytics:read"):
        raise PermissionDeniedError("缺少权限：analytics:read")
    scope = get_data_scope(roles)
    org_id = user_scope.get("organization_id")
    if scope == "self":
        raise PermissionDeniedError("尚未配置账号与人才档案映射，暂不支持本人范围问数")
    if scope != "all" and (type(org_id) is not int or org_id <= 0):
        raise PermissionDeniedError("部门范围问数缺少有效部门归属")
    return {"user_id": user_scope["user_id"], "roles": roles, "data_scope": scope,
            "organization_id": org_id}


def inject_data_scope(sql: str, user_scope: Mapping[str, Any] | None) -> str:
    """每个物理表引用替换为受限派生表，覆盖 JOIN/CTE/UNION/子查询。

    MVP 的部门问数仅开放有明确部门键的快照和部门维表；其他表缺少可靠的
    账号/部门映射，拒绝执行而不是猜测 org_path 或 user_id == talent_id。
    """
    sqlglot, exp, _ = _parser()
    scope = checked_scope(user_scope)
    tree = _parse(validate_select_sql(sql))
    if scope["data_scope"] == "all":
        return tree.sql(dialect="postgres")
    schema = _schema()
    # checked_scope 已确认受限范围的部门 ID 是正整数，不能带入任意 SQL 字符串。
    org_id = scope["organization_id"]
    permitted = f"SELECT org_id FROM {schema}.dim_organization WHERE org_id = {org_id}"
    # path 只包含祖先ID：部门3在根部门1下时path为/1/，子部门以/1/3/开头。
    # 以斜杠分隔并加 %，避免部门3误匹配成部门30。
    if scope["data_scope"] == "dept_sub":
        permitted += (f" OR path LIKE (SELECT path || '{org_id}/%' FROM {schema}.dim_organization "
                      f"WHERE org_id = {org_id})")
    for table in _physical_tables(tree):
        if table.name not in {"fact_talent_snapshot", "dim_organization"}:
            raise PermissionDeniedError("此分析表尚无可靠部门映射，MVP 仅支持部门快照和部门维表的范围查询")
        key = "organization_id" if table.name == "fact_talent_snapshot" else "org_id"
        # 例如 FROM fact_talent_snapshot f 改写为
        # FROM (SELECT * FROM fact_talent_snapshot WHERE 范围条件) f。
        # 保留别名，使外层 JOIN 和聚合只看到授权行；OR/外连接不能跳过此内层过滤。
        alias = table.alias_or_name
        source = table.copy()
        source.set("alias", None)
        predicate = sqlglot.parse_one(f"{key} IN ({permitted})", read="postgres")
        filtered = exp.select("*").from_(source).where(predicate).subquery(alias)
        table.replace(filtered)
    # 派生表已用原表名作为别名；去掉原始 schema 列限定以避免失效引用。
    for column in tree.find_all(exp.Column):
        if column.db == schema:
            column.set("db", None)
    return tree.sql(dialect="postgres")


def inject_limit(sql: str, max_rows: int = 1000) -> str:
    """在原查询外再套 SELECT ... LIMIT，限制最终返回行数。

    UNION 和已有超大 LIMIT 也会受约束；这不是限制扫描行数/聚合成本，
    数据库侧仍需只读权限和 statement_timeout。此方法本身不替代安全/权限检查。
    """
    _, exp, _ = _parser()
    if type(max_rows) is not int or not 1 <= max_rows <= 1000:
        raise SQLValidationError("max_rows 必须在 1—1000 之间")
    tree = _parse(sql)
    return exp.select("*").from_(tree.subquery("nl2sql_result")).limit(max_rows).sql(dialect="postgres")
