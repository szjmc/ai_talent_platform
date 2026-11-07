"""
标签：[Agent组] [P0]
模块：app/ai/agents/resume_parser.py
职责：从简历文本中抽取关键字（岗位/学校/薪资/年限/技能），推断试卷难度，
     并基于题库标签做轻量 RAG 检索，输出匹配难度的试卷。

============================ 替换说明 ============================
- 题目与标准答案只来自已发布且当前身份可读的数据库题库；语义通道失败时
  仍从同一授权范围随机选题，绝不使用代码内置试卷。
- 简历关键字优先走 ResumeAgent/LLM，失败时降级本地规则并显式返回解析模式。
==================================================================
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.ai.utils.output_parser import parse_json

logger = logging.getLogger(__name__)

# 学历层级权重（用于报告综合分析）
_SCHOOL_TIER = {
    "博士": 5, "硕士": 4, "研究生": 4, "本科": 3, "学士": 3,
    "大专": 2, "专科": 2, "高中": 1,
}


# ================================================================ 0. LLM 抽取（优先）
_LLM_SYSTEM = """你是简历信息抽取专家。从候选人简历中抽取结构化信息，只输出 JSON，不要多余解释。
输出格式：
{
  "name": "姓名",
  "email": "邮箱地址",
  "position_title": "应聘岗位名称",
  "seniority": "初级/中级/高级/专家",
  "education": "最高学历(博士/硕士/本科/大专)",
  "school": "毕业院校",
  "salary_min": 数字(期望月薪下限),
  "years": 数字(工作年限),
  "skills": ["技能1","技能2"]
}
无法识别的字段填空字符串或默认值（seniority=中级, salary_min=0, years=0, skills=[]）。"""


async def parse_resume_llm(resume_text: str) -> dict[str, Any]:
    """优先用硅基流动 LLM 抽取简历关键字；失败则回退到正则规则。"""
    try:
        from app.ai.core.llm_manager import llm_manager
        result = await llm_manager.chat(
            [{"role": "system", "content": _LLM_SYSTEM},
             {"role": "user", "content": f"简历：\n{resume_text}"}],
            purpose="resume_parse", temperature=0.1, max_tokens=500,
        )
        data = parse_json(result.content)
        education = data.get("education", "")
        keywords = {
            "name": data.get("name", ""),
            "email": data.get("email", ""),
            "position_title": data.get("position_title", "未识别"),
            "seniority": data.get("seniority", "中级"),
            "education": education or data.get("school", "未识别"),
            "school": data.get("school", ""),
            "school_tier": _SCHOOL_TIER.get(education),
            "salary_min": float(data.get("salary_min", 0) or 0),
            "years": float(data.get("years", 0) or 0),
            "skills": data.get("skills", []),
        }
        logger.info("[resume·LLM] 抽取: %s", keywords)
        return keywords
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 简历抽取失败，回退正则：%s", exc)
        return parse_resume(resume_text)


# ================================================================ 1. 关键字抽取
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract_email(text: str) -> str:
    """正则抽取邮箱（LLM 抽取失败时的兜底）。"""
    m = _EMAIL_RE.search(text or "")
    return m.group(0).strip() if m else ""


def _extract_name(text: str) -> str:
    """正则抽取姓名（优先「姓名：」显式标注，其次首行 2-4 个汉字）。

    兼容常见简历首页写法：「张伟 的个人简历」「张伟-后端工程师」「张伟简历」
    （此前只认「姓名：」与整行纯姓名，这两种写法都抽不出名字，
    离线规则解析下创建测评会报「简历未识别出姓名」）。
    """
    m = re.search(r"姓名[:：]\s*([\u4e00-\u9fa5]{2,4})", text or "")
    if m:
        return m.group(1)
    for line in (text or "").splitlines():
        line = line.strip()
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line):
            return line
    # 「张伟 的个人简历 / 张伟简历 / 张伟-后端工程师」等首行写法
    m = re.match(r"^([\u4e00-\u9fa5]{2,4})(?:\s*的)?(?:个人)?简历\b", (text or "").strip())
    if m:
        return m.group(1)
    m = re.match(r"^([\u4e00-\u9fa5]{2,4})\s*[-—|｜]\s*[\u4e00-\u9fa5A-Za-z]{2,}", (text or "").strip())
    if m:
        return m.group(1)
    return ""


def parse_resume(resume_text: str) -> dict[str, Any]:
    """从简历纯文本抽取结构化关键字。

    返回: {
        name, email, position_title, seniority, education, school,
        school_tier, salary_min, years, skills[]
    }
    """
    text = resume_text or ""

    # --- 邮箱 / 姓名 ---
    email = _extract_email(text)
    name = _extract_name(text)

    # --- 岗位/层级 ---
    seniority = "中级"
    if re.search(r"首席|专家|架构师|技术总监|CTO", text):
        seniority = "专家"
    elif re.search(r"高级|资深|Senior", text, re.I):
        seniority = "高级"
    elif re.search(r"初级|助理|Junior|实习", text, re.I):
        seniority = "初级"

    m = re.search(r"([一-龥A-Za-z]+(?:工程师|专家|架构师|经理|总监))", text)
    position_title = m.group(1) if m else "未识别岗位"

    # --- 学历 / 院校 ---
    education = ""
    school_tier = None  # 未识别学历时不虚构学校层级
    for kw, tier in _SCHOOL_TIER.items():
        if kw in text:
            education = kw
            school_tier = tier
            break

    # --- 薪资（如 25k / 25-35K / 25000） ---
    salary_min = 0.0
    sm = re.search(r"(\d+)\s*[kK](?:\s*[-~]\s*\d+\s*[kK])?", text)
    if sm:
        salary_min = float(sm.group(1)) * 1000
    else:
        sm2 = re.search(r"(\d{4,6})", text)
        if sm2:
            salary_min = float(sm2.group(1))

    # --- 工作年限 ---
    years = 0.0
    ym = re.search(r"(\d+(?:\.\d+)?)\s*年(?:以上|经验)?", text)
    if ym:
        years = float(ym.group(1))

    # --- 技能关键词（命中即抽） ---
    skill_kw = ["Java", "Python", "Spring", "Vue", "React", "MySQL", "Redis",
                "Kubernetes", "Docker", "微服务", "AI", "大模型", "MLOps",
                "数据治理", "机器学习", "分布式", "高并发"]
    skills = [k for k in skill_kw if re.search(re.escape(k), text, re.I)]

    keywords = {
        "name": name,
        "email": email,
        "position_title": position_title,
        "seniority": seniority,
        "education": education or "未识别",
        "school": "",
        "school_tier": school_tier,
        "salary_min": salary_min,
        "years": years,
        "skills": skills,
    }
    logger.info("[resume] 抽取关键字: %s", keywords)
    return keywords


# ================================================================ 2. 难度推断
def infer_difficulty(kw: dict[str, Any]) -> str:
    """根据岗位层级 + 年限 + 薪资推断试卷难度。"""
    score = 0
    score += {"初级": 1, "中级": 2, "高级": 3, "专家": 4}.get(kw.get("seniority"), 2)
    years = float(kw.get("years") or 0)
    if years >= 8:
        score += 2
    elif years >= 4:
        score += 1
    salary = float(kw.get("salary_min") or 0)
    if salary >= 40000:
        score += 2
    elif salary >= 25000:
        score += 1

    if score >= 6:
        return "专家"
    if score >= 4:
        return "高级"
    if score >= 2:
        return "中级"
    return "初级"


# ================================================================ 3. 题库入口
# 题目和标准答案只允许来自持久化题库。同步入口保留给旧调用方，但不再内置试卷。
def retrieve_questions(difficulty: str, skills: list[str], count: int = 5) -> list[dict[str, Any]]:
    """旧同步入口无法执行授权查库；调用异步入口 retrieve_questions_rag。"""
    from app.core.exceptions import BusinessRuleError

    raise BusinessRuleError("请通过 retrieve_questions_rag 从数据库题库组卷，服务不再提供内置试题")


# ================================================================ RAG 组卷整合（接 RAG 组题库向量检索）
# 说明：RAG 组已实现「题库 Milvus 向量检索」（QuestionBankService / QuestionBankVectorAdapter），
# 本函数是 resume_parser 预留的「RAG 替换点」正式落位：优先调用 RAG 组题库检索，
# 语义通道不可用或候选题不足时，从同一数据库、同一授权范围随机补足。
# 权限错误直接向上抛出；题库不足明确报错，绝不返回代码内置题目。
_DIFFICULTY_TO_RAG = {
    "初级": "beginner", "中级": "intermediate", "高级": "advanced", "专家": "advanced",
}

# 简历技能关键词 -> RAG 组题库技术方向（technology）
_TECH_MAP = {
    "java": "java", "spring": "java", "python": "python", "vue": "frontend",
    "react": "frontend", "mysql": "database", "redis": "database",
    "kubernetes": "devops", "docker": "devops", "微服务": "microservice",
    "分布式": "distributed", "高并发": "performance", "ai": "ai",
    "大模型": "ai", "机器学习": "ai", "mlops": "ai", "数据治理": "data",
}


def _infer_technology(skills: list[str]) -> str:
    """从简历技能推断 RAG 组题库的技术方向，默认 ai（数字化平台方向）。"""
    for sk in (skills or []):
        for kw, tech in _TECH_MAP.items():
            if kw.lower() in str(sk).lower():
                return tech
    return "ai"


def _to_questions(items: list[Any]) -> list[dict[str, Any]]:
    """把题库记录转换成试卷快照；向量相似度绝不能当作题目分值。"""
    out = []
    for it in items:
        q_type = it.question_type
        if q_type in ("true_false",):
            q_type = "single"
        elif q_type in ("fill_blank", "coding"):
            q_type = "text"
        out.append({
            "type": q_type if q_type in ("single", "multiple", "text") else "single",
            "content": it.stem,
            "options": ([{"key": o.key, "text": o.text} for o in it.options] if it.options else None),
            "answer": it.answer,
            "score": 10.0,  # 题目分值为确定性评分规则，与检索相似度无关
            "dimension": it.technology or (it.knowledge_points or ["专业能力"])[0],
        })
    return out


async def retrieve_questions_rag(db: Any, user: Any, difficulty: str,
                                 skills: list[str], count: int = 5) -> list[dict[str, Any]]:
    """从已发布且当前用户可读的数据库题目组卷，语义检索失败时使用数据库随机选题。"""
    from app.core.exceptions import AIError, BusinessRuleError, ExternalServiceError
    from app.schemas.question_bank import QuestionSearchRequest
    from app.services.question_bank_service import QuestionBankService

    if db is None or user is None:
        raise BusinessRuleError("组卷需要数据库会话和可信登录身份")
    if not 1 <= count <= 100:
        raise BusinessRuleError("题目数量必须在1到100之间")
    multiple = max(1, count // 4) if count >= 3 else 0
    text = max(1, count // 4) if count >= 2 else 0
    request = QuestionSearchRequest(
        technology=_infer_technology(skills),
        difficulty=_DIFFICULTY_TO_RAG.get(difficulty, "intermediate"),
        query=(" ".join(skills) + " " + difficulty) if skills else difficulty,
        counts={"single": count - multiple - text, "multiple": multiple, "text": text},
        include_answers=True,
    )
    service = QuestionBankService(db)
    try:
        result = await service.search(request, user)
    except (AIError, ExternalServiceError) as exc:
        logger.warning("题库语义通道不可用，改用授权范围内的数据库选题：%s", type(exc).__name__)
        result = await service.search(request.model_copy(update={"query": None}), user)
    selected = list(result.items)
    shortage = dict(result.shortage)
    # 某一题型不足时用其他已发布题型补足，仍保持相同技术方向、难度和权限范围。
    while shortage and len(selected) < count:
        available = [kind for kind in ("single", "multiple", "text") if shortage.get(kind, 0) > 0]
        if not available:
            break
        kind = available[0]
        request_more = request.model_copy(update={
            "query": None,
            "counts": {kind: min(shortage[kind], count - len(selected))},
            "exclude_question_ids": [item.question_id for item in selected],
        })
        more = await service.search(request_more, user)
        selected.extend(more.items)
        shortage[kind] -= len(more.items)
        if not more.items:
            shortage.pop(kind)
    if len(selected) != count:
        raise BusinessRuleError(
            f"当前权限范围内符合技术方向与难度的已发布题目不足：需要{count}题，实际{len(selected)}题"
        )
    logger.info("数据库题库组卷完成：technology=%s difficulty=%s count=%s",
                request.technology, request.difficulty, len(selected))
    return _to_questions(selected)


# ================================================================ 人才档案解析 Agent 整合
# 说明：同事「人才档案解析 Agent」（ResumeAgent，注册名 resume）负责结构化解析简历，
# 输出 ResumeParsedData（basic_info/education/work_experience/projects/skills/warnings）。
# 本模块（测评组）通过其公开入口调用，映射为组卷所需关键字；解析失败自动降级本地规则。
def _extract_salary(text: str) -> float:
    """从简历原文正则抽取期望月薪下限（元），无则 0。"""
    m = re.search(r"(\d+)\s*[kK]?\s*[-~至]\s*(\d+)\s*[kK]", text or "")
    if m:
        return float(m.group(1)) * 1000
    sm = re.search(r"(\d+)\s*[kK]", text or "")
    if sm:
        return float(sm.group(1)) * 1000
    sm2 = re.search(r"(\d{4,6})", text or "")
    if sm2:
        return float(sm2.group(1))
    return 0.0


def _seniority_of(title: str, years: float) -> str:
    """依据岗位 title + 工作年限推断层级（初级/中级/高级/专家）。"""
    t = title or ""
    if re.search(r"首席|专家|架构师|技术总监|CTO|总监|负责人|经理|主管", t):
        return "专家"
    if re.search(r"高级|资深|Senior", t, re.I):
        return "高级"
    if re.search(r"初级|助理|Junior|实习", t, re.I):
        return "初级"
    if years >= 8:
        return "高级"
    if years >= 3:
        return "中级"
    return "初级"


def _map_agent_result(parsed: dict[str, Any], resume_text: str) -> dict[str, Any]:
    """ResumeParsedData -> 组卷关键字（与 parse_resume 返回结构一致）。"""
    basic = parsed.get("basic_info") or {}
    skills = parsed.get("skills") or []
    years = float(basic.get("work_years") or 0)
    education = basic.get("education") or ""
    school = basic.get("school") or ""
    position_title = basic.get("current_position") or ""

    if not position_title:
        for exp in parsed.get("work_experience") or []:
            if exp.get("title"):
                position_title = exp["title"]
                break
    if not position_title:
        position_title = "未识别岗位"

    return {
        "name": basic.get("name") or _extract_name(resume_text),
        "email": basic.get("email") or _extract_email(resume_text),
        "position_title": position_title,
        "seniority": _seniority_of(position_title, years),
        "education": education or "未识别",
        "school": school,
        "school_tier": _SCHOOL_TIER.get(education),
        "salary_min": _extract_salary(resume_text),
        "years": years,
        "skills": skills,
    }


async def parse_resume_with_mode(db: Any, user: Any, resume_text: str) -> tuple[dict[str, Any], str]:
    """解析简历并返回 (关键字, 解析模式)，模式 ∈ {"agent", "rule"}。

    优先走 ResumeAgent（LLM 结构化抽取，字段更全）；Agent 不可用 / 无权限 / LLM 异常时
    自动降级到 parse_resume（本地正则），保证组卷链路不中断，同时向上透出实际使用的模式，
    便于接口返回「解析模式」字段（agent=大模型成功 / rule=已降级规则），排障一目了然。
    """
    try:
        from app.ai.agents.resume_agent import ResumeAgent
        from app.services.talent_access import actor_context

        ctx = actor_context(user) if user is not None else None
        result = await ResumeAgent(db=db, context=ctx).run({"text": resume_text})
        if result.get("ok"):
            parsed = result.get("result") or {}
            mapped = _map_agent_result(parsed, resume_text)
            logger.info("[resume] 人才档案解析 Agent 成功：%s", mapped)
            return mapped, "agent"
        logger.warning("[resume] 人才档案解析 Agent 失败，降级本地规则：%s", result.get("error"))
    except Exception as exc:  # noqa: BLE001 - Agent 不可用不阻断组卷
        logger.warning("[resume] 人才档案解析 Agent 调用异常，降级本地规则：%s", exc)
    return parse_resume(resume_text), "rule"


async def parse_resume_via_agent(db: Any, user: Any, resume_text: str) -> dict[str, Any]:
    """调用「人才档案解析 Agent」解析简历，映射为组卷关键字；失败降级本地规则。

    兼容入口：仅返回关键字（丢弃解析模式），供脚本/既有调用方使用；
    需要感知「是否走了 LLM」时请改用 parse_resume_with_mode。
    """
    kw, _mode = await parse_resume_with_mode(db, user, resume_text)
    return kw


__all__ = ["parse_resume", "parse_resume_llm", "infer_difficulty",
           "retrieve_questions", "retrieve_questions_rag", "parse_resume_via_agent",
           "parse_resume_with_mode"]
