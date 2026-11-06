"""
模块：app/services/succession_rules.py
职责：关键岗位继任方案 · 规则引擎（纯函数，可解释、可复现）。

标签：[Agent组] [P1]（继任准备度 / 空缺风险 / 建议动作）

============================ 实现思路与代码要点 ============================
这是「14 条写死风险规则」之外新增的一组「继任规则」，思路完全一致：
不交给 LLM 算数，用写死的公式和阈值对结构化数据做判断，避免数学幻觉。

数据流（从底到顶）：
    calc_readiness   算准备度（核心公式，一个人）
        ↓
    grade_readiness  给准备度分级（≥85 / 70~84 / <70）
        ↓
    assess_risk      判空缺风险（看有几个合格候选）
        ↓
    build_actions    生成建议动作（P0 / P1）
        ↓
    build_succession_report  组装成整份报告 JSON（最顶层）

输入：数据库查出来的「现任 + 候选」数据（每人含匹配分/潜力/年限/测评达标率）。
输出：一份继任报告 dict，前端照着渲染，不经过 LLM。

核心公式（答辩要讲的一句话）：
    继任准备度 = 匹配分×40% + 潜力分×30% + 经验分×20% + 测评达标率×10%

为什么要有「映射表」先转换？
    公式只能做加减乘除，但「潜力=高」是文字、「年限=7年」是数字，
    得先统一成分数才能算。POTENTIAL_SCORE 和 _exp_score 就是干这个的。
=========================================================================
"""
from __future__ import annotations

from datetime import datetime

# 潜力 → 分数（高=100，中=70，低=40；未匹配给默认 50，避免脏数据导致崩溃）
POTENTIAL_SCORE = {
    "高": 100,
    "中": 70,
    "低": 40,
}


def _exp_score(years: float) -> float:
    """根据工作年限折算经验分（0~100）。"""
    if years >= 7:
        return 100.0      # ≥7年：经验最足，满分
    if years >= 5:
        return 80.0       # 5~6年：比较有经验
    if years >= 3:
        return 60.0       # 3~4年：有一定经验
    return 40.0           # <3年：经验还浅


def calc_readiness(candidate: dict) -> float:
    """算一个人的继任准备度（0~100）。

    传入：{"match_score", "potential", "years", "eval_rate"}
    传出：准备度分数（保留 1 位小数）
    """
    match_score = candidate["match_score"]   # 匹配分，如 92
    potential = candidate["potential"]       # "高"/"中"/"低"
    years = candidate["years"]               # 工作年限
    eval_rate = candidate["eval_rate"]       # 测评达标率 0~1

    pot_score = POTENTIAL_SCORE.get(potential, 50.0)   # 潜力分（查映射表）
    exp_score = _exp_score(years)                      # 经验分
    eval_score = eval_rate * 100.0                     # 测评达标率 → 0~100 分

    readiness = (
        match_score * 0.4 +   # 匹配分占 40%
        pot_score * 0.3 +     # 潜力  占 30%
        exp_score * 0.2 +     # 经验  占 20%
        eval_score * 0.1      # 测评  占 10%
    )
    return round(readiness, 1)


def grade_readiness(readiness: float) -> str:
    """根据准备度返回分级标签（给前端上颜色用）。"""
    if readiness >= 85:
        return "可立即接任"
    if readiness >= 70:
        return "培养后可接"
    return "长期储备"


def assess_risk(qualified_count: int) -> dict:
    """根据「可立即接任（准备度≥85）」的人数判断岗位空缺风险。

    0 个 → 高；1 个 → 中；≥2 个 → 低。
    """
    if qualified_count <= 0:
        return {"level": "高", "desc": "无合格候选，一旦空缺将出现真空期"}
    if qualified_count == 1:
        return {"level": "中", "desc": "有1名可立即接任者，但无备选，建议同步培养第二梯队"}
    return {"level": "低", "desc": "有2名以上合格候选，梯队健康"}


def build_actions(incumbent: dict, candidates: list, risk: dict) -> list:
    """根据现任/候选/风险，生成给 leader 的动作清单（每条含 priority/action/who/deadline/reason）。

    全部是写死的 if 规则，每条动作都能说清「凭什么触发」，可解释。
    """
    actions = []

    # —— P0：对现任做保留访谈（年限≥7 且 匹配分≥90 的现任 = 高流失风险）——
    if incumbent["years"] >= 7 and incumbent["match_score"] >= 90:
        actions.append({
            "priority": "P0",
            "action": "完成一对一保留访谈",
            "who": incumbent["name"],
            "deadline": "30天内",
            "reason": "年限长+匹配分高+核心岗位，流失风险高",
        })

    # —— P0：给「可立即接任」的候选人安排真实岗位验证 ——
    for c in candidates:
        if c["readiness"] >= 85:
            actions.append({
                "priority": "P0",
                "action": "主导关键模块，作为继任能力验证",
                "who": c["name"],
                "deadline": "下季度",
                "reason": f"准备度{c['readiness']}，已具备接任能力，需实战验证",
            })

    # —— P1：给「可培养」的候选人配导师补短板 ——
    for c in candidates:
        if 70 <= c["readiness"] < 85:
            actions.append({
                "priority": "P1",
                "action": "配置导师，补齐测评短板",
                "who": c["name"],
                "deadline": "90天内",
                "reason": f"准备度{c['readiness']}，潜力尚有提升空间",
            })

    # —— 兜底：空缺风险高时，额外加一条紧急培养 ——
    if risk["level"] == "高":
        actions.append({
            "priority": "P0",
            "action": "启动紧急梯队培养，锁定2名候选重点培养",
            "who": "人力部",
            "deadline": "立即",
            "reason": "岗位空缺风险高，需立即储备",
        })

    return actions


def build_succession_report(post_name: str, incumbent: dict, candidates: list) -> dict:
    """组装整份继任报告 JSON。

    传入：岗位名 + 现任 + 候选列表
    传出：{"post_name", "incumbent", "risk", "candidates", "qualified_count", "actions"}
    """
    # 1) 给每个候选算准备度 + 分级，塞回它自己身上
    for c in candidates:
        c["readiness"] = calc_readiness(c)
        c["grade"] = grade_readiness(c["readiness"])

    # 2) 按准备度从高到低排序
    candidates.sort(key=lambda x: x["readiness"], reverse=True)

    # 3) 统计「可立即接任」人数，判空缺风险
    qualified = sum(1 for c in candidates if c["readiness"] >= 85)
    risk = assess_risk(qualified)

    # 4) 生成建议动作
    actions = build_actions(incumbent, candidates, risk)

    # 5) 记录生成时间，让报告有明确时间锚点（数据为实时查询，标注快照时刻）
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 6) 打包
    return {
        "post_name": post_name,
        "generated_at": generated_at,
        "incumbent": incumbent,
        "risk": risk,
        "candidates": candidates,
        "qualified_count": qualified,
        "actions": actions,
    }
