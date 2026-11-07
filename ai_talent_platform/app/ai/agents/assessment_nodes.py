"""
标签：[Agent组] [P0]
模块：app/ai/agents/assessment_nodes.py
职责：人才测评分析 Agent 的节点函数（纯函数：state 进、增量 dict 出）。

节点管线：
    assign     识别简历关键字 -> RAG 检索匹配难度试卷
    dispatch   测评任务下发 + 短信推送
    monitor    实时答题监控
    score      大模型语义分析（Dify）
    analyze    短板研判（Dify）
    report     综合笔试+面试+学历+年限 -> 个性化报告
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from pydantic import ValidationError

from app.config.settings import settings
from app.core.exceptions import AIError, BusinessRuleError
from app.schemas.assessment import AnalyzeResult, LLMScoreResult

from app.ai.agents.assessment_dify_client import call_analyze_workflow, call_score_workflow

logger = logging.getLogger(__name__)


class AssessmentState(TypedDict, total=False):
    """测评工作流状态。"""

    # ---- 入参 ----
    assessment_id: int
    talent_id: int
    position_id: int | None
    job_level: str
    profession: str
    grade: str
    stage: str
    answers: list[dict]
    questions: list[dict]
    email_result: dict[str, Any]
    objective_possible: float
    subjective_possible: float
    # 新增：简历文本 / 候选人背景 / 联系方式
    resume_text: str
    email: str
    candidate_name: str
    dimension_rule_scores: dict[str, float]
    education: str
    work_years: float
    # ---- assign ----
    paper_id: str
    resume_keywords: dict[str, Any]
    difficulty: str
    # ---- dispatch ----
    dispatched_at: str
    notify_channels: list[str]
    sms_result: dict[str, Any]
    # ---- monitor ----
    monitoring_flags: dict[str, Any]
    overtime: bool
    abnormal: bool
    # ---- stat（已剔除多维汇总模板，仅保留客观题总分）----
    rule_total_score: float
    # ---- score(Dify) ----
    llm_scores: dict[str, float]
    score_rationale: str
    # ---- analyze(Dify) ----
    weakness: list[dict]
    ability_structure: dict[str, float]
    level: str
    growth_potential: str
    # ---- 面试 ----
    interview_questions: list[str]
    interview_records: list[dict]
    interview_score: float
    # ---- report ----
    report: dict[str, Any]
    suggestions: list[str]
    strengths: list[str]
    job_fit: float
    dimension_scores: dict[str, float]
    total_score: float
    model_name: str
    prompt_version: str
    # ---- 控制 ----
    errors: list[str]
    retries: int
    status: str
    started_at: str
    finished_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ================================================================ 综合评分权重（可按需调整）
# 三项相加必须 = 1.0
WEIGHT_WRITTEN: float = 0.5   # 笔试占比
WEIGHT_INTERVIEW: float = 0.4  # 面试占比
WEIGHT_BACKGROUND: float = 0.1  # 背景(学历/年限)占比
# 岗位匹配度 job_fit：笔试专业分 vs 面试分
FIT_PROFESSIONAL: float = 0.5
FIT_INTERVIEW: float = 0.5


# ================================================================ 1. assign 组卷
async def node_assign(state: AssessmentState) -> dict[str, Any]:
    """读取已由 Service 从数据库装载的试卷；不再使用内置题或模拟试卷。"""
    questions = state.get("questions")
    if not questions:
        raise BusinessRuleError("请先通过测评服务创建并读取数据库试卷")
    return {"paper_id": str(state["assessment_id"]), "questions": questions,
            "difficulty": state.get("job_level", "中级"), "status": "assigned"}


# ================================================================ 2. dispatch 下发
async def node_dispatch(state: AssessmentState) -> dict[str, Any]:
    """发送数据库任务对应的真实考试链接；未配置邮件时如实回传失败。"""
    from app.infrastructure.notify.email_client import send_email
    from app.utils.exam_token import build_exam_url

    base_url = settings.FRONTEND_URL or settings.PUBLIC_BASE_URL
    if not base_url:
        email_result = {"ok": False, "channel": "email", "simulated": False,
                        "msg": "未配置 FRONTEND_URL 或 PUBLIC_BASE_URL，未发送邮件"}
    else:
        email_result = await send_email(
            state.get("email", ""), template="assessment_invite",
            params={"name": state.get("candidate_name") or "您好",
                    "paper": state.get("profession") or "人才测评",
                    "url": build_exam_url(base_url, state["assessment_id"])},
        )
    return {"dispatched_at": _now(), "notify_channels": ["email"],
            "email_result": email_result,
            "status": "dispatched" if email_result.get("ok") else "assign_failed"}


# ================================================================ 3. monitor 监控
async def node_monitor(state: AssessmentState) -> dict[str, Any]:
    """实时答题监控：异常判定。"""
    answers = state.get("answers") or []
    empty_cnt = sum(1 for a in answers if not a.get("answer"))
    abnormal = empty_cnt >= max(1, len(answers) // 2) if answers else False
    return {
        "monitoring_flags": {"empty_answer_count": empty_cnt, "total_questions": len(answers)},
        "overtime": bool(state.get("overtime")),
        "abnormal": abnormal,
        "status": "monitored",
    }


# ================================================================ 4. stat（已剔除多维汇总模板）
async def node_stat(state: AssessmentState) -> dict[str, Any]:
    """仅对数据库装载的标准答案做客观题判分，不信任请求中的 rule_score。"""
    from app.services.assessment_service import AssessmentService

    answers = state.get("answers") or []
    total = 0.0
    objective_possible = 0.0
    subjective_possible = 0.0
    dimensions: dict[str, float] = {}
    for answer in answers:
        points = float(answer["score"])
        if points < 0:
            raise BusinessRuleError("数据库试卷存在负分值")
        dimension = answer.get("dimension") or "未分类"
        if answer.get("type") in ("single", "multiple"):
            objective_possible += points
            expected = AssessmentService._normalize_choice(answer.get("standard_answer"))
            got = AssessmentService._normalize_choice(answer.get("answer"))
            earned = points if expected and got == expected else 0.0
            total += earned
            dimensions[dimension] = round(dimensions.get(dimension, 0.0) + earned, 2)
        else:
            subjective_possible += points
    return {"rule_total_score": round(total, 2), "dimension_rule_scores": dimensions,
            "objective_possible": objective_possible, "subjective_possible": subjective_possible,
            "status": "stat_done"}


async def node_score(state: AssessmentState) -> dict[str, Any]:
    """语义评分只使用已提交的主观题；无主观题时无需调用外部模型。"""
    if not state.get("subjective_possible"):
        return {"llm_scores": {}, "score_rationale": "本次试卷没有主观题", "status": "scored"}
    out = await call_score_workflow({
        "talent_id": state.get("talent_id"), "position_id": state.get("position_id"),
        "job_level": state.get("job_level"),
        "answers": [answer for answer in state.get("answers", []) if answer.get("type") == "text"],
        "rule_total_score": state.get("rule_total_score", 0.0),
    })
    try:
        scores = LLMScoreResult.model_validate(out)
        if not set(out).issuperset(LLMScoreResult.model_fields):
            raise ValueError("missing score fields")
        values = scores.model_dump()
    except (ValidationError, ValueError, TypeError) as exc:
        raise AIError("Dify 语义评分缺少必填字段或分值超出0到100") from exc
    return {"llm_scores": {key: values[key] for key in
                           ("comprehensive", "professional", "position_fit", "subjective_total")},
            "score_rationale": values["rationale"], "status": "scored"}


async def node_analyze(state: AssessmentState) -> dict[str, Any]:
    """把本次真实得分交给已配置的工作流，拒绝缺字段或非法数值。"""
    out = await call_analyze_workflow({
        "talent_id": state.get("talent_id"), "job_level": state.get("job_level"),
        "dimension_scores": state.get("dimension_rule_scores", {}),
        "llm_scores": state.get("llm_scores", {}),
    })
    try:
        if not set(out).issuperset(AnalyzeResult.model_fields):
            raise ValueError("missing analysis fields")
        analyzed = AnalyzeResult.model_validate(out)
        if any(not 0 <= float(value) <= 100 for value in analyzed.ability_structure.values()):
            raise ValueError("invalid dimension score")
    except (ValidationError, ValueError, TypeError) as exc:
        raise AIError("Dify 短板研判结果结构不合法") from exc
    return {"weakness": [item.model_dump() for item in analyzed.weakness],
            "ability_structure": analyzed.ability_structure, "level": analyzed.level,
            "growth_potential": analyzed.growth_potential, "status": "analyzed"}


# ================================================================ 7. report 综合报告
async def node_report(state: AssessmentState) -> dict[str, Any]:
    """按真实题目满分归一化；缺失面试分不按零分处理，重新归一化可用权重。"""
    objective_possible = float(state.get("objective_possible") or 0)
    subjective_possible = float(state.get("subjective_possible") or 0)
    possible = objective_possible + subjective_possible
    if possible <= 0:
        raise BusinessRuleError("试卷总分为零，无法生成评分报告")
    llm_scores = state.get("llm_scores") or {}
    rule = float(state.get("rule_total_score") or 0)
    subjective = float(llm_scores.get("subjective_total") or 0) / 100 * subjective_possible
    written = round((rule + subjective) / possible * 100, 1)
    interview = state.get("interview_score")
    if interview is not None and not 0 <= float(interview) <= 100:
        raise BusinessRuleError("面试得分必须在0到100之间")
    education = state.get("education")
    edu_tier = {"博士": 95, "硕士": 88, "研究生": 88, "本科": 80, "大专": 70, "专科": 70}.get(education)
    background = None
    if edu_tier is not None and state.get("work_years") is not None:
        background = round(min(100, edu_tier * 0.7 + min(float(state["work_years"]) * 3, 30)), 1)
    components = [(written, WEIGHT_WRITTEN)]
    if interview is not None:
        components.append((float(interview), WEIGHT_INTERVIEW))
    if background is not None:
        components.append((background, WEIGHT_BACKGROUND))
    total_score = round(sum(value * weight for value, weight in components) / sum(weight for _, weight in components), 1)
    job_fit = None
    if "professional" in llm_scores:
        job_fit = round(float(llm_scores["professional"]), 1)
    elif state.get("dimension_rule_scores"):
        job_fit = written
    ability = state.get("ability_structure") or {}
    strengths = [f"{key}({value}分)" for key, value in ability.items() if float(value) >= 80]
    weakness = state.get("weakness") or []
    suggestions = [item["suggestion"] for item in weakness if item.get("suggestion")]
    report = {"summary": "", "total_score": total_score, "written_score": written,
              "interview_score": interview, "background_score": background,
              "level": state.get("level"), "job_fit": job_fit, "strengths": strengths,
              "weakness": weakness, "suggestions": suggestions,
              "growth_potential": state.get("growth_potential", ""),
              "interview_records": state.get("interview_records", [])}
    return {"report": report, "dimension_scores": ability, "total_score": total_score,
            "job_fit": job_fit, "strengths": strengths, "suggestions": suggestions,
            "level": state.get("level"), "status": "reported", "finished_at": _now()}


# 节点注册表
NODE_REGISTRY: dict[str, Any] = {
    "assign": node_assign,
    "dispatch": node_dispatch,
    "monitor": node_monitor,
    "stat": node_stat,
    "score": node_score,
    "analyze": node_analyze,
    "report": node_report,
}

EDGES_FULL: list[tuple[str, str]] = [
    ("assign", "dispatch"),
    ("dispatch", "monitor"),
    ("monitor", "stat"),
    ("stat", "score"),
    ("score", "analyze"),
    ("analyze", "report"),
]

__all__ = [
    "EDGES_FULL",
    "NODE_REGISTRY",
    "AssessmentState",
    "node_assign",
    "node_dispatch",
    "node_monitor",
    "node_stat",
    "node_score",
    "node_analyze",
    "node_report",
]
