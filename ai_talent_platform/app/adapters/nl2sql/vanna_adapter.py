"""
模块：app/adapters/nl2sql/vanna_adapter.py
职责：Vanna.ai 适配（自然语言 -> SQL -> 数据 -> 图表配置）。

标签：[Vanna组] [P0]  依赖：SQLValidator + PgSQL 只读连接（ask -> SQL -> 只读执行 -> 图表）

流程：
    用户问题
      -> Vanna generate_sql(question)
      -> sql_validator.validate_select_sql（必须，见 adapters/nl2sql/sql_validator）
      -> 注入行级数据范围（非管理员，build_scope_clause，fail-closed）
      -> pg_session 只读执行（强制 LIMIT）
      -> infer_chart 图表配置（时间->折线、枚举->柱状、占比->饼图）
      -> insight 数据解读（启发式；LLM 解读由调用方按需追加）

实现要点：
- 训练数据（DDL、示例 SQL、业务术语）在 scripts/train_vanna.py 中导入（调用本类 train）；
- 分析类大查询走 PgSQL 分析库，不打 MySQL 主库；
- 图表配置统一输出 {type, x_field, y_field, title, series}，前端用 ECharts 渲染；
- 数据权限：生成 SQL 后追加角色数据范围条件，杜绝越权查询（无法注入时拒绝执行）；
- Vanna 引擎可注入：VannaAdapter.configure(engine)（测试 / 自建实例），
  未注入时按需导入 vanna 包，未安装 / 未初始化时抛「依赖未就绪」结构化异常。

已完成：
    - [x] VannaAdapter.ask(question, user_scope, max_rows) -> NL2SQLResponse 字典
    - [x] VannaAdapter.train(corpus)（scripts/train_vanna.py 调用）
    - [x] 图表类型推断 infer_chart（时间->折线、枚举->柱状、占比->饼图）
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.adapters.nl2sql.sql_validator import (
    DEFAULT_MAX_ROWS,
    inject_data_scope,
    inject_limit,
    validate_select_sql,
)
from app.core.exceptions import BusinessRuleError
from app.infrastructure.database.postgres import pg_session

logger = logging.getLogger(__name__)

# 数据范围不受限的角色（与 permissions.visible_organization_ids 的 hr=ALL 口径一致）
_UNRESTRICTED_ROLES = {"admin", "super_admin", "hr"}

_DATE_LIKE_RE = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?$")
_PIE_HINT_RE = re.compile(r"占比|百分|比率|比例|结构比")


def is_scope_unrestricted(user_scope: dict[str, Any] | None) -> bool:
    """当前数据范围是否不受限（管理员 / 超管 / HR / data_scope=all）。"""
    scope = user_scope or {}
    roles = {str(role).lower() for role in (scope.get("roles") or [])}
    data_scope = str(scope.get("data_scope") or "").lower()
    return bool(roles & _UNRESTRICTED_ROLES) or data_scope == "all"


def _json_safe(value: Any) -> Any:
    """把 Decimal / 日期等行值收敛为 JSON 友好类型。"""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def build_scope_clause(sql: str, user_scope: dict[str, Any] | None) -> str:
    """按查询涉及的表拼装行级数据范围条件（受限身份无法注入时 fail-closed 拒绝）。

    :param user_scope: {roles, organization_id, data_scope}（来自 AIContext，不信任 LLM 传参）
    :return: 可直接 AND 的条件片段；不受限时为空字符串。
    :raises BusinessRuleError: 受限身份且缺 organization_id / 表不支持行级过滤。
    """
    scope = user_scope or {}
    if is_scope_unrestricted(scope):
        return ""
    org_id = scope.get("organization_id")
    if not isinstance(org_id, int) or org_id <= 0:
        raise BusinessRuleError("数据范围受限：当前身份缺少部门信息，无法执行分析查询")

    lowered = sql.lower()
    clauses: list[str] = []
    if "dim_talent_wide" in lowered:
        clauses.append(f"org_path LIKE '%/{org_id}/%'")
    if "fact_talent_snapshot" in lowered:
        clauses.append(f"organization_id = {org_id}")
    if "dim_organization" in lowered:
        clauses.append(f"org_id = {org_id}")
    fact_tables = ("fact_assessment_history", "fact_training_track", "fact_behavior_event")
    if any(name in lowered for name in fact_tables):
        clauses.append(
            "talent_id IN (SELECT talent_id FROM dim_talent_wide "
            f"WHERE org_path LIKE '%/{org_id}/%')"
        )
    if not clauses:
        raise BusinessRuleError("数据范围受限：该查询涉及的表暂不支持行级过滤，请联系管理员")
    return " AND ".join(f"({c})" for c in clauses)


def infer_chart(columns: list[str], rows: list[list[Any]], question: str = "") -> dict | None:
    """图表类型推断：时间 -> line、占比 -> pie、多数值 -> radar、其余 -> bar。"""
    if not columns or not rows:
        return None
    x_field = columns[0]
    numeric_fields = [
        col
        for i, col in enumerate(columns[1:], start=1)
        if any(
            isinstance(row[i], (int, float, Decimal)) and not isinstance(row[i], bool)
            for row in rows[:20]
        )
    ]

    def _looks_like_date(sample: list[Any]) -> bool:
        return all(
            isinstance(v, str) and _DATE_LIKE_RE.match(v) for v in sample if v is not None
        ) and any(v is not None for v in sample)

    chart_type = "bar"
    y_field = numeric_fields[0] if numeric_fields else (columns[1] if len(columns) > 1 else "")
    sample_x = [row[0] for row in rows[:20]]
    if _looks_like_date(sample_x):
        chart_type = "line"
    elif _PIE_HINT_RE.search(question or "") and numeric_fields:
        chart_type = "pie"
    elif len(numeric_fields) >= 3:
        chart_type = "radar"

    return {
        "type": chart_type,
        "x_field": str(x_field),
        "y_field": str(y_field),
        "title": (question or "").strip()[:80] or str(x_field),
        "series": numeric_fields[:4],
    }


def heuristic_insight(question: str, columns: list[str], rows: list[list[Any]]) -> str:
    """无 LLM 时的启发式数据解读（保证问数链路离线可用）。"""
    if not rows:
        return "查询未返回数据。"
    parts = [f"共返回 {len(rows)} 行。"]
    if len(columns) >= 2:
        value_index = len(columns) - 1
        pairs = [
            (row[0], row[value_index])
            for row in rows
            if row and isinstance(row[value_index], (int, float, Decimal))
        ]
        if pairs:
            top = max(pairs, key=lambda item: float(item[1]))
            parts.append(f"「{columns[0]}」维度中「{top[0]}」最高（{columns[-1]}={_json_safe(top[1])}）。")
    if question:
        parts.append(f"问题：{question[:60]}。")
    return "".join(parts)


class VannaAdapter:
    """Vanna 适配器：question -> 校验后的 SQL -> PgSQL 只读执行 -> 图表 / 解读。"""

    # 可注入引擎（测试 fake / 运维自建实例）：具备 generate_sql(question) 与可选 train(...)
    _engine: Any | None = None

    @classmethod
    def configure(cls, engine: Any) -> None:
        """注入 Vanna 兼容引擎（具备 generate_sql；可选 train/run_sql）。"""
        cls._engine = engine

    @classmethod
    def reset_configured(cls) -> None:
        cls._engine = None

    # ------------------------------------------------------------------ 引擎
    def _load_engine(self) -> Any:
        if VannaAdapter._engine is not None:
            return VannaAdapter._engine
        try:
            import vanna  # noqa: F401
        except ImportError as exc:
            raise BusinessRuleError(
                "依赖未就绪：Vanna 未安装（pip install vanna>=0.7 后重试）"
            ) from exc
        try:
            from vanna.remote import VannaDefault  # 官方零配置入口

            engine = VannaDefault()
        except Exception as exc:  # noqa: BLE001 - 版本差异 / 未配置统一结构化上抛
            raise BusinessRuleError(
                f"依赖未就绪：Vanna 初始化失败（{exc}）；"
                "可通过 VannaAdapter.configure(engine) 注入自建实例"
            ) from exc
        VannaAdapter._engine = engine
        return engine

    # ------------------------------------------------------------------ 训练
    def train(self, corpus: dict[str, Any]) -> dict[str, Any]:
        """导入训练语料（scripts/train_vanna.py 调用）：DDL + 文档 + 示例问答。"""
        engine = self._load_engine()
        engine_train = getattr(engine, "train", None)
        if engine_train is None:
            raise BusinessRuleError("依赖未就绪：当前 Vanna 引擎不支持 train（缺少 train 方法）")

        trained = 0
        if corpus.get("ddl"):
            engine_train(ddl=str(corpus["ddl"]))
            trained += 1
        for doc in corpus.get("documentation") or []:
            engine_train(documentation=str(doc))
            trained += 1
        for example in corpus.get("examples") or []:
            question, sql = example.get("question"), example.get("sql")
            if question and sql:
                engine_train(question=str(question), sql=str(sql))
                trained += 1
        logger.info("Vanna 训练导入完成：%s 条（database=%s）", trained, corpus.get("database"))
        return {"trained": trained, "database": corpus.get("database")}

    # ------------------------------------------------------------------ 问数
    async def ask(
        self,
        question: str,
        user_scope: dict[str, Any] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """自然语言问数全链路；返回 NL2SQLResponse 字段字典。"""
        started = time.perf_counter()
        question = (question or "").strip()
        if not question:
            raise BusinessRuleError("查询问题不能为空")

        engine = self._load_engine()
        generate = getattr(engine, "generate_sql", None)
        if generate is None:
            raise BusinessRuleError("依赖未就绪：Vanna 引擎缺少 generate_sql 方法")

        if inspect.iscoroutinefunction(generate):
            raw_sql = await generate(question)
        else:
            raw_sql = await asyncio.to_thread(generate, question)
        if not isinstance(raw_sql, str) or not raw_sql.strip():
            raise BusinessRuleError("Vanna 未能生成有效 SQL，请换一种问法重试")

        # 1) 安全校验（不注入 LIMIT，先拼数据范围）
        sql = validate_select_sql(raw_sql, max_rows=max_rows, apply_limit=False)
        # 2) 行级数据范围（非管理员 fail-closed）
        scope_clause = build_scope_clause(sql, user_scope)
        if scope_clause:
            sql = inject_data_scope(sql, scope_clause)
        # 3) 强制 LIMIT
        sql = inject_limit(sql, max_rows)

        columns, rows = await self._execute_readonly(sql, max_rows)
        chart = infer_chart(columns, rows, question)
        return {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "chart": chart,
            "insight": heuristic_insight(question, columns, rows),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    async def _execute_readonly(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
        """PgSQL 只读执行（SQL 已过校验；分析库未启用时结构化拒绝）。"""
        async with pg_session() as pg:
            if pg is None:
                raise BusinessRuleError(
                    "依赖未就绪：PgSQL 分析库未启用（PGSQL_ENABLED=False），问数不可用"
                )
            result = await pg.execute(text(sql))
            columns = [str(key) for key in result.keys()]
            rows = [list(row) for row in result.all()[:max_rows]]
        return columns, [[_json_safe(cell) for cell in row] for row in rows]


__all__ = [
    "VannaAdapter",
    "build_scope_clause",
    "heuristic_insight",
    "infer_chart",
    "is_scope_unrestricted",
]
