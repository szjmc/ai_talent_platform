"""
模块：app/ai/agents/resume_parser.py
职责：从简历文本中抽取关键字（岗位/学校/薪资/年限/技能），推断试卷难度，
     并基于题库标签做轻量 RAG 检索，输出匹配难度的试卷。

============================ 替换说明 ============================
- 当前「题库检索」是基于标签/关键词的轻量匹配（本地内置题库）；
  >>> RAG 替换点 <<< 后续接入 Milvus 向量库后，把 retrieve_questions() 内部
      换成「简历关键字 embedding -> 题库集合相似度 top-k」即可，对外签名不变。
- 简历关键字抽取先用规则（正则），后续可换成 LLM 抽取（llm_manager）。
==================================================================
"""

from __future__ import annotations

import json
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
            "school_tier": _SCHOOL_TIER.get(education, 3),
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
    """正则抽取姓名（优先「姓名：」显式标注，其次首行 2-4 个汉字）。"""
    m = re.search(r"姓名[:：]\s*([\u4e00-\u9fa5]{2,4})", text or "")
    if m:
        return m.group(1)
    for line in (text or "").splitlines():
        line = line.strip()
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line):
            return line
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
    school_tier = 3  # 默认本科
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


# ================================================================ 3. 题库（轻量 RAG）
# 每张题带 difficulty 标签与 skill_tags，按难度+技能命中检索
_QUESTION_BANK: list[dict[str, Any]] = [
    {"question_id": "E1", "type": "single", "difficulty": "初级", "skill_tags": ["Java"],
     "content": "Java 中用于定义常量的关键字是？",
     "options": [{"key": "A", "text": "static"}, {"key": "B", "text": "final"},
                 {"key": "C", "text": "const"}, {"key": "D", "text": "volatile"}],
     "answer": "B", "score": 10, "dimension": "专业基础"},
    {"question_id": "E2", "type": "single", "difficulty": "中级", "skill_tags": ["Java", "Spring"],
     "content": "Spring 框架中 Bean 默认作用域是？",
     "options": [{"key": "A", "text": "prototype"}, {"key": "B", "text": "singleton"},
                 {"key": "C", "text": "request"}, {"key": "D", "text": "session"}],
     "answer": "B", "score": 10, "dimension": "专业基础"},
    {"question_id": "E3", "type": "multiple", "difficulty": "高级", "skill_tags": ["微服务", "分布式"],
     "content": "微服务架构下保证分布式事务一致性的常用方案有？",
     "options": [{"key": "A", "text": "2PC"}, {"key": "B", "text": "TCC"},
                 {"key": "C", "text": "本地消息表"}, {"key": "D", "text": "忽略一致性"}],
     "answer": ["A", "B", "C"], "score": 15, "dimension": "架构设计"},
    {"question_id": "E4", "type": "text", "difficulty": "高级", "skill_tags": ["高并发"],
     "content": "请描述你处理过的最高并发系统，QPS 多少、做了哪些优化？",
     "options": None, "answer": None, "score": 25, "dimension": "实战经验"},
    {"question_id": "E5", "type": "text", "difficulty": "专家", "skill_tags": ["AI", "MLOps"],
     "content": "设计一个企业级 LLM 应用平台，从数据治理到模型上线，你会怎么规划？",
     "options": None, "answer": None, "score": 30, "dimension": "架构设计"},
    {"question_id": "E6", "type": "single", "difficulty": "中级", "skill_tags": ["MySQL"],
     "content": "MySQL InnoDB 中，B+ 树索引适合以下哪种场景？",
     "options": [{"key": "A", "text": "全表扫描"}, {"key": "B", "text": "范围查询"},
                 {"key": "C", "text": "模糊查询"}, {"key": "D", "text": "无索引"}],
     "answer": "B", "score": 10, "dimension": "专业基础"},
    {"question_id": "E7", "type": "text", "difficulty": "中级", "skill_tags": ["团队协作"],
     "content": "描述一次你主导跨部门项目协作，遇到的最大冲突及解决方式。",
     "options": None, "answer": None, "score": 20, "dimension": "综合素质"},
]


