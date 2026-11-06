"""
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

from app.ai.agents.assessment_dify_client import (
    call_analyze_workflow,
    call_assign_workflow,
    call_score_workflow,
)
from app.ai.agents.resume_parser import infer_difficulty, parse_resume, retrieve_questions

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
    # 新增：简历文本 / 候选人背景 / 联系方式
    resume_text: str
    email: str
    education: str
    work_years: float
    # ---- assign ----
    paper_id: str
    questions: list[dict]
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
    """识别简历关键字 -> RAG 检索匹配难度试卷。

    若 state 带 resume_text：解析岗位/学校/薪资/年限 -> 推断难度 -> 题库检索；
    否则回退到 Dify 组卷（测试数据兜底）。
    """
    resume_text = state.get("resume_text")
    if resume_text:
        logger.info("[assign] 基于简历 RAG 组卷 talent_id=%s", state.get("talent_id"))
        keywords = parse_resume(resume_text)
        difficulty = infer_difficulty(keywords)
        questions = retrieve_questions(
            difficulty, keywords.get("skills", []), count=int(state.get("question_count") or 5)
        )
        return {
            "paper_id": f"paper-{difficulty}-{state.get('talent_id', 0)}",
            "questions": questions,
            "resume_keywords": keywords,
            "difficulty": difficulty,
            "status": "assigned",
        }

    # 无简历文本：回退 Dify 组卷
    logger.info("[assign] 无简历文本，走 Dify 组卷")
    out = await call_assign_workflow({
        "talent_id": state.get("talent_id"),
        "position_id": state.get("position_id"),
        "job_level": state.get("job_level"),
        "profession": state.get("profession"),
        "grade": state.get("grade"),
        "question_count": 5,
    })
    return {
        "paper_id": out.get("paper_id", ""),
        "questions": out.get("questions", []),
        "difficulty": state.get("job_level", "中级"),
        "status": "assigned",
    }


# ================================================================ 2. dispatch 下发
async def node_dispatch(state: AssessmentState) -> dict[str, Any]:
    """测评任务下发 + 邮件推送。"""
    logger.info("[dispatch] 下发测评任务 paper_id=%s email=%s",
                state.get("paper_id"), state.get("email"))
    from app.infrastructure.notify.email_client import send_email

    email_result = await send_email(
        state.get("email", ""),
        template="assessment_invite",
        params={
            "name": f"候选人{state.get('talent_id', '')}",
            "paper": state.get("difficulty", ""),
            "url": "/exam/start",
        },
    )
    return {
        "dispatched_at": _now(),
        "notify_channels": ["email"],
        "email_result": email_result,
        "status": "dispatched",
    }


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
    """客观题总分汇总（多维统计模板已按需求剔除）。"""
    answers = state.get("answers") or []
    total = 0.0
    for a in answers:
        if a.get("scored_by") == "ai":
            continue
        total += float(a.get("rule_score") or 0.0)
    return {"rule_total_score": round(total, 2), "status": "stat_done"}


# ================================================================ 5. score 语义打分(Dify)
async def node_score(state: AssessmentState) -> dict[str, Any]:
    """大模型语义打分（Dify）。"""
    out = await call_score_workflow({
        "talent_id": state.get("talent_id"),
        "position_id": state.get("position_id"),
        "job_level": state.get("job_level"),
        "answers": state.get("answers", []),
        "rule_total_score": state.get("rule_total_score", 0.0),
    })
    return {
        "llm_scores": {
            "comprehensive": out.get("comprehensive", 0.0),
            "professional": out.get("professional", 0.0),
            "position_fit": out.get("position_fit", 0.0),
            "subjective_total": out.get("subjective_total", 0.0),
        },
        "score_rationale": out.get("rationale", ""),
        "status": "scored",
    }


# ================================================================ 6. analyze 短板研判(Dify)
async def node_analyze(state: AssessmentState) -> dict[str, Any]:
    """短板研判、能力结构分析。"""
    out = await call_analyze_workflow({
        "talent_id": state.get("talent_id"),
        "job_level": state.get("job_level"),
        "llm_scores": state.get("llm_scores", {}),
    })
    return {
        "weakness": out.get("weakness", []),
        "ability_structure": out.get("ability_structure", {}),
        "level": out.get("level", state.get("grade", "中级")),
        "growth_potential": out.get("growth_potential", ""),
        "status": "analyzed",
    }


# ================================================================ 7. report 综合报告
async def node_report(state: AssessmentState) -> dict[str, Any]:
    """综合笔试 + 面试 + 学历 + 年限 -> 个性化报告。

    输出：综合得分、优势、岗位匹配度、短板。
    权重：笔试(客观+主观) 60% + 面试 30% + 背景(学历/年限) 10%。
    """
    llm_scores = state.get("llm_scores") or {}
    rule = float(state.get("rule_total_score") or 0.0)
    subj = float(llm_scores.get("subjective_total") or 0.0)
    written = round(rule * 0.4 + subj * 0.6, 1)

    interview_score = float(state.get("interview_score") or 0.0)

    # 背景分：学历 + 年限
    edu_tier = {"博士": 95, "硕士": 88, "研究生": 88, "本科": 80, "大专": 70, "专科": 70}.get(
        state.get("education", ""), 78)
    years = float(state.get("work_years") or 0)
    background = round(min(100, edu_tier * 0.7 + min(years * 3, 30)), 1)

    total_score = round(written * WEIGHT_WRITTEN + interview_score * WEIGHT_INTERVIEW + background * WEIGHT_BACKGROUND, 1)

    # 岗位匹配度：笔试专业分 + 面试分
    job_fit = round(float(llm_scores.get("professional", 0)) * FIT_PROFESSIONAL + interview_score * FIT_INTERVIEW, 1)

    # 优势：能力结构分 >=80 的维度
    ability = state.get("ability_structure") or {}
    strengths = [f"{k}({v}分)" for k, v in ability.items() if float(v or 0) >= 80]

    weakness = state.get("weakness") or []
    suggestions = [w.get("suggestion") for w in weakness if w.get("suggestion")]

    report = {
        "summary": "",
        "total_score": total_score,
        "written_score": written,
        "interview_score": interview_score,
        "background_score": background,
        "level": state.get("level", "中级"),
        "job_fit": job_fit,
        "strengths": strengths,
        "weakness": weakness,
        "suggestions": suggestions,
        "growth_potential": state.get("growth_potential", ""),
        "interview_records": state.get("interview_records", []),
    }
    return {
        "report": report,
        "dimension_scores": ability,
        "total_score": total_score,
        "job_fit": job_fit,
        "strengths": strengths,
        "suggestions": suggestions,
        "status": "reported",
        "finished_at": _now(),
    }


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
