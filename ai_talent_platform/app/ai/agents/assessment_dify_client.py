"""
模块：app/ai/agents/assessment_dify_client.py
职责：测评模块与 Dify 中台三个工作流的对接客户端。

============================ 重要：替换说明 ============================
assign / score / analyze 三个节点调用的是【Dify 组同事】正在开发的工作流，
目前尚未提供。本模块用「测试数据」先跑通全链路；待同事交付 Dify 工作流后，
只需把每个方法里 >>> DIFY 替换点 <<< 区块内的测试数据 return 删除，
改为调用下方 _call_dify_workflow(...) 即可（HTTP 调用框架已写好，开箱即用）。

三个工作流与 Dify 环境变量对应关系：
    assign  -> DIFY_WF_ASSIGN    （自适应组卷，千人千卷）
    score   -> DIFY_WF_SCORE     （大模型语义打分）
    analyze -> DIFY_WF_ANALYZE   （短板研判 / 能力结构分析）
当对应 WF_ID 非空时，自动走真实 Dify；为空时走测试数据。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


# ================================================================ 真实 Dify 调用
async def _call_dify_workflow(workflow_id: str, inputs: dict[str, Any], user: str = "talent-platform") -> dict:
    """通用 Dify 工作流调用（阻塞模式）。

    Dify Workflow Run API：
        POST {DIFY_BASE_URL}/workflows/run
        Header: Authorization: Bearer {DIFY_API_KEY}
        Body:   { "inputs": {...}, "response_mode": "blocking", "user": "..." }
    返回 Dify 输出的 data.outputs 字段（dict）。
    """
    if not workflow_id:
        raise ExternalServiceError("Dify 工作流 ID 未配置")

    url = f"{settings.DIFY_BASE_URL.rstrip('/')}/workflows/run"
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs,
        "response_mode": "blocking",
        "user": user,
    }
    logger.info("调用 Dify 工作流：%s inputs=%s", workflow_id, {k: "..." for k in inputs})
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise ExternalServiceError(
            f"Dify 工作流调用失败（HTTP {resp.status_code}）：{resp.text[:300]}"
        )
    data = resp.json()
    # Dify 阻塞模式返回 { task_id, workflow_run_id, data: { outputs: {...}, status, ... } }
    outputs = (data.get("data") or {}).get("outputs") or {}
    logger.info("Dify 工作流 %s 完成：status=%s", workflow_id, (data.get("data") or {}).get("status"))
    return outputs


# ================================================================ 1) assign 组卷
async def call_assign_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """自适应组卷：按岗位层级/专业/等级千人千卷。

    :param inputs: {talent_id, position_id, job_level, profession, grade, question_count}
    :return: {paper_id, questions:[{question_id,type,content,options,score,dimension,sort}], total_score}
    """
    if getattr(settings, 'DIFY_WF_ASSIGN', ''):
        # >>> DIFY 替换点：同事工作流就绪后，启用下面这行即可删除测试数据 <<<
        # return await _call_dify_workflow(getattr(settings, 'DIFY_WF_ASSIGN', ''), inputs)
        return await _call_dify_workflow(getattr(settings, 'DIFY_WF_ASSIGN', ''), inputs)

    # ===== 测试数据（Dify 组交付前占位，交付后删除本块）=====
    logger.warning("[assign] Dify 工作流未配置，返回测试组卷数据")
    return {
        "paper_id": f"paper-test-{inputs.get('talent_id', 0)}",
        "total_score": 100.0,
        "questions": [
            {"question_id": 1001, "type": "single", "content": "数字化转型项目中，最核心的成功要素是？",
             "options": [{"key": "A", "text": "技术平台选型"}, {"key": "B", "text": "业务流程重构"},
                         {"key": "C", "text": "高层支持与组织协同"}, {"key": "D", "text": "供应商管理"}],
             "score": 10, "dimension": "战略思维", "sort": 1},
            {"question_id": 1002, "type": "multiple", "content": "AI 应用落地的关键环节包括？（多选）",
             "options": [{"key": "A", "text": "数据治理"}, {"key": "B", "text": "场景定义"},
                         {"key": "C", "text": "模型选型"}, {"key": "D", "text": "上线后运营"}],
             "score": 15, "dimension": "专业能力", "sort": 2},
            {"question_id": 1003, "type": "text", "content": "请结合本岗位，描述一次你主导的数字化项目及量化成果。",
             "options": None, "score": 25, "dimension": "实战经验", "sort": 3},
            {"question_id": 1004, "type": "text", "content": "描述你带团队推进跨部门协作时遇到的最大阻力及解决方式。",
             "options": None, "score": 25, "dimension": "综合素质", "sort": 4},
            {"question_id": 1005, "type": "single", "content": "对一个技术债高的存量系统，你优先采取？",
             "options": [{"key": "A", "text": "推倒重建"}, {"key": "B", "text": "灰度重构"},
                         {"key": "C", "text": "维持现状"}, {"key": "D", "text": "外包改造"}],
             "score": 25, "dimension": "专业能力", "sort": 5},
        ],
    }


# ================================================================ 2) score 语义打分
async def call_score_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """大模型语义打分：量化综合素质、专业能力、岗位适配度。

    :param inputs: {talent_id, position_id, job_level, answers:[...], rule_total_score}
    :return: {comprehensive, professional, position_fit, subjective_total, rationale}
    """
    if getattr(settings, 'DIFY_WF_SCORE', ''):
        # >>> DIFY 替换点：同事工作流就绪后，启用下面这行即可删除测试数据 <<<
        return await _call_dify_workflow(getattr(settings, 'DIFY_WF_SCORE', ''), inputs)

    # ===== 测试数据（Dify 组交付前占位，交付后删除本块）=====
    logger.warning("[score] Dify 工作流未配置，返回测试语义打分数据")
    return {
        "comprehensive": 82.0,
        "professional": 76.5,
        "position_fit": 80.0,
        "subjective_total": 78.0,
        "rationale": "主观题作答结构完整、有量化结果与团队协作案例，专业深度良好；"
                     "对技术债治理与 AI 落地环节的阐述偏宏观，缺少具体指标。",
    }


# ================================================================ 3) analyze 短板研判
async def call_analyze_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """短板研判、能力结构分析。

    :param inputs: {talent_id, job_level, dimension_scores, llm_scores}
    :return: {weakness:[{dimension,score,gap,suggestion}], ability_structure, level, growth_potential}
    """
    if getattr(settings, 'DIFY_WF_ANALYZE', ''):
        # >>> DIFY 替换点：同事工作流就绪后，启用下面这行即可删除测试数据 <<<
        return await _call_dify_workflow(getattr(settings, 'DIFY_WF_ANALYZE', ''), inputs)

    # ===== 测试数据（Dify 组交付前占位，交付后删除本块）=====
    logger.warning("[analyze] Dify 工作流未配置，返回测试短板研判数据")
    return {
        "level": "中级",
        "growth_potential": "较高：专业能力扎实，补足数据治理与 AI 工程化短板后可冲击高级",
        "weakness": [
            {"dimension": "专业能力", "score": 76.5,
             "gap": "AI 工程化与数据治理实践深度不足，缺少端到端落地指标",
             "suggestion": "系统学习 MLOps / 数据治理体系，主导 1 个 AI 应用从 0 到 1 落地并沉淀指标"},
            {"dimension": "战略思维", "score": 70.0,
             "gap": "对数字化转型的顶层设计与业务价值对齐思考偏少",
             "suggestion": "研读企业数字化转型方法论，参与年度业务战略复盘"},
        ],
        "ability_structure": {
            "战略思维": 70.0, "专业能力": 76.5, "实战经验": 85.0,
            "综合素质": 82.0, "团队协作": 88.0, "学习力": 84.0,
        },
    }


__all__ = [
    "call_analyze_workflow",
    "call_assign_workflow",
    "call_report_workflow",
    "call_score_workflow",
]


# ================================================================ 4) report 综合报告
async def call_report_workflow(inputs: dict[str, Any]) -> dict[str, Any]:
    """综合个性化报告（笔试+面试+学历+年限+岗位）。

    :param inputs: {talent_id, position_title, written_score, interview_score,
                    background, education, work_years, weakness}
    :return: {total_score, job_fit, strengths[], weakness[], suggestions[], conclusion}
    """
    if getattr(settings, 'DIFY_WF_REPORT', ''):
        return await _call_dify_workflow(getattr(settings, 'DIFY_WF_REPORT', ''), inputs)

    # 测试数据兜底
    logger.warning("[report] Dify 报告工作流未配置，返回测试报告")
    written = float(inputs.get("written_score", 0))
    interview = float(inputs.get("interview_score", 0))
    bg = float(inputs.get("background", 78))
    total = round(written*0.5 + interview*0.4 + bg*0.1, 1)
    return {
        "total_score": total,
        "job_fit": round((written + interview) / 2, 1),
        "strengths": ["专业基础扎实", "项目经验匹配"],
        "weakness": inputs.get("weakness", []),
        "suggestions": ["补齐 MLOps 实践", "加强系统设计深度"],
        "conclusion": f"综合得分 {total}，建议进入下一环节。",
    }

