"""
模块：app/ai/skills/sql_skill.py
职责：数据查询技能（NL2SQL 执行，Vanna 能力对 Agent 的唯一出口）。

标签：[架构组] [P0]  依赖：[Vanna组] [P0] VannaAdapter + SQLValidator

============================ 实现思路与代码要点 ============================
技能方法：
    query_database(question) -> {sql, columns, rows}
要点：
- 必须经过 adapters/nl2sql/sql_validator 校验（本技能在调用 Vanna 前先做一轮
  「输入侧」防护：拒绝疑似 SQL 注入 payload，属于纵深防御，不替代校验器）；
- 只读连接执行，行数上限 settings（默认 200，技能入参可覆盖，上限 1000）；
- 结果以「列 + 行」形式返回，避免 LLM 二次解析出错；
- 错误处理：SQL 生成失败 / 校验拒绝 / 执行超时 -> 结构化错误（不抛裸异常）。
已完成：
    - [x] query_database 技能 + 入参防护 + 行数/超时约束 + 结果整形
待实现（依赖方）：
    - [ ] VannaAdapter.ask（Vanna 组）
    - [ ] sql_validator.validate_select_sql（Vanna 组）
=========================================================================
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.ai.skills.base import (
    BaseSkill,
    import_dependency,
    load_skill_context,
    require_dependency,
)
from app.config.settings import settings
from app.core.exceptions import ParamError
from app.core.permissions import PermissionEnum
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

# 输入侧防护：问题是自然语言，若包含明显 SQL 危险语法，直接拒绝（不进 NL2SQL）
_DANGEROUS_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|"
    r"information_schema|performance_schema|into\s+outfile|sleep\s*\()\b",
    re.IGNORECASE,
)
_MULTI_STATEMENT = re.compile(r";\s*\S")

MAX_ROWS_LIMIT = 1000
CELL_MAX_LENGTH = 500


@skill_registry.register("sql_query")
class SqlQuerySkill(BaseSkill):
    """自然语言查询数据库（NL2SQL -> 只读执行 -> 列 + 行）。"""

    name = "sql_query"
    description = "用自然语言查询人才/岗位/测评/培训等业务数据，返回列名与数据行（只读）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "自然语言问题，例如：上个月各部门新增人才数量",
                "maxLength": 500,
            },
            "max_rows": {
                "type": "integer",
                "description": "返回行数上限",
                "default": 200,
                "minimum": 1,
                "maximum": MAX_ROWS_LIMIT,
            },
            "with_insight": {
                "type": "boolean",
                "description": "是否附带数据解读（消耗一次 LLM 调用）",
                "default": False,
            },
        },
        "required": ["question"],
    }

    category = "data"
    version = "1.0.0"
    required_permission = str(PermissionEnum.ANALYTICS_READ)
    requires = ("app.adapters.nl2sql.vanna_adapter.VannaAdapter", "app.adapters.nl2sql.sql_validator")
    timeout_seconds = 60.0
    default_max_rows = 200

    async def execute(
        self,
        question: str,
        max_rows: int | None = None,
        with_insight: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        context = load_skill_context()
        question = (question or "").strip()
        if not question:
            raise ParamError("查询问题不能为空")
        self._guard_question(question)

        max_rows = int(max_rows or self.default_max_rows)
        max_rows = max(1, min(max_rows, MAX_ROWS_LIMIT))

        adapter = self._load_adapter()
        method = getattr(adapter, "ask", None) or getattr(adapter, "query", None)
        require_dependency(
            method is not None,
            "VannaAdapter.ask",
            hint="Vanna 组未实现 NL2SQL 适配器",
        )

        # 数据范围由上下文注入（行级权限），不接受 LLM 传入的 user_id / org_id
        scope = {
            "user_id": context.user_id,
            "roles": context.roles,
            "organization_id": context.organization_id,
            "data_scope": context.data_scope,
        }
        result = method(question=question, user_scope=scope, max_rows=max_rows)
        if hasattr(result, "__await__"):
            result = await result
        return self._shape(result, question, max_rows, with_insight)

    # ---------------------------------------------------------------- 内部实现
    def _guard_question(self, question: str) -> None:
        """输入侧安全护栏（纵深防御，真正的校验仍在 sql_validator 中）。"""
        if _MULTI_STATEMENT.search(question) or _DANGEROUS_PATTERN.search(question):
            raise ParamError("查询内容包含不被允许的数据库操作语句")

    def _load_adapter(self) -> Any:
        adapter_cls = import_dependency(
            "app.adapters.nl2sql.vanna_adapter",
            "VannaAdapter",
            hint="Vanna 组未实现 NL2SQL 适配器",
        )
        return adapter_cls()

    @staticmethod
    def _shape(result: Any, question: str, max_rows: int, with_insight: bool) -> dict[str, Any]:
        """把 Vanna 返回值（dict / Pydantic / ORM）收敛成统一结构。"""
        if isinstance(result, dict):
            data = dict(result)
        else:
            data = {
                "sql": getattr(result, "sql", None),
                "columns": getattr(result, "columns", None) or [],
                "rows": getattr(result, "rows", None) or [],
                "insight": getattr(result, "insight", None),
                "chart": getattr(result, "chart", None),
            }

        columns = list(data.get("columns") or [])
        rows = [list(row) for row in (data.get("rows") or [])]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        # 单格截断，避免超大文本挤爆 LLM 上下文
        rows = [
            [
                (str(cell)[:CELL_MAX_LENGTH] + "…")
                if isinstance(cell, str) and len(cell) > CELL_MAX_LENGTH
                else cell
                for cell in row
            ]
            for row in rows
        ]

        payload: dict[str, Any] = {
            "question": question,
            "sql": data.get("sql"),
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": max_rows,
            "database": settings.PGSQL_DB if settings.PGSQL_ENABLED else settings.DB_NAME,
        }
        if with_insight:
            payload["insight"] = data.get("insight")
        if data.get("chart"):
            payload["chart"] = data["chart"]
        logger.info("sql_query 完成：rows=%s truncated=%s", len(rows), truncated)
        return payload


# ------------------------------------------------------------------ 函数式入口
async def query_database(question: str, max_rows: int = 200, with_insight: bool = False) -> dict[str, Any]:
    """函数式入口：等价于 SqlQuerySkill().execute(...)。"""
    return await SqlQuerySkill().execute(question=question, max_rows=max_rows, with_insight=with_insight)
