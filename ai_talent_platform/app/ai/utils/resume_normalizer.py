"""
gxd-人才资料规范化与证据检查，规则处理不依赖大模型。

标签：[RAG组] [P1]（简历结构化归一化，供解析链路复用）
"""
import re
from datetime import datetime
from app.schemas.talent_resume_schema import ResumeParsedData

def clean_text(text):
    """gxd-规范换行与空白，不删除可能有业务意义的重复经历。"""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())

def normalize(parsed: ResumeParsedData, source: str):
    """gxd-检查模型字段证据、联系方式及日期冲突；不猜测缺失字段。"""
    data = parsed.model_dump()
    warnings = list(data["warnings"])
    compact = re.sub(r"\s+", "", source).casefold()
    basic = data["basic_info"]
    for field in ("name", "mobile", "email", "school", "major", "current_company", "current_position"):
        value = basic.get(field)
        if value and re.sub(r"\s+", "", value).casefold() not in compact:
            warnings.append(f"{field}无法在原文核对，已置空")
            basic[field] = None
    if basic.get("mobile") and not re.fullmatch(r"[+\d() -]{7,20}", basic["mobile"]):
        warnings.append("手机号格式异常，保留原文供核对")
        basic["mobile"] = None
    if basic.get("email") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", basic["email"]):
        warnings.append("邮箱格式异常，已置空")
        basic["email"] = None
    aliases = {"大学本科": "本科", "本科毕业": "本科", "研究生": None, "硕士研究生": "硕士", "博士研究生": "博士", "专科": "大专"}
    degree = basic.get("education")
    basic["education"] = aliases.get(degree, degree)
    if basic["education"] and basic["education"] not in source and degree not in source:
        basic["education"] = None
        warnings.append("学历缺少原文依据，已置空")
    years = basic.get("work_years")
    if years is not None and not re.search(rf"(?:工作|从业|经验)[^\n]{{0,10}}{years}\s*年|{years}\s*年[^\n]{{0,10}}(?:工作|经验)", source):
        basic["work_years"] = None
        warnings.append("总工作年限未找到明确原文依据，已置空")
    data["skills"] = [s for s in data["skills"] if re.sub(r"\s+", "", s).casefold() in compact]
    for section in ("education", "work_experience", "projects"):
        accepted = []
        for item in data[section]:
            if not item["evidence"] or re.sub(r"\s+", "", item["evidence"]).casefold() not in compact:
                warnings.append(f"{section}存在无原文证据条目，已排除")
                continue
            dates = []
            for key in ("start_date", "end_date"):
                value = item[key]
                if value in (None, "", "至今", "现在"):
                    dates.append(None)
                    continue
                normalized = value.replace("/", "-").replace(".", "-")
                try:
                    fmt = "%Y-%m-%d" if len(normalized.split("-")) == 3 else "%Y-%m"
                    date = datetime.strptime(normalized, fmt)
                    item[key] = date.strftime(fmt)
                    dates.append(date)
                except ValueError:
                    warnings.append(f"{section}.{key}日期格式异常")
                    dates.append(None)
            if all(dates) and dates[1] < dates[0]:
                warnings.append(f"{section}结束日期早于开始日期，请核对")
            accepted.append(item)
        data[section] = accepted
    for key in ("name", "mobile", "education"):
        if not basic.get(key):
            warnings.append(f"缺少{key}")
    data["warnings"] = list(dict.fromkeys(warnings))
    return ResumeParsedData.model_validate(data)