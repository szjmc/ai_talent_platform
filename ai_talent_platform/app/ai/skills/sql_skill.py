"""
模块：app/ai/skills/sql_skill.py
职责：数据查询技能（NL2SQL 执行）。

============================ 实现思路与代码要点 ============================
技能方法：
    query_database(question) -> {sql, columns, rows}
要点：
- 必须经过 adapters/nl2sql/sql_validator 校验；
- 只读连接执行，行数上限 settings（max_rows）；
- 结果以「列 + 行」形式返回，避免 LLM 二次解析出错。
待实现清单：
    - [ ] query_database
    - [ ] 错误处理（SQL 生成失败 / 执行超时）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
