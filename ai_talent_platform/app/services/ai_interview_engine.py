"""
模块：app/services/ai_interview_engine.py
职责：AI 数字人面试的**纯函数引擎**：人设、问题计划、回答量化评分、综合评估。

标签：[Agent组] [P1]（AI 面试核心算法：离线可测、无数据库/无网络依赖）

================================ 设计原则 ================================
1. 可解释优先：分数来自可复核的信号（回答长度、结构词、关键词命中、数据点、
   相关性），每个分数都能在 signals 中回溯；不做「模型拍脑袋给分」。
2. 离线可用：不调用大模型也能完整跑通「生成计划 -> 逐题评分 -> 出报告」；
   大模型只在 Service 层做「问题个性化 / 反馈润色」的可选增强，失败自动降级。
3. 真实优先：不编造候选人未说过的经历；未作答题目记 None 而不是 0，
   综合分只对已作答维度取加权平均，并单独给出完成率。
4. 一题一追问：回答过短/证据不足时最多追问一次，追问文本随问题计划快照，
   追问内容不重复计为新的主问题。
=========================================================================
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, NamedTuple

# ------------------------------------------------------------------ 维度
DIMENSIONS: tuple[str, ...] = (
    "professional",
    "problem_solving",
    "communication",
    "culture_fit",
    "potential",
)

DIMENSION_LABELS: dict[str, str] = {
    "professional": "专业能力",
    "problem_solving": "问题解决",
    "communication": "沟通表达",
    "culture_fit": "协作与文化",
    "potential": "成长潜力",
}

DIMENSION_WEIGHTS: dict[str, float] = {
    "professional": 1.30,
    "problem_solving": 1.20,
    "communication": 1.00,
    "culture_fit": 1.00,
    "potential": 0.80,
}

DIMENSION_STRENGTHS: dict[str, str] = {
    "professional": "专业基础扎实，能结合具体场景讲清方法与取舍",
    "problem_solving": "问题定位与推进思路清晰，具备拆解复杂问题的能力",
    "communication": "表达有条理，信息组织与重点呈现较好",
    "culture_fit": "协作意识与换位思考较好，能处理分歧并推动结果",
    "potential": "成长动机明确，有持续学习与自我迭代的迹象",
}

DIMENSION_TIPS: dict[str, str] = {
    "professional": "系统梳理岗位核心技术栈，准备 1-2 个能讲清「问题-方案-结果」的深度案例",
    "problem_solving": "练习用「现象-假设-验证-复盘」拆解问题，并明确优先级判断依据",
    "communication": "采用「背景-任务-行动-结果」结构表达，先说结论再补细节",
    "culture_fit": "准备跨部门协作与分歧处理的真实案例，突出你的具体动作与影响",
    "potential": "明确 1-2 年成长目标，并给出可验证的学习计划与实践证据",
}

LEVEL_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (90.0, "卓越"),
    (80.0, "优秀"),
    (70.0, "良好"),
    (60.0, "合格"),
)

RECOMMENDATION_LABELS: dict[str, str] = {
    "strongly_recommend": "强烈推荐：建议优先进入下一轮",
    "recommend": "推荐：建议进入下一轮",
    "consider": "可考虑：建议人工复核关键经历后再决定",
    "not_recommend": "暂不推荐：建议补充其他评估方式",
    "insufficient": "样本不足：需补面或人工复核",
}

# ------------------------------------------------------------------ 数字人人设
PERSONAS: dict[str, dict[str, Any]] = {
    "friendly_hr": {
        "name": "晓岚",
        "title": "资深招聘官 · AI 数字人",
        "style": "warm",
        "avatar_theme": "aurora",
        "voice_hint": "female",
        "language": "zh-CN",
        "tagline": "关注你的真实经历与成长动机",
    },
    "tech_lead": {
        "name": "陈拓",
        "title": "技术负责人 · AI 数字人",
        "style": "professional",
        "avatar_theme": "cyan",
        "voice_hint": "male",
        "language": "zh-CN",
        "tagline": "深挖技术深度与工程判断",
    },
    "strict_expert": {
        "name": "沈砚",
        "title": "首席面试专家 · AI 数字人",
        "style": "rigorous",
        "avatar_theme": "violet",
        "voice_hint": "male",
        "language": "zh-CN",
        "tagline": "高标准、重证据的结构化面试",
    },
    "global_interviewer": {
        "name": "Ava",
        "title": "Global Talent Partner · AI",
        "style": "global",
        "avatar_theme": "amber",
        "voice_hint": "female",
        "language": "en-US",
        "tagline": "Bilingual, competency-based interview",
    },
}

PERSONA_ALIASES = {
    "hr": "friendly_hr",
    "tech": "tech_lead",
    "expert": "strict_expert",
    "global": "global_interviewer",
}

DEFAULT_DIMENSION_KEYWORDS: dict[str, list[str]] = {
    "professional": ["项目", "负责", "方案", "技术", "结果", "难点"],
    "problem_solving": ["问题", "分析", "排查", "方案", "优先级", "复盘"],
    "communication": ["背景", "目标", "行动", "结果", "团队", "沟通"],
    "culture_fit": ["协作", "分歧", "沟通", "团队", "推进", "结果"],
    "potential": ["学习", "成长", "目标", "计划", "实践", "复盘"],
}


# ================================================================== 基础工具
def resolve_persona(value: str | None) -> str:
    """把别名 / 空值归一为内置人设 key（未知值回落 friendly_hr）。"""
    key = (value or "").strip()
    key = PERSONA_ALIASES.get(key, key)
    return key if key in PERSONAS else "friendly_hr"


def persona_profile(value: str | None) -> dict[str, Any]:
    """返回人设的可展示信息（不会暴露内部实现细节）。"""
    key = resolve_persona(value)
    persona = PERSONAS[key]
    return {
        "key": key,
        "name": persona["name"],
        "title": persona["title"],
        "style": persona["style"],
        "avatar_theme": persona["avatar_theme"],
        "voice_hint": persona["voice_hint"],
        "language": persona["language"],
        "tagline": persona["tagline"],
    }


def parse_skills(raw: Any, limit: int = 6) -> list[str]:
    """清洗技能标签：去空、去重、限长、限制数量。"""
    if not isinstance(raw, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text or len(text) > 30:
            continue
        if text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _short(text: Any, limit: int = 80) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _text_len(text: str) -> tuple[int, int]:
    """返回 (中文字符数, 英文词数)，用于中英双语阈值判断。"""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text or ""))
    return chinese, words


def _is_en(language: str | None) -> bool:
    return str(language or "").lower().startswith("en")


# ================================================================== 问题计划
def _question(
    dimension: str,
    text: str,
    intent: str,
    keywords: list[str] | None = None,
    follow_up: str = "",
    weight: float = 1.0,
) -> dict[str, Any]:
    """构造一道面试题（字段固定，便于前端与报告复用）。"""
    return {
        "id": 0,  # 最终由 build_interview_plan 统一编号
        "dimension": dimension,
        "text": text.strip(),
        "intent": intent.strip(),
        "keywords": [str(k).strip() for k in (keywords or DEFAULT_DIMENSION_KEYWORDS[dimension]) if str(k).strip()],
        "follow_up": follow_up.strip(),
        "weight": round(float(weight), 2),
    }


def _zh_plan(profile: dict[str, Any], position: dict[str, Any], count: int, difficulty: str) -> list[dict[str, Any]]:
    name = str(profile.get("name") or "候选人").strip()
    years = int(profile.get("work_years") or 0)
    skills = parse_skills(profile.get("skills") or position.get("skills"))
    company = str(profile.get("current_company") or "").strip()
    current_role = str(profile.get("current_position") or "").strip()
    position_title = str(position.get("title") or profile.get("position_title") or "目标岗位").strip()
    skill1 = skills[0] if skills else ""
    skill2 = skills[1] if len(skills) > 1 else ""

    intro = _question(
        "communication",
        "先请你做一个简单的自我介绍：重点讲讲你的核心经历、最擅长的事情，以及为什么对这个岗位感兴趣。",
        "考察自我认知、表达结构与岗位动机",
        ["经历", "擅长", "项目", "成果", "岗位"],
        "如果只用三句话概括你的核心竞争力，你会怎么说？",
        0.8,
    )
    if years < 2:
        project = _question(
            "professional",
            "请讲一个你在校或实习中从零推进一件事情的真实经历：目标是什么、你做了哪些关键动作、最终结果如何？",
            "考察项目参与度、行动力与结果意识",
            ["目标", "负责", "行动", "结果", "复盘"],
            "这件事如果重做一次，你会改变哪个决定？为什么？",
            1.2,
        )
    elif company and current_role:
        project = _question(
            "professional",
            f"你在{company}担任{current_role}期间，最有代表性的一个项目是什么？请说明项目目标、你承担的角色和最终结果。",
            "考察真实项目深度、个人贡献与结果",
            skills + ["项目", "负责", "结果", "指标", "挑战"],
            "这个项目里最困难的一个技术或协作决策是什么？你当时怎么权衡的？",
            1.3,
        )
    elif company:
        project = _question(
            "professional",
            f"你在{company}期间最有代表性的一个项目是什么？请说明项目目标、你承担的角色和最终结果。",
            "考察真实项目深度、个人贡献与结果",
            skills + ["项目", "负责", "结果", "指标", "挑战"],
            "这个项目里最困难的一个技术或协作决策是什么？你当时怎么权衡的？",
            1.3,
        )
    else:
        project = _question(
            "professional",
            "请介绍一个你主导或深度参与的代表性项目，说明项目目标、你的角色、关键动作和最终结果。",
            "考察真实项目深度、个人贡献与结果",
            skills + ["项目", "负责", "结果", "指标", "挑战"],
            "这个项目里最困难的一个技术或协作决策是什么？你当时怎么权衡的？",
            1.3,
        )

    skill_q = _question(
        "professional",
        f"你最熟悉的 {skill1} 相关经验是什么？请结合一个具体场景，讲讲你如何用它解决业务问题。"
        if skill1
        else f"针对{position_title}这个方向，你最擅长的一项专业能力是什么？请用一个具体场景说明你如何用它解决业务问题。",
        "考察专业技能的真实使用场景与深度",
        [skill1] if skill1 else ["专业", "场景", "方案", "效果", "难点"],
        "如果让你给刚入门的同事讲清楚这项能力的关键点，你会怎么讲？",
        1.2,
    )
    skill_q2 = _question(
        "professional",
        f"再挑一个你写过/做过的 {skill2} 案例：当时的技术方案是什么，为什么不用更简单的做法？"
        if skill2
        else "请再举一个你在专业上做过取舍的例子：当时有哪些备选方案，你最终为什么选了这个？",
        "考察技术判断、方案对比与取舍能力",
        ([skill2] if skill2 else []) + ["方案", "对比", "取舍", "原因", "结果"],
        "如果资源减半，你还会坚持这个方案吗？会怎么调整？",
        1.1,
    )
    scenario = _question(
        "problem_solving",
        f"假设你入职后负责{position_title}的一项关键目标，上线前一周发现核心指标明显低于预期，你会如何定位问题并推进解决？",
        "考察问题拆解、优先级判断与跨团队推进",
        ["排查", "定位", "假设", "优先级", "方案", "复盘"],
        "如果时间只够处理一个问题，你会先解决哪一个？为什么？",
        1.2,
    )
    pressure = _question(
        "problem_solving",
        "如果同时有三件紧急任务、资源只够先做一件，你会如何排序并向相关方沟通？",
        "考察压力下的决策与向上沟通",
        ["排序", "影响", "沟通", "资源", "取舍"],
        "如果被你排在后面的任务负责人强烈反对，你会怎么处理？",
        1.0,
    )
    collaboration = _question(
        "culture_fit",
        "请讲一次你和同事或跨部门意见不一致的经历：分歧点是什么，你做了什么，最后结果如何？",
        "考察协作意识、冲突处理与结果导向",
        ["分歧", "沟通", "协作", "方案", "结果"],
        "如果对方始终不接受你的方案，你会怎么处理？",
        1.0,
    )
    failure = _question(
        "culture_fit",
        "请讲一次失败或没有达标的经历：你从中学到了什么，之后做了哪些改变？",
        "考察复盘能力、责任感与自我修正",
        ["失败", "原因", "复盘", "改变", "结果"],
        "这件事对你后来的工作方式产生了什么具体影响？",
        0.9,
    )
    potential = _question(
        "potential",
        "未来一到两年，你希望自己在哪些方面成长？为此已经做了哪些准备？",
        "考察成长动机、学习行动与规划清晰度",
        ["成长", "目标", "学习", "计划", "实践"],
        "如果入职后发现能力差距比预期大，你会怎么补齐？",
        0.9,
    )
    motivation = _question(
        "potential",
        f"你为什么选择我们这个团队和{position_title}这个岗位？你觉得自己最匹配的三点是什么？",
        "考察岗位动机、自我认知与匹配度",
        ["岗位", "团队", "匹配", "优势", "业务"],
        "如果同时拿到其他 offer，你会用什么标准做选择？",
        0.8,
    )
    if difficulty == "senior":
        strategy = _question(
            "problem_solving",
            f"从你的过往经验看，{position_title}这个方向未来一年最大的挑战是什么？你会如何应对？",
            "考察行业理解、前瞻判断与策略思维",
            ["趋势", "挑战", "策略", "落地", "指标"],
            "如果只能先做一个最关键的举措，你会选什么？",
            1.1,
        )
    else:
        strategy = None

    if count <= 3:
        pool = [intro, project, potential]
    elif count == 4:
        pool = [intro, project, scenario, potential]
    elif count == 5:
        pool = [intro, project, skill_q, scenario, potential]
    else:
        pool = [intro, project, skill_q, scenario, collaboration, potential]
        extras = [skill_q2, motivation, pressure, failure]
        if strategy is not None:
            extras.insert(0, strategy)
        pool.extend(extras[: max(0, count - 6)])
    return pool[:count]


def _en_plan(profile: dict[str, Any], position: dict[str, Any], count: int, difficulty: str) -> list[dict[str, Any]]:
    years = int(profile.get("work_years") or 0)
    skills = parse_skills(profile.get("skills") or position.get("skills"))
    position_title = str(position.get("title") or profile.get("position_title") or "this role").strip()
    skill1 = skills[0] if skills else ""
    skill2 = skills[1] if len(skills) > 1 else ""

    intro = _question(
        "communication",
        "Let's start with a brief introduction: walk me through your core experience, what you do best, and why you are interested in this role.",
        "Assess self-awareness, structure and motivation",
        ["experience", "strength", "project", "result", "role"],
        "If you had to sum up your core strength in three sentences, what would you say?",
        0.8,
    )
    project = _question(
        "professional",
        ("Tell me about the most representative project from your time at your current company. "
         "What was the goal, what was your role, and what was the outcome?")
        if years >= 2
        else ("Tell me about a project you drove from scratch at university or during an internship. "
              "What was the goal, what did you do, and what was the outcome?"),
        "Assess depth of real project experience and personal contribution",
        skills + ["project", "role", "result", "metric", "challenge"],
        "What was the hardest technical or cross-team decision in that project, and how did you make it?",
        1.3,
    )
    skill_q = _question(
        "professional",
        (f"What is your hands-on experience with {skill1}? Please use a concrete scenario to explain "
         "how you applied it to solve a business problem.")
        if skill1
        else (f"What is your strongest professional capability for the {position_title} role? "
              "Please explain with a concrete scenario."),
        "Assess real-world depth of professional skills",
        [skill1] if skill1 else ["professional", "scenario", "solution", "impact", "trade-off"],
        "How would you explain the key points of this capability to a junior colleague?",
        1.2,
    )
    skill_q2 = _question(
        "professional",
        (f"Tell me about another project where you used {skill2}. What was the technical approach, "
         "and why not choose a simpler option?")
        if skill2
        else ("Tell me about a professional trade-off you made. What alternatives did you consider, "
              "and why did you choose your approach?"),
        "Assess technical judgement and trade-off reasoning",
        ([skill2] if skill2 else []) + ["approach", "alternative", "trade-off", "reason", "result"],
        "If you had half the resources, would you still choose the same approach? How would you adapt?",
        1.1,
    )
    scenario = _question(
        "problem_solving",
        (f"Imagine you join us and own a key goal for the {position_title} role. One week before launch, "
         "a core metric is clearly below target. How would you diagnose the issue and drive a solution?"),
        "Assess problem decomposition, prioritisation and cross-team execution",
        ["diagnose", "hypothesis", "priority", "solution", "review"],
        "If you only had time to fix one thing, what would you tackle first, and why?",
        1.2,
    )
    pressure = _question(
        "problem_solving",
        "If you had three urgent tasks and only enough capacity to do one, how would you prioritise and communicate it?",
        "Assess decision-making under pressure and upward communication",
        ["priority", "impact", "communication", "resource", "trade-off"],
        "What would you do if the owner of a deprioritised task strongly disagreed?",
        1.0,
    )
    collaboration = _question(
        "culture_fit",
        ("Tell me about a time you disagreed with a colleague or another team. What was the disagreement, "
         "what did you do, and what was the outcome?"),
        "Assess collaboration, conflict handling and result orientation",
        ["disagreement", "communication", "collaboration", "solution", "result"],
        "What would you do if the other side never accepted your proposal?",
        1.0,
    )
    failure = _question(
        "culture_fit",
        "Tell me about a failure or a missed target. What did you learn, and what did you change afterwards?",
        "Assess accountability, reflection and self-correction",
        ["failure", "cause", "reflection", "change", "result"],
        "How did that experience specifically change the way you work?",
        0.9,
    )
    potential = _question(
        "potential",
        "Where do you want to grow over the next one to two years, and what have you already done to prepare?",
        "Assess growth motivation, learning actions and planning",
        ["growth", "goal", "learning", "plan", "practice"],
        "What would you do if you found the capability gap larger than expected after joining?",
        0.9,
    )
    motivation = _question(
        "potential",
        f"Why this team and this {position_title} role? What are the three strongest reasons you are a good fit?",
        "Assess motivation, self-awareness and role fit",
        ["role", "team", "fit", "strength", "business"],
        "If you had another offer, what criteria would you use to decide?",
        0.8,
    )
    strategy = _question(
        "problem_solving",
        (f"Based on your experience, what is the biggest challenge for {position_title} over the next year, "
         "and how would you approach it?"),
        "Assess domain insight, forward thinking and strategy",
        ["trend", "challenge", "strategy", "execution", "metric"],
        "If you could only take one key action first, what would it be?",
        1.1,
    )

    if count <= 3:
        pool = [intro, project, potential]
    elif count == 4:
        pool = [intro, project, scenario, potential]
    elif count == 5:
        pool = [intro, project, skill_q, scenario, potential]
    else:
        pool = [intro, project, skill_q, scenario, collaboration, potential]
        extras = [skill_q2, motivation, pressure, failure]
        if difficulty == "senior":
            extras.insert(0, strategy)
        pool.extend(extras[: max(0, count - 6)])
    return pool[:count]


def build_interview_plan(
    profile: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    *,
    count: int = 6,
    difficulty: str = "middle",
    language: str = "zh-CN",
) -> list[dict[str, Any]]:
    """生成结构化面试计划（纯规则、可复现）。

    - count 夹到 [3, 10]；每个问题带 id / dimension / intent / keywords / follow_up / weight；
    - 候选人画像与岗位要求只用于**措辞个性化**，不编造任何未提供的经历；
    - 语言为 en-US 时使用英文题库，其余按中文处理。
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 6
    count = max(3, min(10, count))
    profile = profile or {}
    position = position or {}
    plan = _en_plan(profile, position, count, difficulty) if _is_en(language) else _zh_plan(
        profile, position, count, difficulty
    )
    for index, item in enumerate(plan):
        item["id"] = index + 1
    return plan


