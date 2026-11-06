"""
模块：app/ai/workflows/analysis_workflow.py
职责：数据分析工作流——受权问数取数 + 基于真实聚合指标的人才研判。

标签：[Agent组] [P2]  依赖：AnalyticsService（问数 / 看板聚合）

两类工作：
1. run_analysis：自然语言问数取数并组装提示词，交给基座调用 LLM；
2. run_analysis_workflow：四节点人才研判
       collect_metrics -> talent_review -> team_structure -> recommendation
   指标只来自 AnalyticsService 当前受权聚合；离职趋势、培训完成率、匹配率
   缺少可靠数据源/口径时保留 None 并标记 partial，不伪造零值或风险结论。

============================ 实现思路与代码要点 ============================
节点编排：
    collect_data   汇总数据（调 sql_skill.query_database）
        ↓
    analyze        维度分析（结构 / 缺口 / 趋势）
        ↓
    build_insight_prompt   组装 LLM 提示词（数据 + 结构化输出约束）
        ↓
    build_report   渲染报告结构（供 analytics 导出 Word/PDF）

职责边界：本 workflow 只负责「取数 -> 分析 -> 拼提示词」，不直接调用 LLM。
LLM 调用统一交给 BaseAgent.run（基座）执行，避免重复调用浪费 token。

防幻觉三道防线：
    1. collect_data 用 row_count > 0 标记 has_data
    2. run_analysis 里，取不到数据直接拒答，不进 LLM
    3. 提示词约束「只能基于数据，严禁编造」+ 结构化 JSON 输出
=========================================================================
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

# 取数入口：架构组写的「Vanna 对 Agent 的唯一出口」，内部已做权限过滤 + 注入护栏 + limit 截断
from app.ai.skills.sql_skill import query_database

# ---------------------------------------------------------------- 常量统一管控
DEFAULT_QUERY_MAX_ROWS = 200
MAX_LLM_INPUT_CHARS = 8000

# 结构化输出约束：让 LLM 严格返回 JSON，报告渲染模块无脑取字段
INSIGHT_SYSTEM_PROMPT = (
    "你是一名人才数据分析师。你只能根据下面提供的查询结果回答问题，"
    "严禁编造、推测或补充查询结果中不存在的数据。"
    "如果查询结果为空，请直接说明没有取到相关数据。"
    "结论必须引用具体数字作为依据。"
)
INSIGHT_OUTPUT_SCHEMA = (
    "\n\n请严格按以下 JSON 格式输出，不要输出任何额外文字、解释或 markdown 代码块：\n"
    '{"conclusions": ["结论1（带数字依据）", "结论2", "结论3"], '
    '"suggestions": ["核心建议1"]}\n'
    "conclusions 为 2~3 条，suggestions 为 1 条。"
)


async def collect_data(question: str, max_rows: int = DEFAULT_QUERY_MAX_ROWS) -> dict[str, Any]:
    """第 1 节点：调 Vanna 问数能力取数据。

    传进：用户问题 question
    干了：调 sql_skill.query_database 查分析表
    传出：{question, sql, columns, rows, row_count, has_data, error_type, ...}
    """
    try:
        result = await query_database(question, max_rows=max_rows)
    except Exception as exc:  # noqa: BLE001 - 取数失败降级，不中断流程
        return {
            "question": question,
            "sql": "",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "has_data": False,
            "error_type": "query_error",
            "error": str(exc),
        }

    row_count = int(result.get("row_count") or 0)
    data: dict[str, Any] = {
        "question": result.get("question", question),
        "sql": result.get("sql", ""),
        "columns": list(result.get("columns") or []),
        "rows": list(result.get("rows") or []),
        "row_count": row_count,
        # 是否真的取到数：以 row_count 为准（sql_skill 返回的是 row_count，没有 has_data）
        "has_data": row_count > 0,
        "truncated": bool(result.get("truncated", False)),
        "error_type": None,
    }
    return data


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    """第 2 节点：对取到的数据做基础维度分析。

    传进：collect_data 的结果
    干了：行数 / 列数 / 每列非空计数（业务指标计算见后续迭代路线）
    传出：data 追加 analyze 字段
    """
    columns = data.get("columns") or []
    rows = data.get("rows") or []

    col_stats: list[dict[str, Any]] = []
    for idx, col in enumerate(columns):
        non_empty = 0
        for row in rows:
            if idx < len(row) and row[idx] not in (None, "", "None"):
                non_empty += 1
        col_stats.append({"column": col, "non_empty": non_empty})

    data["analyze"] = {
        "total_rows": len(rows),
        "total_columns": len(columns),
        "column_stats": col_stats,
    }
    return data


def build_insight_prompt(data: dict[str, Any]) -> str:
    """第 3 节点：组装提示词（不调 LLM，交给基座）。

    传进：collect_data + analyze 的结果
    干了：把数据 + 截断提示 + 结构化输出约束拼成 user prompt
    传出：给基座调 LLM 用的完整提示词字符串
    """
    data_text = json.dumps(
        {
            "columns": data.get("columns") or [],
            "rows": data.get("rows") or [],
            "row_count": data.get("row_count", 0),
        },
        ensure_ascii=False,
        default=str,
    )
    data_text = data_text[:MAX_LLM_INPUT_CHARS]

    prompt = f"请根据以下查询结果给出分析结论与决策建议：\n\n{data_text}"

    # 截断感知：数据被截断时明确告知 LLM，避免做出全盘结论
    if data.get("truncated"):
        prompt += "\n\n【注意】返回数据因行数限制被截断，分析仅基于部分样本。"

    prompt += INSIGHT_OUTPUT_SCHEMA
    return prompt


def build_report(data: dict[str, Any], analysis_type: str = "analysis") -> dict[str, Any]:
    """第 4 节点：组装最终报告结构。

    传进：前三节点的结果 + 分析类型
    干了：整理成结构化报告字段（含业务元信息）
    传出：最终报告 dict
    """
    report: dict[str, Any] = {
        "analysis_type": analysis_type,
        "question": data.get("question", ""),
        "sql": data.get("sql", ""),
        "columns": data.get("columns") or [],
        "rows": data.get("rows") or [],
        "row_count": data.get("row_count", 0),
        "analysis": data.get("analyze", {}),
        "query_time": datetime.datetime.now().isoformat(),
    }
    if data.get("truncated"):
        report["truncated"] = True
    if data.get("error_type"):
        report["error_type"] = data["error_type"]
        report["error"] = data.get("error", "")
    return report


def build_reject_message(data: dict[str, Any]) -> dict[str, Any]:
    """取不到数据时的拒答消息（区分错误类型，对用户友好可引导）。"""
    error_type = data.get("error_type")
    if error_type == "query_error":
        message = "查询执行失败，请简化问题或更换查询维度。"
    elif error_type == "no_permission":
        message = "你没有该维度的数据访问权限。"
    else:  # SQL 成功执行，但 row_count = 0
        message = "暂未匹配到该范围的数据，请检查部门、时间范围是否正确。"
    return {
        "answered": False,
        "message": message,
        "question": data.get("question", ""),
        "row_count": data.get("row_count", 0),
        "error_type": error_type,
    }


async def run_analysis(question: str, max_rows: int = DEFAULT_QUERY_MAX_ROWS) -> dict[str, Any]:
    """工作流总入口：collect_data -> analyze -> build_insight_prompt。

    传进：用户问题 question
    干了：取数 + 分析 + 拼提示词（不调 LLM，LLM 由基座 run 执行）
    传出：
        - 取不到数据：返回 answered=False 的拒答结构
        - 取到数据：返回 answered=True + prompt + data（供基座调 LLM、parse_output 解析）
    """
    data = await collect_data(question, max_rows=max_rows)

    # 防幻觉闸门：没取到数据就不往下走，直接拒答（不进 LLM）
    if not data.get("has_data"):
        return build_reject_message(data)

    data = analyze(data)
    prompt = build_insight_prompt(data)
    return {
        "answered": True,
        "question": question,
        "prompt": prompt,
        "data": data,
    }


logger = logging.getLogger(__name__)

# 服务类注入点：默认 AnalyticsService；测试可替换为同签名服务。
_analytics_service_cls: Any | None = None


def _point_field(point: Any, field: str) -> Any:
    """兼容 dict / Pydantic 模型取值。"""
    if isinstance(point, dict):
        return point.get(field)
    return getattr(point, field, None)


def _shift_month(value: datetime.date, offset: int) -> datetime.date:
    index = value.year * 12 + (value.month - 1) + offset
    return datetime.date(index // 12, index % 12 + 1, 1)


def _assess_risks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """只对有真实分母的指标提示风险；缺数据不是 0，也不据此判定流失/培训/匹配风险。"""
    overview = metrics.get("overview") or {}
    total = _point_field(overview, "total_talents")
    if total == 0:
        return [{"level": "info", "type": "no_data",
                 "message": "最新快照人才总数为 0：请确认人才档案与分析库同步"}]
    return []


def _render_insight(topic: str, metrics: dict[str, Any]) -> str:
    overview = metrics.get("overview") or {}
    parts = [f"研判主题：{topic or '默认人才盘点'}。"]
    total = _point_field(overview, "total_talents")
    avg = _point_field(overview, "assessment_avg_score")
    if total is not None:
        parts.append(f"最新月度快照人才总数 {total} 人。")
    else:
        parts.append("最新月度快照不可用，人才总数未按零处理。")
    if avg is not None:
        parts.append(f"测评记录平均分 {avg}。")
    else:
        parts.append("暂无可用测评平均分。")
    parts.append("离职趋势、培训完成率、岗位匹配率缺少可靠口径或数据源，未生成结论。")
    return "".join(parts)


async def run_analysis_workflow(
    topic: str,
    *,
    organization_id: int | None = None,
    db: Any = None,
    user: Any = None,
) -> dict[str, Any]:
    """按可信登录身份执行四节点人才研判。

    指标全部来自 AnalyticsService 的受权只读聚合；不支持/失败的指标保留 None，
    不伪造 overview、离职趋势、培训完成率或匹配率。db 仅为兼容旧调用保留，
    分析查询由 AnalyticsService 自行建立 PgSQL 只读会话。
    """
    from app.schemas.analytics_statistics import AnalyticsFilters, TrendQuery
    from app.services.analytics_service import AnalyticsService

    generated_at = datetime.datetime.now().astimezone()
    service_cls = _analytics_service_cls or AnalyticsService
    nodes: dict[str, Any] = {}
    if user is None:
        return {
            "topic": topic, "organization_id": organization_id,
            "nodes": {"collect_metrics": {"error": "缺少可信登录身份"}},
            "risks": [], "insight": "", "status": "failed",
            "error": "人才研判需要登录用户身份", "generated_at": generated_at.isoformat(),
        }

    filters = AnalyticsFilters(organization_ids=[organization_id] if organization_id else [])
    today = generated_at.date()
    trend_query = TrendQuery(
        organization_ids=filters.organization_ids,
        start_month=_shift_month(today.replace(day=1), -11),
        end_month=today.replace(day=1),
        comparison="none",
    )
    errors: list[str] = []
    warnings: list[str] = []

    async def _call(name: str, coro):
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 - 指标级降级，最终状态如实标记
            logger.warning("AnalyticsService.%s 失败：%s", name, exc)
            errors.append(f"{name}: {exc}")
            return None

    try:
        service = service_cls(db)
        education = await _call("distribution.education", service.distribution("education", filters, user))
        org_dist = await _call("distribution.org", service.distribution("org", filters, user))
        assessment = await _call("assessment_summary", service.assessment_summary(filters, user))
        trend = await _call("trend.talent_total", service.trend(trend_query, user))
    except Exception as exc:  # noqa: BLE001 - 服务构造失败
        logger.exception("collect_metrics 失败 topic=%s", topic)
        return {
            "topic": topic, "organization_id": organization_id,
            "nodes": {"collect_metrics": {"error": str(exc)}},
            "risks": [], "insight": "", "status": "failed",
            "error": str(exc), "generated_at": generated_at.isoformat(),
        }

    trend_items = list(_point_field(trend, "items") or []) if trend is not None else []
    latest = trend_items[-1] if trend_items else None
    total = None
    if latest is not None and not _point_field(latest, "missing"):
        total = _point_field(latest, "value")
    overview = {
        "total_talents": total,
        "active_positions": None,
        "match_rate": None,
        "training_completion": None,
        "assessment_avg_score": _point_field(assessment, "average_score") if assessment is not None else None,
        "degraded": any(value is None for value in (education, org_dist, assessment, trend)),
    }
    for result in (education, org_dist, assessment, trend):
        warnings.extend(_point_field(result, "warnings") or [])
    metrics = {
        "overview": overview,
        "distribution_education": education,
        "distribution_org": org_dist,
        "assessment_summary": assessment,
        "trend_talent_total": trend,
        # 旧字段保留为 None，明确没有离职趋势数据源。
        "trend_turnover": None,
    }
    nodes["collect_metrics"] = {
        "metrics": metrics, "collected_at": generated_at.isoformat(),
        "degraded": overview["degraded"], "warnings": warnings, "errors": errors,
    }

    risks = _assess_risks(metrics)
    insight = _render_insight(topic, metrics)
    nodes["talent_review"] = {"summary": insight, "risks": risks}

    def _distribution_items(result: Any) -> list[dict[str, Any]]:
        return [{"label": _point_field(item, "label"), "value": _point_field(item, "count")}
                for item in (_point_field(result, "items") or [])]

    org_items = _distribution_items(org_dist)
    edu_items = _distribution_items(education)
    nodes["team_structure"] = {
        "org_distribution": org_items,
        "education_distribution": edu_items,
        "note": "" if org_items and edu_items else "分布指标不可用或为空，请检查分析库同步与授权范围",
    }

    actions: list[str] = []
    if total is None or not org_items or not edu_items:
        actions.append("先补齐人才档案并执行分析库同步，再复核月度快照与组织/学历分布")
    if assessment is None or _point_field(assessment, "record_count") in (None, 0):
        actions.append("暂无可用测评记录，完成笔试评分后再纳入能力研判")
    if not actions:
        actions.append("基于当前受权快照维持例行月度盘点；离职、培训完成率与匹配率待可靠数据源接入后再评估")
    nodes["recommendation"] = {"actions": actions, "insight": insight}

    status = "partial" if errors or overview["degraded"] or warnings else "succeeded"
    result: dict[str, Any] = {
        "topic": topic, "organization_id": organization_id, "nodes": nodes,
        "risks": risks, "insight": insight, "status": status,
        "generated_at": generated_at.isoformat(), "llm_enabled": False,
    }
    if errors:
        result["error"] = "；".join(errors)
    elif warnings:
        result["error"] = "；".join(dict.fromkeys(warnings))
    return result


__all__ = ["run_analysis_workflow"]