def retrieve_questions(difficulty: str, skills: list[str], count: int = 5) -> list[dict[str, Any]]:
    """轻量 RAG：按难度 + 技能标签检索题目。

    >>> RAG 替换点 <<< 接入 Milvus 后：
        query_emb = await llm.embed([" ".join(skills) + " " + difficulty])
        hits = await milvus.search(collection="question_bank", vectors=query_emb, top_k=count)
        return [q for q in hits]
    当前用标签匹配打分排序：难度匹配 +2，技能命中 +1，取 top-N。
    """
    scored = []
    for q in _QUESTION_BANK:
        s = 0
        if q.get("difficulty") == difficulty:
            s += 2
        elif q.get("difficulty") in _UPSET(difficulty):
            s += 1
        for sk in (skills or []):
            if any(tag.lower() in sk.lower() for tag in q.get("skill_tags", [])):
                s += 1
        scored.append((s, q))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [q for _, q in scored[:count]]
    logger.info("[rag] 难度=%s 命中题目=%s", difficulty, [q["question_id"] for q in picked])
    return picked


def _UPSET(difficulty: str) -> list[str]:
    order = ["初级", "中级", "高级", "专家"]
    i = order.index(difficulty) if difficulty in order else 1
    return order[max(0, i - 1): i + 1]


# ================================================================ RAG 组卷整合（接 RAG 组题库向量检索）
# 说明：RAG 组已实现「题库 Milvus 向量检索」（QuestionBankService / QuestionBankVectorAdapter），
# 本函数是 resume_parser 预留的「RAG 替换点」正式落位：优先调用 RAG 组题库检索，
# 检索失败 / 无结果 / 权限不足时，自动降级到上面的本地内置题库（retrieve_questions），
# 保证组卷链路在任何环境下都能闭环，且不改动 RAG 组任何代码。
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
    """把 RAG 组 SelectedQuestion 转成组卷统一结构（不含答案字段下发，仅组卷落库用）。"""
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
            "options": [{"key": o.key, "text": o.text} for o in (it.options or [])],
            "answer": it.answer,
            "score": float(it.score or 10),
            "dimension": it.technology or (it.knowledge_points or ["专业能力"])[0],
        })
    return out


async def retrieve_questions_rag(db: Any, user: Any, difficulty: str,
                                 skills: list[str], count: int = 5) -> list[dict[str, Any]]:
    """RAG 组卷入口：优先走 RAG 组题库向量检索，失败降级本地内置题库。

    :param db: 数据库会话（传给 QuestionBankService）
    :param user: 当前用户（RAG 组做权限校验）
    :param difficulty: 用户侧难度（初级/中级/高级/专家）
    :param skills: 简历技能列表
    :param count: 期望题目总数
    """
    try:
        from app.schemas.question_bank import QuestionSearchRequest
        from app.services.question_bank_service import QuestionBankService

        request = QuestionSearchRequest(
            technology=_infer_technology(skills),
            difficulty=_DIFFICULTY_TO_RAG.get(difficulty, "intermediate"),
            query=(" ".join(skills) + " " + difficulty) if skills else difficulty,
            counts={"single": max(1, count // 2), "multiple": max(1, count // 4),
                    "text": max(1, count // 4)},
            include_answers=True,  # 组卷需标准答案落库，前端下发时再剥离
        )
        result = await QuestionBankService(db).search(request, user)
        questions = _to_questions(result.items)
        if questions:
            logger.info("[rag] RAG 组题库命中 %s 题（technology=%s difficulty=%s）",
                        len(questions), request.technology, request.difficulty)
            return questions
        logger.warning("[rag] RAG 组题库无结果，降级本地内置题库")
    except Exception as exc:  # noqa: BLE001 - RAG 不可用不阻断组卷，降级本地
        logger.warning("[rag] RAG 组题库检索失败，降级本地内置题库：%s", exc)
    return retrieve_questions(difficulty, skills, count)


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
        "school_tier": _SCHOOL_TIER.get(education, 3),
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
