"""
功能：把岗位说明书转换为结构化画像草稿。
输入：JD原文及可选岗位名称。
输出：JobProfileParsed草稿、实际解析通道。
流程：Dify提取 → Python标准化；不可用时规则提取。
边界：不确认、不落库、不替HR补造要求；草稿需人工确认。

标签：[Agent组] [P1]（JD 解析：Dify 优先，规则兜底）
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from app.adapters.dify.workflow_adapter import DifyError, DifyWorkflowAdapter
from app.core.exceptions import ParamError
from app.schemas.position import ConditionItem, JobProfileParsed

# ------------------------------------------------------------------ 词表
# *kwq* 新增：学历关键词 -> 标准值。
# 说明：只做「同义收敛」，不做等级推断（等级比较在 matching_rules 内完成）。
_EDUCATION_MAP: dict[str, str] = {
    "博士": "博士",
    "硕士": "硕士",
    "研究生": "硕士",
    "本科": "本科",
    "学士": "本科",
    "大专": "大专",
    "专科": "大专",
    "中专": "中专",
    "高中": "高中及以下",
}

_VALID_EDUCATION = {"博士", "硕士", "本科", "大专", "中专", "高中及以下"}

# *kwq* 新增：中文技能词表（仅用于识别原文中明确出现的技能名词）。
# 说明：英文技术词走通用 ASCII 提取，不需要词表；中文无法可靠分词，
#      因此只认这份有限词表 —— 宁可漏识别（进入 missing/review），也不猜。
_CN_SKILL_TERMS: tuple[str, ...] = (
    "数据分析",
    "数据挖掘",
    "机器学习",
    "深度学习",
    "自然语言处理",
    "推荐系统",
    "项目管理",
    "团队管理",
    "产品设计",
    "需求分析",
    "测试自动化",
    "性能优化",
    "微服务",
    "分布式系统",
    "高并发",
    "数据库设计",
    "运维监控",
    "安全审计",
    "招聘管理",
    "薪酬绩效",
    "组织发展",
)

# *kwq* 新增：英文停用词（避免把 the/and/with 之类当成技能）。
_ASCII_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "you", "your", "our", "are", "will",
        "have", "has", "not", "but", "can", "all", "any", "job", "work", "team",
        "year", "years", "plus", "etc", "api", "app", "www", "http", "https",
    }
)

_ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,30}")

# *kwq* 新增：抽象能力短语（无法用 skill_tags 直接证伪的能力描述）。
# 原因（MATCH-015 实测）：JD 写「熟悉 MySQL 数据库设计与优化」，规则解析会把
#      「MySQL」与「数据库设计」各算一个技能；后者作为**硬条件**参与判定时，
#      人才标签里几乎不会出现字面「数据库设计」，导致该岗位 5 名候选人里 4 名被判
#      required_skills=FAIL 而全部淘汰（张伟 Python/FastAPI/MySQL 全中仍被淘汰）。
#      这属于过度淘汰（误杀），与「只有明确不满足才 FAIL」相冲突。
# 处理策略：只有当整个分句**没有**任何具体技术词时，才把能力短语当成技能；
#      与具体技术词同句时视为对该技术的描述，不单独升级为硬门槛。
#      原文不会丢——该分句仍保留在 required_conditions 中供 HR 复核。
_CAPABILITY_PHRASES: frozenset[str] = frozenset(
    {
        "数据库设计",
        "性能优化",
        "运维监控",
        "安全审计",
        "项目管理",
        "团队管理",
        "需求分析",
        "产品设计",
        "组织发展",
        "招聘管理",
        "薪酬绩效",
    }
)

# *kwq* 新增：年限表达（3年 / 3 年以上 / 3-5年）。
_YEARS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\d{1,2})\s*年(?:以上)?[^\n。；;]{0,8}?经验"),
    re.compile(r"(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*年"),
    re.compile(r"工作年限[:：]?\s*(\d{1,2})\s*年"),
)

# *kwq* 新增：薪资表达（15k-25k / 15-25K / 15000-25000元）。
_SALARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(\d{1,3})\s*[kK]\s*[-~至]\s*(\d{1,3})\s*[kK]"),
    re.compile(r"(\d{4,6})\s*[-~至]\s*(\d{4,6})\s*元"),
)

_LOCATION_PATTERN = re.compile(r"(?:工作地点|上班地址|办公地点)[:：]\s*([^\n，,；;]{2,40})")
_TITLE_PATTERN = re.compile(r"(?:岗位名称|职位名称|招聘岗位)[:：]\s*([^\n，,；;]{2,40})")

# *kwq* 新增：段落标题识别（职责段 / 要求段）。
_RESPONSIBILITY_HEADINGS = ("岗位职责", "工作职责", "职责描述", "工作内容", "你将负责")
_REQUIREMENT_HEADINGS = ("任职要求", "任职资格", "岗位要求", "任职条件", "我们希望你")

# *kwq* 新增：优先条件关键词（命中即归入 preferred，永不参与硬性淘汰）。
_PREFERRED_KEYWORDS = ("优先", "加分", "更佳", "尤佳")

# *kwq* 新增：技能识别行关键词。
# 原因：JD 的「职责」段里常出现「参与接口设计与性能优化」这类能力描述，
#      若全篇扫技能词，会把「性能优化」当成技能标签，污染硬条件比对。
#      只在「要求类」语句里取技能，宁可漏识别（进入 missing/review），也不制造假技能。
_SKILL_LINE_KEYWORDS = ("熟悉", "掌握", "精通", "了解", "使用", "技能", "技术栈", "开发经验", "优先")


# ------------------------------------------------------------------ 工具


def _as_text_list(value: Any) -> list[str]:
    """把任意形态的列表压平成去重文本列表（保序，忽略空项）。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _coerce_conditions(value: Any) -> list[ConditionItem]:
    """把条件集合统一为 ConditionItem（字符串 / dict / 模型均可）。"""
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    items: list[ConditionItem] = []
    for item in value:
        if isinstance(item, ConditionItem):
            items.append(item)
            continue
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("condition") or "").strip()
            if not text:
                continue
            normalized = item.get("normalized")
            items.append(
                ConditionItem(
                    text=text,
                    normalized=str(normalized).strip() if normalized else None,
                    source_span=item.get("source_span"),
                    confidence=float(item.get("confidence") or 0.0),
                )
            )
            continue
        text = str(item or "").strip()
        if text:
            items.append(ConditionItem(text=text))
    return items


