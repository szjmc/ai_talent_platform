"""
模块：app/adapters/nl2sql/sql_validator.py
职责：SQL 安全校验（NL2SQL 生成的 SQL 必须先过这里再执行）。

============================ 实现思路与代码要点 ============================
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
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
