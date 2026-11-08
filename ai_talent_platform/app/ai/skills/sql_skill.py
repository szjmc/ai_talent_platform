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
    - [x] query_database 技能、入参防护、可信范围、结果整形
    - [x] Vanna MVP ask / SQLValidator 对接（部署依赖须配置）
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
        """Agent 的实际问数操作：读可信上下文 → 输入护栏 → 调 Adapter → 整形结果。

        question/max_rows/with_insight 来自工具参数；**_ 丢弃额外参数，
        不让模型指定 user_id、roles 等身份。对外应经 BaseSkill.run 调用，
        由 run 统一处理参数、权限、超时与结构化错误。
        """
        context = load_skill_context()
        question = (question or "").strip()
        if not question:
            raise ParamError("查询问题不能为空")
        self._guard_question(question)

        max_rows = int(max_rows or self.default_max_rows)
        max_rows = max(1, min(max_rows, MAX_ROWS_LIMIT))

        # 数据范围由上下文注入（行级权限），不接受 LLM 传入的 user_id / org_id
        scope = {
            "user_id": context.user_id,
            "roles": context.roles,
            "organization_id": context.organization_id,
            "data_scope": context.data_scope,
        }

        # 缓存：相同 question + scope 在 TTL 内直接返回结果，避免重复调 Vanna 生成 SQL
        # key 含 data_scope 与 user_id，保证不同权限档不会互相串数据。
        import hashlib

        cache_key_raw = f"nl2sql:{scope.get('data_scope')}:{scope.get('user_id')}:{max_rows}:{with_insight}:{question}"
        cache_key = "nl2sql:" + hashlib.md5(cache_key_raw.encode("utf-8")).hexdigest()
        try:
            from app.infrastructure.cache import get_json

            cached = await get_json(cache_key)
            if isinstance(cached, dict) and cached.get("_v") == 1:
                logger.info("sql_query 命中缓存：%s", question[:40])
                return cached["payload"]
        except Exception:  # noqa: BLE001
            cached = None

        adapter = self._load_adapter()
        method = getattr(adapter, "ask", None) or getattr(adapter, "query", None)
        require_dependency(
            method is not None,
            "VannaAdapter.ask",
            hint="Vanna 组未实现 NL2SQL 适配器",
        )

        result = method(question=question, user_scope=scope, max_rows=max_rows, with_insight=with_insight)
        # 兼容同步替代实现和当前异步 ask，统一拿到结果后再转成工具返回结构。
        if hasattr(result, "__await__"):
            result = await result
        payload = self._shape(result, question, max_rows, with_insight)

        # 异步回写缓存（TTL 10 分钟；缓存键由 _shape 挂在 payload["_cache_key"] 上）
        cache_key = payload.pop("_cache_key", None)
        if cache_key:
            try:
                from app.infrastructure.cache import set_json

                await set_json(cache_key, {"_v": 1, "payload": {**payload, "cached": True}}, ttl=600)
            except Exception:  # noqa: BLE001
                pass

        return payload

    # ---------------------------------------------------------------- 内部实现
    def _guard_question(self, question: str) -> None:
        """输入侧安全护栏（纵深防御，真正的校验仍在 sql_validator 中）。"""
        if _MULTI_STATEMENT.search(question) or _DANGEROUS_PATTERN.search(question):
            raise ParamError("查询内容包含不被允许的数据库操作语句")

    def _load_adapter(self) -> Any:
        """延迟加载 Vanna 适配器；未就绪时由公共依赖助手返回可识别的技能错误。"""
        adapter_cls = import_dependency(
            "app.adapters.nl2sql.vanna_adapter",
            "VannaAdapter",
            hint="Vanna 组未实现 NL2SQL 适配器",
        )
        return adapter_cls()

    @staticmethod
    def _shape(result: Any, question: str, max_rows: int, with_insight: bool) -> dict[str, Any]:
        """把 dict/对象形式结果转换为 Agent 易使用的列名、数据行和附加信息。

        保留图表、耗时、缓存标记及警告；with_insight 控制是否返回解读。
        行数/单格字符串截断只防止工具结果过大，不替代数据库 LIMIT 或行级权限。
        """
        if isinstance(result, dict):
            data = dict(result)
        else:
            data = {
                "sql": getattr(result, "sql", None),
                "columns": getattr(result, "columns", None) or [],
                "rows": getattr(result, "rows", None) or [],
                "insight": getattr(result, "insight", None),
                "chart": (getattr(result, "chart", None).model_dump(mode="json")
                          if hasattr(getattr(result, "chart", None), "model_dump")
                          else getattr(result, "chart", None)),
                "elapsed_ms": getattr(result, "elapsed_ms", 0),
                "cached": getattr(result, "cached", False),
                "warnings": getattr(result, "warnings", []),
            }

        columns = list(data.get("columns") or [])
        rows = [list(row) for row in (data.get("rows") or [])]
        # 只描述整形阶段是否丢弃了行；不代表数据库里符合条件的总行数。
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
            "database": settings.PGSQL_DB,
            "elapsed_ms": data.get("elapsed_ms", 0),
            "cached": data.get("cached", False),
            "warnings": data.get("warnings") or [],
        }
        if with_insight:
            payload["insight"] = data.get("insight")
        if data.get("chart"):
            payload["chart"] = data["chart"]
        payload["cached"] = False  # 本次是实时查询结果
        logger.info("sql_query 完成：rows=%s truncated=%s", len(rows), truncated)
        # 缓存键通过闭包挂在 payload 上，由 execute() 在异步上下文里回写缓存
        payload["_cache_key"] = cache_key
        return payload


# ------------------------------------------------------------------ 函数式入口
async def query_database(question: str, max_rows: int = 200, with_insight: bool = False) -> dict[str, Any]:
    """供其他 Python 模块调用的便捷入口，与 Agent 一样经 run 做完整检查。

    成功返回结果字典；失败把 SkillResult 中的错误码恢复为业务异常，
    保留参数错误422、SQL拒绝400、权限403等含义，供上层统一处理。
    """
    from app.core.exceptions import BusinessException

    result = await SqlQuerySkill().run(question=question, max_rows=max_rows, with_insight=with_insight)
    if not result.ok:
        status = {1001: 422, 1002: 400, 2001: 401, 2004: 403}.get(result.error_code, 502)
        raise BusinessException(result.error, code=result.error_code, status_code=status)
    return result.data
