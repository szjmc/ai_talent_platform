"""
模块：app/utils/resume_data.py
职责：简历解析结果（Resume.parsed_data 这一 JSON 列）的统一读取兼容层。

标签：[架构组] [P0]（打通「简历解析 → 人才档案 → 岗位匹配」数据链）

============================ 背景 ============================
合并后同一个 JSON 列存在两种写入格式：
    1) 扁平结构：resume_service.parse_worker 写 ParsedResume.model_dump()，
       结构即 {basic_info, education, work_experience, projects, skills, warnings}
       （app/models/resume.py 注释声明的契约）；
    2) 嵌套结构：talent_resume_service.save_parsed 写
       {"parsed": {...}, "warnings": [...], "analysis_meta": {...}, "duplicates": [...]}。
读取方若只认其中一种，另一种写入的数据就被读成空 —— 画像取不到经历、检索建不出分块、
匹配召回命中不了解析出的候选人（P0-04 主链断点）。

本模块把这些读取点统一为「先读嵌套 parsed，缺失则把整块当扁平」，
两种写入都可读，同时不改动任何一侧的写入格式与外部契约。
==============================================================
"""

from __future__ import annotations

from typing import Any

# 嵌套格式独有的包裹键：扁平数据里不应作为正文返回
_WRAPPER_KEYS = ("parsed", "analysis_meta", "duplicates", "source_pages")


def extract_parsed(parsed_data: dict[str, Any] | None) -> dict[str, Any]:
    """取出结构化简历正文（basic_info / education / work_experience / projects / skills）。

    :param parsed_data: Resume.parsed_data 原始 JSON
    :return: 正文 dict；无数据返回空 dict（调用方无需再做 None 判断）
    """
    data = parsed_data if isinstance(parsed_data, dict) else {}
    inner = data.get("parsed")
    if isinstance(inner, dict):
        return dict(inner)
    return {key: value for key, value in data.items() if key not in _WRAPPER_KEYS}


def extract_skills(parsed_data: dict[str, Any] | None) -> list[str]:
    """取出技能标签数组（去空、去重、保持原顺序）。"""
    raw = extract_parsed(parsed_data).get("skills") or []
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def extract_warnings(parsed_data: dict[str, Any] | None) -> list[str]:
    """取出解析告警：优先正文内的 warnings，其次嵌套格式外层的 warnings。"""
    data = parsed_data if isinstance(parsed_data, dict) else {}
    for candidate in (extract_parsed(data).get("warnings"), data.get("warnings")):
        if isinstance(candidate, list):
            return [str(item) for item in candidate]
    return []


def merge_skill_tags(existing: list[str] | None, incoming: list[str] | None) -> list[str]:
    """合并技能标签（保留已有顺序，追加新增项，去重）。"""
    merged: list[str] = []
    seen: set[str] = set()
    for source in (existing or [], incoming or []):
        for item in source:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged


__all__ = ["extract_parsed", "extract_skills", "extract_warnings", "merge_skill_tags"]
