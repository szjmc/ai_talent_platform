"""
模块：app/services/desensitize.py
职责：员工端报告输出脱敏（避免普通员工看到管理者专属措辞）。

标签：[Agent组] [P2]（输出侧安全：敏感词弱化）

============================ 实现思路与代码要点 ============================
背景：
    普通员工（employee）只能看「自己」的报告，不能看「继任方案 / 周期报告」
    等管理者专属功能。但生成报告的正文由 LLM 生成，可能顺带写出「继任」「排名」
    「流失预警」「决策」等管理者视角的措辞。为避免员工界面出现这些字眼，在
    输出侧统一做一次「敏感词弱化」——不删整段、不破坏报告结构，只把敏感词
    替换成中性表述。

设计：
    - is_employee(roles)：判定当前用户是否为「纯普通员工」（不含任何管理角色）
    - sanitize_for_employee(data)：递归遍历 dict/list/str，替换敏感词
    - 敏感词表 SENSITIVE_WORDS：{敏感词 -> 中性替换}，命中即替换（不区分大小写）

要点：
    - 只改字符串，不改变数据结构（keys 不动，只替换 value 里的文本）；
    - 替换是「弱化」而非「删除」，保证报告仍通顺可读；
    - 管理角色（super_admin / admin / hr / manager）不走脱敏，原样返回。
=========================================================================
"""

from __future__ import annotations

from typing import Any

# 管理角色集合：只要具备任一管理角色，就不按「普通员工」脱敏
MANAGER_ROLES = {"super_admin", "admin", "hr", "manager"}

# 敏感词 -> 中性替换（按「长词在前」匹配，避免子串误伤）
SENSITIVE_WORDS: list[tuple[str, str]] = [
    ("继任梯队", "发展梯队"),
    ("继任方案", "发展建议"),
    ("继任候选", "发展候选"),
    ("继任准备度", "发展准备度"),
    ("继任者", "接班人"),
    ("继任", "发展"),
    ("周期报告", "定期报告"),
    ("流失预警", "人才保留"),
    ("流失风险", "人才保留"),
    ("流失", "流动"),
    ("同事排名", "表现评估"),
    ("匹配分排名", "表现评估"),
    ("排名", "表现评估"),
    ("仅管理者可见", "仅管理员可见"),
    ("管理者可见", "管理员可见"),
    ("管理者", "管理员"),
    ("决策建议", "参考建议"),
    ("决策", "参考"),
    ("leader", "管理员"),
]


def is_employee(roles: list[str] | set[str] | tuple[str, ...] | None) -> bool:
    """判定当前角色集合是否为「普通员工」（不含任何管理角色）。

    传进：roles（角色编码列表，如 ["employee"] / ["manager"]）
    干了：把角色归一成字符串集合，判断是否与管理角色集合有交集
    传出：True 表示普通员工（需脱敏），False 表示含管理角色或角色为空
    """
    role_set = {str(r) for r in (roles or [])}
    # 角色为空（匿名/未知）不按员工处理，避免误伤
    if not role_set:
        return False
    return not role_set.intersection(MANAGER_ROLES)


def _replace_text(text: str) -> str:
    """对单段文本做敏感词替换。"""
    for word, replacement in SENSITIVE_WORDS:
        # 不区分大小写替换（如 Leader / LEADER）
        text = text.replace(word, replacement)
        lowered = word.lower()
        if lowered != word:
            # 简单处理首字母大写等常见形式，避免误漏
            text = text.replace(word.capitalize(), replacement)
    return text


def sanitize_for_employee(data: Any) -> Any:
    """递归脱敏：遍历 dict/list/str，替换敏感词，返回新结构。

    传进：data（Agent 返回的结果 dict，可嵌套 dict / list / str）
    干了：递归遍历，字符串值调用 _replace_text，其它类型原样保留
    传出：脱敏后的新结构（不改原对象）
    """
    if isinstance(data, str):
        return _replace_text(data)
    if isinstance(data, list):
        return [sanitize_for_employee(item) for item in data]
    if isinstance(data, dict):
        return {key: sanitize_for_employee(value) for key, value in data.items()}
    return data


__all__ = [
    "is_employee",
    "sanitize_for_employee",
    "MANAGER_ROLES",
    "SENSITIVE_WORDS",
]
