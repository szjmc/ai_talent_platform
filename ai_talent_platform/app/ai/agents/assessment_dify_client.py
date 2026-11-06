"""测评模块的 Dify 工作流客户端。

标签：[Vanna组] [P1]

工作流由 DIFY_WF_ASSIGN / SCORE / ANALYZE / REPORT 启用。
未配置、执行失败或输出不合法时明确报错，不生成模拟试卷、分数或研判结论。
题目事实由数据库题库提供；模型只承担语义评分和分析。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


async def _call_dify_workflow(workflow_id: str, inputs: dict[str, Any], user: str = "talent-platform") -> dict:
    """调用已配置的 Dify 应用，并验证 HTTP、JSON、运行状态和输出结构。"""
    if not workflow_id or not settings.DIFY_BASE_URL.strip() or not settings.DIFY_API_KEY.strip():
        raise ExternalServiceError("Dify 测评工作流未完整配置，请配置工作流标识、DIFY_BASE_URL 和 DIFY_API_KEY")

    url = f"{settings.DIFY_BASE_URL.rstrip('/')}/workflows/run"
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"inputs": inputs, "response_mode": "blocking", "user": user, "workflow_id": workflow_id}
    logger.info("调用 Dify 工作流：%s inputs=%s", workflow_id, sorted(inputs))
    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise ExternalServiceError(f"Dify 工作流调用失败（HTTP {exc.response.status_code}）") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ExternalServiceError("Dify 工作流请求失败或返回内容不是合法 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ExternalServiceError("Dify 工作流返回结构不合法")
    execution = data["data"]
    if execution.get("status") != "succeeded":
        raise ExternalServiceError("Dify 工作流未成功完成，未生成可信测评结果")
    outputs = execution.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise ExternalServiceError("Dify 工作流成功响应中缺少有效 outputs")
    return outputs


async def call_assign_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """自适应组卷；缺少真实工作流配置时拒绝，不返回固定试卷。"""
    return await _call_dify_workflow(settings.DIFY_WF_ASSIGN, inputs)


async def call_score_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """语义评分；仅接受真实工作流输出，分值由节点作范围校验。"""
    return await _call_dify_workflow(settings.DIFY_WF_SCORE, inputs)


async def call_analyze_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """基于本次测评的真实维度得分执行短板研判。"""
    return await _call_dify_workflow(settings.DIFY_WF_ANALYZE, inputs)


async def call_report_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """基于真实笔试、面试和人才背景生成综合报告。"""
    return await _call_dify_workflow(settings.DIFY_WF_REPORT, inputs)


__all__ = [
    "call_analyze_workflow",
    "call_assign_workflow",
    "call_report_workflow",
    "call_score_workflow",
]