# ================================================================== 回答评分
class AnswerScore(NamedTuple):
    score: float
    signals: dict[str, Any]
    feedback: str


_STRUCTURE_WORDS = (
    "首先", "其次", "然后", "接着", "最后", "第一", "第二", "第三", "一是", "二是",
    "因为", "所以", "因此", "但是", "不过", "总结", "结果", "背景", "目标", "行动",
    "first", "second", "then", "finally", "because", "therefore", "however", "result",
    "background", "goal", "action", "outcome", "summary",
)


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    hits: list[str] = []
    for keyword in keywords:
        key = str(keyword or "").strip()
        if not key:
            continue
        if key.lower() in lowered:
            hits.append(key)
    return hits


def _token_set(text: str, language: str | None) -> set[str]:
    """中英通用的粗粒度 token：中文取二元字组，英文取小写单词。"""
    value = text or ""
    if _is_en(language):
        return {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z'\-]+", value) if len(token) > 2}
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    bigrams = {chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))}
    words = {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z'\-]+", value) if len(token) > 2}
    return bigrams | words


def _relevance(answer: str, question: dict[str, Any], language: str | None) -> float:
    """回答切题度（0-1）。

    单纯用「命中题干 token / 题干 token 总数」会系统性压低正常回答：题干往往很长，
    而候选人不会逐字复述问题。这里改为三路融合 + 实质性回答的切题基线：

    1. 关键点覆盖率（题目 keywords）——最直接的切题证据；
    2. 题干与回答的 overlap coefficient（交集 / 较小集合）——容忍不同措辞；
    3. 维度词典覆盖率——回答是否落在该维度常见语义范围；
    4. relevance_floor：篇幅足够、结构完整的回答至少视为「部分切题」，
       避免因为没有命中少数几个关键词而被判成跑题（跑题仍由低分反馈暴露）。
    """
    text = str(answer or "")
    keywords = [str(k).strip() for k in question.get("keywords") or [] if str(k).strip()]
    answer_tokens = _token_set(text, language)
    target_tokens = _token_set(str(question.get("text") or ""), language)
    if not answer_tokens:
        return 0.0

    keyword_cov = len(_keyword_hits(text, keywords)) / len(keywords) if keywords else 0.0
    overlap = (
        len(answer_tokens & target_tokens) / max(1, min(len(answer_tokens), len(target_tokens)))
        if target_tokens else 0.0
    )
    dimension_terms = DEFAULT_DIMENSION_KEYWORDS.get(str(question.get("dimension") or ""), [])
    dimension_cov = (
        len(_keyword_hits(text, dimension_terms)) / len(dimension_terms) if dimension_terms else 0.0
    )
    lexical = 0.45 * keyword_cov + 0.35 * overlap + 0.20 * dimension_cov

    chinese, words = _text_len(text)
    if (words >= 40) if _is_en(language) else (chinese >= 60):
        floor = 0.28
    elif (words >= 20) if _is_en(language) else (chinese >= 35):
        floor = 0.18
    else:
        floor = 0.0
    return round(min(1.0, max(lexical, floor)), 4)


def _length_points(chinese: int, words: int, language: str | None) -> int:
    """回答充分度：鼓励把「背景-行动-结果」讲完整，而不是堆砌字数。"""
    if _is_en(language):
        if words < 8:
            return 2
        if words < 20:
            return 6
        if words < 40:
            return 11
        if words < 70:
            return 16
        return 20
    if chinese < 20:
        return 2
    if chinese < 40:
        return 6
    if chinese < 80:
        return 11
    if chinese < 150:
        return 16
    return 20


def score_answer(
    question: dict[str, Any],
    answer: str,
    *,
    duration_seconds: int = 0,
    language: str = "zh-CN",
) -> AnswerScore:
    """对单题回答做可解释评分（0-100）。

    评分构成（全部可在 signals 中核对，满分 96，留出「接近完美」的表达空间）：
        base 34（有效回答的基准分）
        + 充分度 0-20（中文字数 / 英文词数）
        + 切题度 0-18（关键词覆盖 / 题干重合 / 维度词典，带实质回答基线）
        + 结构 0-10（首先/其次/因为/所以等结构词）
        + 证据 0-8（数字、百分比、指标等可验证信息）
        + 细节 0-6（多句表达 + 词汇丰富度）
    过短回答有明确上限，避免「一句话拿高分」；堆砌关键词也拿不到满分。
    """
    text = str(answer or "").strip()
    signals: dict[str, Any] = {
        "char_count": len(text),
        "chinese_chars": 0,
        "word_count": 0,
        "sentence_count": 0,
        "duration_seconds": max(0, int(duration_seconds or 0)),
        "structure_hits": [],
        "keywords_hit": [],
        "missing_keywords": [],
        "numbers": [],
        "relevance": 0.0,
        "relevance_floor": 0.0,
    }
    if not text:
        return AnswerScore(0.0, signals, "未作答，无法评分。")

    chinese, words = _text_len(text)
    sentences = len(re.findall(r"[。！？!?.;；\n]+", text)) + 1
    structure_hits = [word for word in _STRUCTURE_WORDS if word in text.lower()]
    keywords = [str(k) for k in question.get("keywords") or [] if str(k).strip()]
    keyword_hits = _keyword_hits(text, keywords)
    missing = [k for k in keywords if k not in keyword_hits]
    # 数字证据：阿拉伯数字 + 中文数字（「三个月」「两成」「二十亿」在中文回答里非常常见）。
    numbers = re.findall(
        r"\d+(?:\.\d+)?\s*(?:%|％|万|千|百|人|个|次|天|周|月|年|ms|s|倍)?"
        r"|[一二两三四五六七八九十百千万亿几]+(?:小时|分钟|个月|个|人|次|天|周|月|年|倍|成|%|％|万|千|百|亿)",
        text,
    )
    # 去掉「一个 / 一次 / 一些」这类不构成结果证据的泛化数量词。
    weak = {"一个", "一次", "一些", "一下", "一种"}
    numbers = [item.strip() for item in numbers if item.strip() and item.strip() not in weak]
    relevance = _relevance(text, question, language)

    length_pts = _length_points(chinese, words, language)
    relevance_pts = round(min(1.0, relevance * 1.6) * 18, 2)
    structure_pts = min(10, len(structure_hits) * 4)
    evidence_pts = min(8, len(numbers) * 3) if numbers else 0
    depth_pts = 0
    if sentences >= 2:
        depth_pts += 3
    unique_chars = len(set(re.findall(r"[\u4e00-\u9fff]", text)))
    if chinese >= 30 and unique_chars / max(1, chinese) >= 0.45:
        depth_pts += 3
    elif words >= 20:
        depth_pts += 3

    score = 34 + length_pts + relevance_pts + structure_pts + evidence_pts + depth_pts
    # 切题度极低时不再给「堆字数」红利
    if relevance < 0.06 and (chinese >= 50 or words >= 35):
        score -= 8
    # 过短回答上限：避免一句「做过」拿到及格分
    if _is_en(language):
        if words < 6:
            score = min(score, 35)
        elif words < 15:
            score = min(score, 52)
        elif words < 30:
            score = min(score, 68)
    else:
        if chinese < 10:
            score = min(score, 35)
        elif chinese < 25:
            score = min(score, 52)
        elif chinese < 45:
            score = min(score, 68)
    score = round(max(0.0, min(100.0, score)), 1)

    signals.update(
        {
            "chinese_chars": chinese,
            "word_count": words,
            "sentence_count": sentences,
            "structure_hits": structure_hits,
            "keywords_hit": keyword_hits,
            "missing_keywords": missing,
            "numbers": numbers[:8],
            "relevance": round(relevance, 3),
            "relevance_floor": 0.28 if (words >= 40 if _is_en(language) else chinese >= 60) else (
                0.18 if (words >= 20 if _is_en(language) else chinese >= 35) else 0.0
            ),
            "length_points": length_pts,
            "relevance_points": relevance_pts,
            "structure_points": structure_pts,
            "evidence_points": evidence_pts,
            "depth_points": depth_pts,
        }
    )

    if score >= 85:
        feedback = "回答结构清晰，有具体事实与结果支撑，能够说明你的真实贡献。"
    elif score >= 70:
        feedback = (
            "回答结构清晰并给出量化证据，若能进一步说明关键取舍与个人贡献会更完整。"
            if numbers else
            "回答覆盖了主要信息，若能补充量化结果、个人贡献与取舍过程会更完整。"
        )
    elif score >= 55:
        feedback = "回答基本切题，但细节和结果证据偏少，建议按「背景-任务-行动-结果」展开。"
    else:
        feedback = "回答较短或不够聚焦，建议补充具体场景、你的关键动作和最终结果。"
    if missing and score < 85:
        feedback += f" 可进一步说明：{'、'.join(missing[:3])}。"
    if int(duration_seconds or 0) > 0 and int(duration_seconds) < 8 and score < 70:
        feedback += " 本次作答时间偏短，建议展开说明。"
    return AnswerScore(score, signals, feedback)


def should_follow_up(question: dict[str, Any], answer: str, score: float, language: str = "zh-CN") -> bool:
    """是否值得追问一次：回答过短 / 证据不足 / 得分偏低，且题目自带追问。"""
    if not str(question.get("follow_up") or "").strip():
        return False
    text = str(answer or "")
    chinese, words = _text_len(text)
    # 已经讲清楚（分数不低且篇幅足够）就不再追问，避免面试变成连环追问。
    if score >= 78 and (words >= 40 if _is_en(language) else chinese >= 80):
        return False
    short = words < 30 if _is_en(language) else chinese < 50
    keywords = [str(k) for k in question.get("keywords") or [] if str(k).strip()]
    missing = len(keywords) - len(_keyword_hits(text, keywords))
    missing_key_points = missing >= max(2, len(keywords) // 2) and (
        words < 60 if _is_en(language) else chinese < 90
    )
    return bool(short or score < 62 or missing_key_points)


def follow_up_text(question: dict[str, Any], language: str = "zh-CN") -> str:
    """取题目追问；未配置时按维度给一条兜底追问。"""
    configured = str(question.get("follow_up") or "").strip()
    if configured:
        return configured
    dimension = str(question.get("dimension") or "communication")
    if _is_en(language):
        return {
            "professional": "Could you go one level deeper on what you personally did and what the measurable outcome was?",
            "problem_solving": "What would you do differently if you faced the same problem again, and why?",
            "communication": "Could you restructure that using situation, action and result?",
            "culture_fit": "How did the other person react, and what did you learn from it?",
            "potential": "What concrete steps would you take in the next three months to close the gap?",
        }.get(dimension, "Could you give one concrete example with a measurable result?")
    return {
        "professional": "能具体说说你个人承担的动作，以及最终可量化的结果吗？",
        "problem_solving": "如果再来一次，你会改变哪个决策？为什么？",
        "communication": "请用「背景-任务-行动-结果」再补充一下关键细节。",
        "culture_fit": "当时对方的反应是什么？你从这次经历里学到了什么？",
        "potential": "为了补齐这个差距，你接下来三个月会具体怎么做？",
    }.get(dimension, "能举一个更具体、有结果的例子吗？")


def acknowledge(score: float, language: str = "zh-CN") -> str:
    """一句自然的过渡语，让数字人的对话不显得机械。"""
    if _is_en(language):
        if score >= 85:
            return "That's a strong example, thank you."
        if score >= 70:
            return "Got it, thanks for walking me through that."
        return "Thanks, I appreciate the context."
    if score >= 85:
        return "很好，这个例子很有说服力，谢谢。"
    if score >= 70:
        return "好的，我了解了，谢谢你的分享。"
    return "好的，谢谢。我们继续。"


def greeting_text(
    *,
    name: str,
    interviewer: str,
    position_title: str | None,
    question_count: int,
    duration_minutes: int,
    language: str = "zh-CN",
) -> str:
    """数字人开场白（同时作为首屏文案与语音播报内容）。"""
    person = (name or ("候选人" if not _is_en(language) else "there")).strip()
    role = (position_title or "").strip()
    if _is_en(language):
        role_text = f" for the {role} role" if role else ""
        return (
            f"Hi {person}, I'm {interviewer}, your AI interviewer{role_text} today. "
            f"This interview takes about {duration_minutes} minutes and has {question_count} questions. "
            "You can answer by voice or text — just be yourself and use concrete examples. Ready? Let's begin."
        )
    role_text = f"{role}岗位的" if role else "本次"
    return (
        f"你好，{person}，我是{interviewer}，{role_text} AI 面试由我负责。"
        f"整个过程大约 {duration_minutes} 分钟，共 {question_count} 个问题。"
        "你可以用语音或文字回答，像平时聊天一样就好，尽量用具体例子说明。"
        "准备好了吗？我们先从第一个问题开始。"
    )


def closing_text(name: str, language: str = "zh-CN") -> str:
    """数字人结束语。"""
    if _is_en(language):
        return (
            f"Thank you, {name or 'and that wraps up our interview'}. "
            "Your responses have been recorded and the hiring team will review the evaluation report. "
            "Thanks for your time today."
        )
    return (
        f"{name or '候选人'}，今天的面试到这里就结束了，感谢你的时间。"
        "你的回答已经记录，评估报告会同步给招聘团队，请留意后续通知。"
    )


# ================================================================== 综合评估
def level_for(score: float, completion_rate: float = 1.0) -> str:
    """等级：完成率低于 50% 时不硬套能力等级，明确标注样本不足。"""
    if completion_rate < 0.5:
        return "样本不足"
    for threshold, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "待提升"


def recommendation_for(score: float, completion_rate: float = 1.0) -> str:
    """给 HR 的建议（不替代人工决策）。"""
    if completion_rate < 0.5:
        return "insufficient"
    if score >= 85:
        return "strongly_recommend"
    if score >= 75:
        return "recommend"
    if score >= 65:
        return "consider"
    return "not_recommend"


def _field(turn: Any, name: str, default: Any = None) -> Any:
    if isinstance(turn, dict):
        return turn.get(name, default)
    return getattr(turn, name, default)


def _weighted_average(pairs: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in pairs)
    if not pairs or total_weight <= 0:
        return None
    return round(sum(score * weight for score, weight in pairs) / total_weight, 1)


def evaluate_session(
    plan: list[dict[str, Any]],
    turns: list[Any],
    *,
    language: str = "zh-CN",
) -> dict[str, Any]:
    """根据问题计划 + 对话记录生成可解释的综合评估报告。

    关键口径：
    - 每题得分 = 规则引擎对「主回答 + 追问回答」合并文本的评分；
    - 维度得分只对**已作答**题目取加权平均，未作答维度返回 None；
    - 综合分 = 已测量维度的加权平均（不把未作答题目当作 0 分拉低候选人）；
    - 完成率单列，完成率 < 50% 时等级 = 样本不足，建议 = insufficient。
    """
    candidate_turns = [
        turn for turn in (turns or [])
        if _field(turn, "role") == "candidate" and _field(turn, "question_index") is not None
    ]
    grouped: dict[int, list[Any]] = {}
    for turn in candidate_turns:
        try:
            index = int(_field(turn, "question_index"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(index, []).append(turn)

    per_question: list[dict[str, Any]] = []
    dimension_buckets: dict[str, list[tuple[float, float]]] = {}
    voice_count = 0
    text_count = 0
    total_chars = 0
    total_duration = 0
    answered_count = 0
    numeric_answers = 0

    for index, question in enumerate(plan or []):
        answers = grouped.get(index) or []
        answer_text = "\n".join(str(_field(turn, "content") or "").strip() for turn in answers).strip()
        dimension = str(question.get("dimension") or "communication")
        weight = float(question.get("weight") or 1.0)
        item: dict[str, Any] = {
            "index": index,
            "question": question.get("text") or "",
            "dimension": dimension,
            "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
            "intent": question.get("intent") or "",
            "score": None,
            "answer": answer_text,
            "feedback": "",
            "signals": {},
            "follow_up_asked": any(
                _field(turn, "kind") == "follow_up" for turn in (turns or [])
                if _field(turn, "role") == "interviewer" and _field(turn, "question_index") == index
            ),
        }
        if not answer_text:
            item["feedback"] = "未作答。"
            per_question.append(item)
            continue

        answered_count += 1
        duration = sum(max(0, int(_field(turn, "duration_seconds") or 0)) for turn in answers)
        scored = score_answer(question, answer_text, duration_seconds=duration, language=language)
        item["score"] = scored.score
        item["feedback"] = scored.feedback
        item["signals"] = scored.signals
        per_question.append(item)
        dimension_buckets.setdefault(dimension, []).append((scored.score, weight))
        for turn in answers:
            mode = str(_field(turn, "input_mode") or "").strip()
            if mode == "voice":
                voice_count += 1
            else:
                text_count += 1
        total_chars += len(answer_text)
        total_duration += duration
        if scored.signals.get("numbers"):
            numeric_answers += 1

    dimension_scores: dict[str, float | None] = {
        dimension: _weighted_average(dimension_buckets.get(dimension, []))
        for dimension in DIMENSIONS
    }
    measured = [
        (score, DIMENSION_WEIGHTS.get(dimension, 1.0))
        for dimension, score in dimension_scores.items()
        if score is not None
    ]
    total_score = _weighted_average(measured)
    question_count = len(plan or [])
    completion_rate = round(answered_count / question_count, 3) if question_count else 0.0
    final_score = total_score if total_score is not None else 0.0
    level = level_for(final_score, completion_rate) if answered_count else "未评估"
    recommendation = (
        recommendation_for(final_score, completion_rate) if answered_count else "insufficient"
    )

    # -------------------------------------------------------------- 优势 / 短板
    ranked = sorted(
        ((dimension, score) for dimension, score in dimension_scores.items() if score is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    strengths: list[str] = []
    for dimension, score in ranked:
        if score >= 75:
            strengths.append(
                f"{DIMENSION_LABELS[dimension]}：{score} 分，{DIMENSION_STRENGTHS[dimension]}"
            )
    if not strengths and ranked:
        dimension, score = ranked[0]
        strengths.append(f"相对优势：{DIMENSION_LABELS[dimension]}（{score} 分）")
    if numeric_answers >= 2:
        strengths.append("回答中包含具体数据或指标，结果意识较好")
    strengths = strengths[:5]

    weaknesses: list[dict[str, Any]] = [
        {
            "dimension": dimension,
            "label": DIMENSION_LABELS[dimension],
            "score": score,
            "gap": f"低于合格线 70 分 {round(70 - score, 1)} 分",
        }
        for dimension, score in sorted(
            ((dimension, score) for dimension, score in dimension_scores.items() if score is not None),
            key=lambda item: item[1],
        )
        if score < 70
    ][:3]

    suggestions: list[str] = [DIMENSION_TIPS[item["dimension"]] for item in weaknesses]
    if completion_rate < 1:
        suggestions.append("本次面试存在未作答题，建议补充完整面试或安排人工复核")
    if not suggestions:
        suggestions.append("整体表现良好，建议进入下一轮，并在背景调查中重点核验关键经历")
    suggestions = suggestions[:5]

    # -------------------------------------------------------------- 总结
    if not answered_count:
        summary = "候选人未作答，无法形成有效评估。"
    else:
        best = ranked[0] if ranked else None
        worst = ranked[-1] if ranked else None
        if _is_en(language):
            summary = (
                f"The candidate answered {answered_count} of {question_count} questions and scored "
                f"{final_score} ({level})."
            )
            if best and worst and best[0] != worst[0]:
                summary += (
                    f" The strongest dimension was {DIMENSION_LABELS[best[0]].lower()} "
                    f"({best[1]}), while {DIMENSION_LABELS[worst[0]].lower()} was relatively weaker "
                    f"({worst[1]})."
                )
            summary += f" {RECOMMENDATION_LABELS[recommendation]}."
        else:
            summary = (
                f"候选人完成 {answered_count}/{question_count} 道题，综合得分 {final_score} 分（{level}）。"
            )
            if best and worst and best[0] != worst[0]:
                summary += (
                    f"其中「{DIMENSION_LABELS[best[0]]}」表现最好（{best[1]} 分），"
                    f"「{DIMENSION_LABELS[worst[0]]}」相对薄弱（{worst[1]} 分）。"
                )
            if completion_rate < 1:
                summary += f"本次完成率 {round(completion_rate * 100)}%，结论需结合未作答部分谨慎使用。"
            summary += f"{RECOMMENDATION_LABELS[recommendation]}。"

    answer_count_with_duration = max(1, answered_count)
    return {
        "total_score": round(final_score, 1),
        "level": level,
        "recommendation": recommendation,
        "recommendation_label": RECOMMENDATION_LABELS[recommendation],
        "dimension_scores": dimension_scores,
        "dimension_labels": DIMENSION_LABELS,
        "dimension_weights": DIMENSION_WEIGHTS,
        "question_count": question_count,
        "answered_count": answered_count,
        "completion_rate": completion_rate,
        "per_question": per_question,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "suggestions": suggestions,
        "summary": summary,
        "scoring_method": "rule",
        "prompt_version": "ai-interview-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "integrity": {
            "voice_answers": voice_count,
            "text_answers": text_count,
            "avg_answer_chars": round(total_chars / answer_count_with_duration, 1),
            "avg_duration_seconds": round(total_duration / answer_count_with_duration, 1),
            "numeric_answers": numeric_answers,
        },
    }


__all__ = [
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "DIMENSION_WEIGHTS",
    "LEVEL_THRESHOLDS",
    "PERSONAS",
    "RECOMMENDATION_LABELS",
    "AnswerScore",
    "acknowledge",
    "build_interview_plan",
    "closing_text",
    "evaluate_session",
    "follow_up_text",
    "greeting_text",
    "level_for",
    "parse_skills",
    "persona_profile",
    "recommendation_for",
    "resolve_persona",
    "score_answer",
    "should_follow_up",
]