def _normalize_education(value: Any) -> str | None:
    """学历标准化：命中词表返回值，未命中保留原文（同时记冲突）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for keyword, normalized in _EDUCATION_MAP.items():
        if keyword in text:
            return normalized
    return text


def _extract_years(text: str) -> int | None:
    """从文本中提取最低工作年限（取区间下限；无匹配返回 None）。"""
    for pattern in _YEARS_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return max(0, min(50, int(match.group(1))))
            except (TypeError, ValueError):
                continue
    return None


def _extract_salary(text: str) -> str | None:
    for pattern in _SALARY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def _split_sections(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """按标题切分「职责段」与「要求段」。

    返回：
        (responsibilities, requirement_lines)
    说明：
        没有标题时，把含「负责/参与/推动」的行当作职责，含「要求/熟悉/掌握/学历」
        的行当作要求；两段都为空时不做任何猜测。
    """
    responsibilities: list[str] = []
    requirements: list[str] = []
    section: str | None = None

    for raw in lines:
        line = raw.strip(" \t-•·*　")
        if not line:
            continue
        if any(heading in line for heading in _RESPONSIBILITY_HEADINGS) and len(line) <= 20:
            section = "resp"
            continue
        if any(heading in line for heading in _REQUIREMENT_HEADINGS) and len(line) <= 20:
            section = "req"
            continue

        if section == "resp":
            responsibilities.append(line)
        elif section == "req":
            requirements.append(line)
        else:
            if any(word in line for word in ("负责", "参与", "推动", "主导", "搭建")):
                responsibilities.append(line)
            elif any(word in line for word in ("要求", "熟悉", "掌握", "学历", "经验", "优先")):
                requirements.append(line)

    return responsibilities, requirements


def _extract_skills(text: str) -> tuple[list[str], list[str]]:
    """从 JD 原文提取英文技术词与中文技能词（区分必备 / 优先）。

    规则：
        - 只识别「原文里写出来的」技能，不做同义扩展；
        - 先剔除薪资串，避免 20k-35k 被切成 "k-35k" 这种假技能；
        - 只在要求类语句里取技能（见 _SKILL_LINE_KEYWORDS）；
        - 所在分句含「优先/加分」的归入 preferred_skills；
        - 英文保留原始大小写（与人才档案 skill_tags 的比对在匹配器里做归一化）。
    """
    required: list[str] = []
    preferred: list[str] = []
    seen: set[str] = set()

    # *kwq* 新增：剔除薪资片段（实测 "20k-35k" 会被 ASCII 提取器切成 "k-35k"）。
    cleaned = text
    salary = _extract_salary(text)
    if salary:
        cleaned = cleaned.replace(salary, " ")

    def _collect(text_part: str, bucket: list[str]) -> None:
        # *kwq* 新增（MATCH-015）：判断本分句是否已有「具体技术词」。
        # 用途：决定抽象能力短语（如 数据库设计）是否值得单独成为硬性技能门槛。
        concrete_tokens = [
            m.group(0).strip(".-")
            for m in _ASCII_TOKEN.finditer(text_part)
            if len(m.group(0).strip(".-")) >= 2
            and m.group(0).strip(".-").lower() not in _ASCII_STOPWORDS
            and not re.fullmatch(r"[a-zA-Z]?-?\d+[a-zA-Z]?", m.group(0).strip(".-"))
        ]
        concrete_cn = [
            term for term in _CN_SKILL_TERMS if term not in _CAPABILITY_PHRASES and term in text_part
        ]
        has_concrete = bool(concrete_tokens or concrete_cn)

        for match in _ASCII_TOKEN.finditer(text_part):
            token = match.group(0).strip(".-")
            if len(token) < 2 or token.lower() in _ASCII_STOPWORDS:
                continue
            # 纯数字 / 「字母-数字」形态是金额、版本号残留，不是技能
            if re.fullmatch(r"[a-zA-Z]?-?\d+[a-zA-Z]?", token):
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            bucket.append(token)
        for term in _CN_SKILL_TERMS:
            if term not in text_part or term.lower() in seen:
                continue
            # *kwq* 新增（MATCH-015）：能力短语与具体技术词同句时不再单独作为技能，
            # 避免「MySQL 数据库设计」被拆成两个硬条件造成过度淘汰（原文仍在 required_conditions）。
            if term in _CAPABILITY_PHRASES and has_concrete:
                continue
            seen.add(term.lower())
            bucket.append(term)

    skill_lines = [
        line for line in cleaned.splitlines() if any(key in line for key in _SKILL_LINE_KEYWORDS)
    ]
    if not skill_lines:
        skill_lines = cleaned.splitlines()

    for line in skill_lines:
        # *kwq* 修改：按分句（、，,；;）切分，再判断「优先」落在哪个分句。
        # 修改原因：
        #   1) 原实现只要行内出现「优先」就把整行技能都当优先条件，
        #      会把「熟悉 Python/FastAPI/MySQL，了解 Redis 优先」里的必备技能误降级；
        #   2) 按第一个「优先」字符切两段也不对 —— Redis 写在「优先」之前，
        #      会被切进必备段。只有按分句归属才符合中文语义。
        chunks = re.split(r"[、，,；;]", line)
        for chunk in chunks:
            if any(keyword in chunk for keyword in _PREFERRED_KEYWORDS):
                _collect(chunk, preferred)
            else:
                _collect(chunk, required)

    # 同一技能同时出现在两类条件里时，以「优先」为准（宁可少淘汰，不可误淘汰）
    preferred_keys = {item.lower() for item in preferred}
    required = [item for item in required if item.lower() not in preferred_keys]
    return required, preferred


# ------------------------------------------------------------------ 规则提取


# *kwq* 新增：纯规则提取（无网络、无模型）。
# 用途：Dify 不可用时的最低可用通道；只抓原文显式信息，产出必须人工确认。
def extract_by_rules(jd_text: str, job_title: str | None = None) -> dict[str, Any]:
    """从 JD 原文按规则提取岗位画像字段（返回原始 dict，交给 normalize 统一校验）。"""
    text = str(jd_text or "").strip()
    if not text:
        raise ParamError("JD 文本为空，无法解析")

    lines = text.splitlines()
    responsibilities, requirement_lines = _split_sections(lines)
    requirement_text = "\n".join(requirement_lines) or text

    title = (job_title or "").strip()
    if not title:
        match = _TITLE_PATTERN.search(text)
        if match:
            title = match.group(1).strip()
    if not title:
        # 取第一个非空短行作为岗位名（仍是原文内容，不是编造）
        for line in lines:
            candidate = line.strip(" \t-•·*　")
            if 2 <= len(candidate) <= 30 and not candidate.endswith(("：", ":")):
                title = candidate
                break

    education: str | None = None
    for keyword, normalized in _EDUCATION_MAP.items():
        if keyword in requirement_text:
            education = normalized
            break

    years = _extract_years(requirement_text)
    if years is None:
        years = _extract_years(text)

    required_skills, preferred_skills = _extract_skills(text)

    # 条件行：要求段中无法结构化的描述（保留原文，供人工复核）
    structured_keywords = set(_EDUCATION_MAP) | {"年", "经验"}
    # *kwq* 新增（MATCH-015）：已被技能/薪资覆盖的条件行不再重复登记为「待复核条件」。
    # 原因（实测）：原实现把「3. 熟悉 MySQL 数据库设计与优化；」同时登记为技能与模糊条件，
    #      匹配时该条件必然产出 NEEDS_REVIEW（「非结构化描述，需人工复核」），
    #      使每名候选人都被打上待复核标记，复核清单失去信噪比；
    #      「5. 薪资 25k-35k」同理——薪资已结构化到 salary_range，不是能力条件。
    skill_tokens = {item.lower() for item in (*required_skills, *preferred_skills)}
    conditions: list[str] = []
    for line in requirement_lines:
        if any(keyword in line for keyword in structured_keywords):
            continue
        if any(keyword in line for keyword in _PREFERRED_KEYWORDS):
            continue
        # 薪资/福利类描述：已由 salary_range 承载
        if _extract_salary(line):
            continue
        # 该行已抽出具体技能：属于可结构化条件，不需要人工复核
        lowered = line.lower()
        if any(token and token in lowered for token in skill_tokens):
            continue
        clean = line.strip()
        if 2 <= len(clean) <= 200:
            conditions.append(clean)

    location_match = _LOCATION_PATTERN.search(text)

    return {
        "job_title": title,
        "responsibilities": responsibilities[:20],
        "required_conditions": [{"text": item} for item in conditions[:10]],
        "preferred_conditions": [],
        "required_skills": required_skills[:30],
        "preferred_skills": preferred_skills[:20],
        "min_education": education,
        "min_work_years": years,
        "location": location_match.group(1).strip() if location_match else None,
        "salary_range": _extract_salary(text),
        "source": "manual",
        "prompt_version": "rule-v1",
    }


# ------------------------------------------------------------------ 标准化


# *kwq* 新增：统一标准化入口。
# 关键约束：
#   1) 只接受白名单字段，多余字段丢弃（不把 Dify 的调试字段带进业务对象）；
#   2) 缺什么就记 missing_fields，绝不补值；
#   3) 冲突要显式记录（同一技能既必备又优先、学历无法识别、年限异常）。
def normalize_job_profile(
    raw: dict[str, Any],
    *,
    job_title_hint: str | None = None,
    source: str = "dify",
    prompt_version: str | None = None,
) -> JobProfileParsed:
    """把 Dify / 规则提取的原始结果标准化成 JobProfileParsed。"""
    if not isinstance(raw, dict):
        raise ParamError("JD 解析结果必须是对象（dict）")

    # 兼容 Dify 常见命名差异（不做语义猜测，只做键名映射）
    title = raw.get("job_title") or raw.get("title") or job_title_hint
    responsibilities = _as_text_list(raw.get("responsibilities") or raw.get("duties"))
    required_conditions = _coerce_conditions(
        raw.get("required_conditions") or raw.get("must_have") or raw.get("conditions")
    )
    preferred_conditions = _coerce_conditions(
        raw.get("preferred_conditions") or raw.get("nice_to_have")
    )

    required_skills = _as_text_list(
        raw.get("required_skills") or raw.get("skills") or raw.get("must_skills")
    )
    preferred_skills = _as_text_list(
        raw.get("preferred_skills") or raw.get("plus_skills")
    )

    min_education = _normalize_education(
        raw.get("min_education") or raw.get("education")
    )
    raw_years = raw.get("min_work_years")
    if raw_years is None:
        raw_years = raw.get("experience_years")
    min_work_years: int | None
    try:
        min_work_years = None if raw_years in (None, "") else max(0, min(50, int(raw_years)))
    except (TypeError, ValueError):
        min_work_years = None

    missing_fields: list[str] = []
    if not title or not str(title).strip():
        missing_fields.append("job_title")
        # 没有岗位名称无法构建画像（禁止用占位名），直接报错让调用方补录
        raise ParamError("JD 解析结果缺少岗位名称，请人工补充 job_title 后重试")
    if not required_skills:
        missing_fields.append("required_skills")
    if min_education is None:
        missing_fields.append("min_education")
    if min_work_years is None:
        missing_fields.append("min_work_years")
    if not responsibilities:
        missing_fields.append("responsibilities")
    # *kwq* 修改（MATCH-015）：必要条件为空但已抽出必备技能时，不再报「缺 required_conditions」。
    # 原因：技能就是结构化后的条件；JD 通篇都是可结构化条件时，硬报缺失会让 HR 以为解析失败。
    if not required_conditions and not required_skills:
        missing_fields.append("required_conditions")

    conflicts: list[str] = []
    overlap = {item.lower() for item in required_skills} & {item.lower() for item in preferred_skills}
    if overlap:
        conflicts.append(f"技能同时出现在必备与优先条件：{'、'.join(sorted(overlap))}")
    if min_education is not None and min_education not in _VALID_EDUCATION:
        conflicts.append(f"学历取值无法识别：{min_education}")
    if min_work_years is not None and min_work_years > 45:
        conflicts.append(f"工作年限要求异常：{min_work_years} 年")
    if job_title_hint and str(title).strip() != job_title_hint.strip():
        conflicts.append(
            f"解析岗位名称「{str(title).strip()}」与岗位档案「{job_title_hint.strip()}」不一致"
        )

    return JobProfileParsed(
        job_title=str(title).strip(),
        responsibilities=responsibilities,
        required_conditions=required_conditions,
        preferred_conditions=preferred_conditions,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        min_education=min_education,
        min_work_years=min_work_years,
        location=raw.get("location"),
        salary_range=raw.get("salary_range"),
        missing_fields=missing_fields,
        conflicts=conflicts,
        # 解析产物一律需要 HR 确认：即使字段齐全，也不能自动生效
        review_required=True,
        source=source if source in {"dify", "llm_fallback", "manual"} else "manual",
        prompt_version=(prompt_version or raw.get("prompt_version") or "v1")[:20],
    )


# ------------------------------------------------------------------ 对外入口


# *kwq* 新增：JD 解析主入口（Dify 优先，失败降级规则提取）。
# 返回值里的 source 直接反映真实通道，不允许把规则结果伪装成 dify。
async def parse_jd(
    jd_text: str,
    *,
    job_title: str | None = None,
    adapter: DifyWorkflowAdapter | None = None,
) -> tuple[JobProfileParsed, str]:
    """解析 JD，返回 (画像, 实际通道说明)。

    通道说明取值：
        "dify"           Dify 工作流成功
        "rule_fallback"  Dify 未配置 / 调用失败，使用规则提取
    """
    text = str(jd_text or "").strip()
    if len(text) < 20:
        raise ParamError("JD 文本过短（至少 20 字），无法可靠解析")

    # 步骤1：正式入口创建 JD 适配器；请求结束释放连接，注入对象归调用方管理。
    if adapter is None:
        async with DifyWorkflowAdapter(workflow_key="job_profile_extraction") as client:
            return await parse_jd(text, job_title=job_title, adapter=client)

    client = adapter
    if client is not None and client.configured:
        try:
            result = await client.run_job_profile_extraction(text)
            raw = result.get("parsed") or result.get("outputs") or {}
            if not isinstance(raw, dict):
                raise ParamError("Dify 返回结构不是对象，无法标准化")
            profile = normalize_job_profile(
                raw,
                job_title_hint=job_title,
                source="dify",
                prompt_version=str(result.get("prompt_version") or "dify-v1"),
            )
            return profile, "dify"
        except (DifyError, ParamError):
            # 外部通道失败 -> 规则兜底；异常不吞掉细节，交由日志与返回值体现
            profile = normalize_job_profile(
                extract_by_rules(text, job_title),
                job_title_hint=job_title,
                source="manual",
                prompt_version="rule-v1",
            )
            return profile, "rule_fallback"

    profile = normalize_job_profile(
        extract_by_rules(text, job_title),
        job_title_hint=job_title,
        source="manual",
        prompt_version="rule-v1",
    )
    return profile, "rule_fallback"


def summarize_missing(profile: JobProfileParsed) -> str:
    """生成缺失项提示语（供接口 message / 日志使用）。"""
    if not profile.missing_fields:
        return "解析完成"
    return "解析完成，缺失字段需人工补充：" + "、".join(profile.missing_fields)


__all__ = [
    "extract_by_rules",
    "normalize_job_profile",
    "parse_jd",
    "summarize_missing",
]
