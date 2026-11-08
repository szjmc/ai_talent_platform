"""人才决策报告：复用受控只读取数，真实数据与模型解读分别返回。"""
from __future__ import annotations

from typing import Any

from app.ai.context import context_scope, get_context
from app.ai.workflows.talent_decision_report import run_analysis
from app.ai.agents.analytics_agent import AnalyticsAgent


async def generate_decision_report(user: Any, question: str, report_type: str | None = None) -> dict[str, Any]:
    """数据查询失败不生成虚构报告；模型不可用时保留真实图表并说明缺少解读。"""
    with context_scope(user):
        result = await run_analysis(question, data_scope=get_context().data_scope, report_type=report_type)
        if not result.get("answered"):
            return result
        agent = AnalyticsAgent(user=user, context=get_context())
        agent._report = result
        agent._report_type = report_type
        agent._report_meta = await agent._build_business_meta(result)
        parsed = await agent.parse_output("")
        parsed["warnings"] = [] if agent._report_meta else ["模型解读不可用；仅展示已查询到的数据与规则风险。"]
        if report_type:
            parsed["warnings"].append("周期报告基于当前可用汇总数据生成，尚未按报告周期限定全部取数或计算同期对比。")
        else:
            parsed["warnings"].append("报告基于固定汇总查询，暂不自动按需求中的部门或其他条件过滤。")
        # 同时保留参考页面使用的 data/insight 字段，兼容原周期报告消费者。
        parsed["data"] = result.get("data") or {}
        parsed["insight"] = {**{key: value for key, value in parsed.items() if key not in ("data", "insight")},
                             "suggestions": {"p0": parsed.get("suggestion_p0", []),
                                             "p1": parsed.get("suggestion_p1", []),
                                             "warn_if_no_action": parsed.get("warn_if_no_action", "")}}
        return parsed
