"""
模块：app/ai/workflows/analysis_workflow.py
职责：数据分析工作流——按用户进行流失风险预警 + 人才研判。

标签：[Agent组] [P2]  依赖：AnalyticsService（问数 / 看板聚合）

两类工作：
1. 流失风险预警：按用户（user_id）拉取其数据范围内的指标，
   评估离职趋势 / 培训完成率 / 匹配率，输出结构化 risks 列表；
2. 人才研判（四节点流水线）：
       collect_metrics -> talent_review -> team_structure -> recommendation
   （纯 async 函数编排——LangGraph 为可选依赖且当前未启用，
   见 requirements.txt 注释；接入图引擎时仅需替换编排层，节点函数不变）

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
import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 服务类注入点：默认延迟解析为 AnalyticsService；测试可 monkeypatch 替换
# （None = 默认），或注入旧签名假服务验证兼容路径。
_analytics_service_cls: Any | None = None


def _current_context() -> tuple[list[str], int | None]:
    """从 AIContext 取当前角色与组织（无上下文 -> ([], None)，由服务层 fail-closed）。"""
    try:
        from app.ai.context import get_context

        context = get_context()
        return list(context.roles or []), context.organization_id
    except Exception:  # noqa: BLE001 - 无上下文 / 循环导入时保持空
        return [], None


def _point_field(point: Any, field: str) -> Any:
    """兼容取值（dict 或 pydantic 模型），供风险评估/洞察模板复用。"""
    if isinstance(point, dict):
        return point.get(field)
    return getattr(point, field, None)


# 跨组缺口注记：Vanna 组重构后的 AnalyticsService 采用 (filters, user) 风格，
# 且暂未提供驾驶舱概览（overview）与离职趋势（turnover）指标；本工作流按
# fail-soft 原则对缺失/失败的指标降级为 0 值 + degraded 注记，不中断流水线。
_DEGRADED_OVERVIEW: dict[str, Any] = {
    "total_talents": 0,
    "active_positions": 0,
    "match_rate": 0.0,
    "training_completion": 0.0,
    "assessment_avg_score": None,
    "degraded": True,
}


async def _compat_call(service: Any, method: str, *args: Any, note: str) -> Any:
    """按旧签名调用服务方法；缺失/签名不匹配/失败时返回 None（调用方降级）。

    Vanna 组 AnalyticsService 重构后旧签名（overview(scope) 等）不再可用，
    此处统一探测兜底，避免 AttributeError 打断整条研判流水线。
    """
    fn = getattr(service, method, None)
    if fn is None:
        logger.warning("AnalyticsService.%s 不存在（跨组 API 未对齐），已降级", method)
        return None
    try:
        return await fn(*args)
    except Exception as exc:  # noqa: BLE001 - 指标级降级，不中断流水线
        logger.warning("AnalyticsService.%s 调用失败（跨组 API 未对齐？）：%s", method, exc)
        return None


def _assess_risks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """按指标评估流失风险（纯函数，便于单测）。

    规则：
    - 离职趋势末值 > 首值且 > 0 -> high（离职上升）
    - 培训完成率 < 0.6          -> medium（培训不足）
    - 匹配率 < 0.5              -> medium（岗位匹配偏低）
    - 人才总数为 0              -> info（无数据，提示先同步）
    """
    risks: list[dict[str, Any]] = []
    overview = metrics.get("overview") or {}
    turnover_metric = metrics.get("trend_turnover") or {}
    turnover_points = _point_field(turnover_metric, "points") or []
    if len(turnover_points) >= 2:
        first = float(_point_field(turnover_points[0], "value") or 0)
        last = float(_point_field(turnover_points[-1], "value") or 0)
        if last > first and last > 0:
            risks.append(
                {
                    "level": "high",
                    "type": "turnover_rising",
                    "message": f"离职趋势上升（{_point_field(turnover_points[0], 'month')} {first} -> "
                    f"{_point_field(turnover_points[-1], 'month')} {last}）",
                }
            )
    training = float(_point_field(overview, "training_completion") or 0)
    if overview and training < 0.6:
        risks.append(
            {
                "level": "medium",
                "type": "training_low",
                "message": f"培训完成率偏低（{training:.0%}，阈值 60%）",
            }
        )
    match_rate = float(_point_field(overview, "match_rate") or 0)
    if overview and match_rate < 0.5:
        risks.append(
            {
                "level": "medium",
                "type": "match_low",
                "message": f"岗位匹配率偏低（{match_rate:.0%}，阈值 50%）",
            }
        )
    if overview and float(_point_field(overview, "total_talents") or 0) == 0:
        risks.append(
            {
                "level": "info",
                "type": "no_data",
                "message": "人才总数为 0：请先执行 scripts/sync_analytics.py 同步分析库",
            }
        )
    return risks


def _render_insight(topic: str, metrics: dict[str, Any]) -> str:
    """指标驱动的结构化研判结论（模板；LLM 增强为后续接入点）。"""
    overview = metrics.get("overview") or {}
    parts = [f"研判主题：{topic or '默认人才盘点'}。"]
    if overview:
        parts.append(
            f"人才规模 {int(_point_field(overview, 'total_talents') or 0)} 人，"
            f"在招岗位 {int(_point_field(overview, 'active_positions') or 0)} 个，"
            f"匹配率 {float(_point_field(overview, 'match_rate') or 0):.0%}，"
            f"培训完成率 {float(_point_field(overview, 'training_completion') or 0):.0%}，"
            f"平均测评分 {_point_field(overview, 'assessment_avg_score') or '-'}。"
        )
    else:
        parts.append("指标聚合未返回数据（分析库未启用或数据未同步）。")
    return "".join(parts)


async def run_analysis_workflow(
    topic: str,
    *,
    organization_id: int | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """执行人才研判流水线（按用户数据范围）。

    :param topic: 研判主题（如 "Q4 研发团队盘点"）
    :param organization_id: 指定组织（缺省取 AIContext.organization_id）
    :param db: 可选 MySQL 会话（缺省无 -> 业务小聚合指标降级为 0 并注记）
    :returns: {topic, organization_id, nodes{collect_metrics, talent_review,
               team_structure, recommendation}, risks, insight, status, generated_at}
    """
    # import 置于函数内：避免模块级依赖、并保证 collect_metrics 只经服务层取数
    from app.services.analytics_service import AnalyticsService

    service_cls = _analytics_service_cls or AnalyticsService

    context_roles, context_org = _current_context()
    scope_org = organization_id if organization_id is not None else context_org
    scope = {"roles": context_roles, "organization_id": scope_org}
    generated_at = datetime.now(timezone.utc)
    nodes: dict[str, Any] = {}
    risks: list[dict[str, Any]] = []
    status = "succeeded"
    error: str | None = None

    try:
        service = service_cls(db)
        # ---- 节点 1：collect_metrics（唯一取数入口：AnalyticsService，禁止裸 SQL）
        # 每个指标独立降级：新版服务未提供 overview/turnover 时置 None，不互相拖垮。
        overview = await _compat_call(service, "overview", scope,
                                      note="驾驶舱概览指标")
        education = await _compat_call(service, "distribution", "education", scope,
                                       note="学历分布")
        org_dist = await _compat_call(service, "distribution", "org", scope,
                                      note="组织分布")
        trend = await _compat_call(service, "trend", "turnover", scope,
                                   note="离职趋势")
        degraded = overview is None or trend is None or education is None or org_dist is None
        if overview is None:
            overview = dict(_DEGRADED_OVERVIEW)
        metrics: dict[str, Any] = {
            "overview": overview,
            "distribution_education": education,
            "distribution_org": org_dist,
            "trend_turnover": trend,
        }
        nodes["collect_metrics"] = {
            "metrics": metrics,
            "collected_at": generated_at.isoformat(),
            "degraded": degraded,
        }
    except Exception as exc:  # noqa: BLE001 - 工作流返回结构化失败，不打断调用方
        logger.exception("collect_metrics 失败 topic=%s", topic)
        return {
            "topic": topic,
            "organization_id": scope_org,
            "nodes": {"collect_metrics": {"error": str(exc)}},
            "risks": [],
            "insight": "",
            "status": "failed",
            "error": str(exc),
            "generated_at": generated_at.isoformat(),
        }

    try:
        # ---- 节点 2：人才研判（风险评估 + 结论）
        trend_points = _point_field(trend, "points") or [] if trend is not None else []
        risks = _assess_risks({"overview": overview, "trend_turnover": {"points": trend_points}})
        nodes["talent_review"] = {
            "summary": _render_insight(topic, {"overview": overview}),
            "risks": risks,
        }

        # ---- 节点 3：组织结构（分布缺失时输出空集 + 提示，不抛错）
        org_items = _point_field(org_dist, "items") or [] if org_dist is not None else []
        edu_items = _point_field(education, "items") or [] if education is not None else []
        nodes["team_structure"] = {
            "org_distribution": [
                {"label": _point_field(i, "label"), "value": _point_field(i, "value")}
                for i in org_items
            ],
            "education_distribution": [
                {"label": _point_field(i, "label"), "value": _point_field(i, "value")}
                for i in edu_items
            ],
            "note": (
                "" if org_items and edu_items
                else "分布指标暂不可用（AnalyticsService 接口未对齐或数据未同步）"
            ),
        }

        # ---- 节点 4：建议
        actions: list[str] = []
        for risk in risks:
            if risk["type"] == "turnover_rising":
                actions.append("对高离职部门启动保留面谈与薪酬竞争力复盘")
            elif risk["type"] == "training_low":
                actions.append("补齐必修课程学习计划，按月跟踪完成率至 60% 以上")
            elif risk["type"] == "match_low":
                actions.append("复盘岗位画像与推荐阈值，优先处理零匹配在招岗位")
        if not actions:
            actions.append("指标整体平稳：维持例行月度盘点与关键岗位继任观察")
        insight = _render_insight(topic, {"overview": overview})
        nodes["recommendation"] = {"actions": actions, "insight": insight}
    except Exception as exc:  # noqa: BLE001 - 指标已到手，节点失败降级 partial
        logger.exception("研判节点失败 topic=%s", topic)
        status = "partial"
        error = str(exc)
        insight = _render_insight(topic, {"overview": overview})

    # 指标有降级 -> partial（模块 docstring 约定：缺失不抛断流，但状态要如实）
    if degraded and status == "succeeded":
        status = "partial"
        error = error or "部分指标降级：AnalyticsService 未提供 overview/turnover 接口（跨组 API 待对齐）"

    result: dict[str, Any] = {
        "topic": topic,
        "organization_id": scope_org,
        "nodes": nodes,
        "risks": risks,
        "insight": insight,
        "status": status,
        "generated_at": generated_at.isoformat(),
    }
    if error:
        result["error"] = error
    # settings 仅作接入点标记：LLM 解读就绪后在此挂接（当前为模板研判）
    result["llm_enabled"] = bool(settings.LLM_API_KEY)
    return result


__all__ = ["run_analysis_workflow"]
