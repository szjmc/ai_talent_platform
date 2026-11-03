"""
模块：app/ai/utils/output_parser.py
职责：AI 输出解析（JSON 容错 + Schema 校验）。

标签：[架构组] [P0]（被全部 Agent 依赖）

============================ 实现思路与代码要点 ============================
现实问题：模型经常返回 ```json ... ``` 、前后夹杂解释文字、字段缺失。
处理策略（按序尝试，全部失败抛 AIError 5002）：
1. 直接 json.loads；
2. 剥离 Markdown 代码块围栏后再 loads；
3. 定位第一个 { / [ 与最后一个 } / ] 截取后 loads；
4. 修复常见问题：尾逗号、单引号、中文引号、JSON5 注释；
5. 用 Pydantic 模型校验（parse_model），字段缺失由 Schema 默认值兜底。
对外 API：
    parse_json(text: str) -> Any（dict / list）
    parse_model(text: str, schema: type[BaseModel]) -> BaseModel
    extract_json(text: str) -> str（只做「截取」，不做解析，便于自定义处理）
=========================================================================
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.exceptions import AIError

logger = logging.getLogger(__name__)

# Markdown 代码块围栏：```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*(.*?)```", re.DOTALL)
# 中文引号 -> 英文引号
_QUOTE_MAP = {
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "「": '"',
    "」": '"',
}
# 尾逗号：, 后面紧跟 } 或 ]
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码围栏，返回内部内容（无围栏时原样返回）。"""
    if not text:
        return ""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.replace("```", "").strip()


def extract_json(text: str) -> str:
    """从混杂文本中截取最外层 {...} 或 [...] 片段。"""
    if not text:
        return ""
    cleaned = strip_code_fence(text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return cleaned[start : end + 1].strip()
    return cleaned.strip()


def _normalize(text: str) -> str:
    """修复常见 JSON 瑕疵：中文引号、尾逗号、行注释。"""
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    # 去掉 // 与 /* */ 注释（仅在引号外的简单场景，够用即可）
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text.strip()


def _loads_with_repair(candidate: str) -> Any:
    """按「原样 -> 归一化 -> 单引号 literal_eval」的顺序尝试解析。"""
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    repaired = _normalize(candidate)
    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, TypeError):
        pass

    # 单引号字典：用 ast.literal_eval 兜底（模型中途返回 Python 字面量时常见）
    try:
        import ast

        value = ast.literal_eval(repaired)
    except (ValueError, SyntaxError) as exc:
        raise AIError(f"模型输出不是合法 JSON：{exc}", data={"snippet": candidate[:500]}) from exc
    if isinstance(value, (dict, list)):
        return value
    raise AIError("模型输出不是 JSON 对象或数组", data={"snippet": candidate[:500]})


def parse_json(text: str) -> Any:
    """容错解析模型输出为 dict / list；失败抛 AIError(5002)。"""
    if text is None:
        raise AIError("模型输出为空，无法解析")
    if isinstance(text, (dict, list)):
        return text

    candidate = extract_json(str(text))
    if not candidate:
        raise AIError("模型输出为空，无法解析", data={"snippet": str(text)[:500]})
    result = _loads_with_repair(candidate)
    logger.debug("parse_json 成功：%s", str(result)[:200])
    return result


def parse_model(text: str, schema: Any) -> Any:
    """解析为 Pydantic 模型：parse_json -> schema.model_validate。"""
    data = parse_json(text)
    try:
        if isinstance(data, list):
            return schema.model_validate({"items": data}) if _accepts_items(schema) else schema.model_validate(data)
        return schema.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - 校验失败统一转 AIError
        raise AIError(
            f"模型输出不符合结构要求：{exc}",
            data={"snippet": str(text)[:500]},
        ) from exc


def _accepts_items(schema: Any) -> bool:
    """判断 Schema 是否接受 {"items": [...]} 形式（列表包装）。"""
    fields = getattr(schema, "model_fields", {}) or {}
    return "items" in fields


__all__ = ["extract_json", "parse_json", "parse_model", "strip_code_fence"]
