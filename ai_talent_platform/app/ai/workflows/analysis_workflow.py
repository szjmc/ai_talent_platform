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

数据访问红线：collect_metrics 只能调用 AnalyticsService（问数 / 看板聚合），
严禁直连 pg_session() 或写裸 SQL —— 数据权限与行级过滤统一走服务层。

实现要点：
- 组织权限：组织范围来自 AIContext（get_context）与调用方 organization_id，
  受限身份由 Service 层自动附加行级部门过滤（fail-closed）；
- 指标缺失 / 分析库未启用时降级为 0 值 + partial 状态，不抛断流；
- insight 为指标驱动的结构化模板（LLM 增强解读为后续接入点，
  settings.LLM_API_KEY 配置就绪后由 LLM 组补充）。

已完成：
    - [x] 流水线执行 run_analysis_workflow(topic, organization_id, db)
    - [x] 流失风险预警输出 _assess_risks（离职上升 / 培训不足 / 匹配偏低）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


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

    context_roles, context_org = _current_context()
    scope_org = organization_id if organization_id is not None else context_org
    scope = {"roles": context_roles, "organization_id": scope_org}
    generated_at = datetime.now(timezone.utc)
    nodes: dict[str, Any] = {}
    risks: list[dict[str, Any]] = []
    status = "succeeded"
    error: str | None = None

    try:
        service = AnalyticsService(db)
        # ---- 节点 1：collect_metrics（唯一取数入口：AnalyticsService，禁止裸 SQL）
        overview = await service.overview(scope)
        education = await service.distribution("education", scope)
        org_dist = await service.distribution("org", scope)
        trend = await service.trend("turnover", scope)
        metrics: dict[str, Any] = {
            "overview": overview,
            "distribution_education": education,
            "distribution_org": org_dist,
            "trend_turnover": trend,
        }
        nodes["collect_metrics"] = {
            "metrics": metrics,
            "collected_at": generated_at.isoformat(),
            "degraded": bool(overview.data_notes),
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
        risks = _assess_risks(
            {"overview": overview, "trend_turnover": {"points": trend.points}}
        )
        nodes["talent_review"] = {
            "summary": _render_insight(topic, {"overview": overview}),
            "risks": risks,
        }

        # ---- 节点 3：组织结构
        nodes["team_structure"] = {
            "org_distribution": [
                {"label": item.label, "value": item.value} for item in org_dist.items
            ],
            "education_distribution": [
                {"label": item.label, "value": item.value} for item in education.items
            ],
            "note": org_dist.note or education.note,
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
