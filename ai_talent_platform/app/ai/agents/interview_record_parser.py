"""
标签：[Agent组] [P1]
模块：app/ai/agents/interview_record_parser.py
职责：面试记录表「拍照上传 -> 结构化解析」。

两条解析路径（自动降级）：
1. 视觉大模型：照片 base64 -> OpenAI 兼容多模态 chat（settings.LLM_VISION_MODEL）
   输出 JSON：{position, interview_score, comprehensive_evaluation, highlights[], weaknesses[]}
2. 兜底路径：视觉模型不可用时，用上传时随照片附带的手工文本（fallback_text）做正则解析；
   两者都缺时返回 failed，由调用方提示面试官补录。

解析结果统一通过 parse 返回：
    {position, interview_score, comprehensive_evaluation, highlights, weaknesses,
     raw_text, parse_status(parsed/fallback/failed)}
"""

from __future__ import annotations

import base64
import re
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings

logger = get_logger(__name__)

INTERVIEW_RECORD_COLLECTION = "interview_record_vectors"

_SYSTEM_PROMPT = """你是面试记录表识别专家。从面试记录表照片中识别以下字段，只输出 JSON，不要多余解释：
{
  "position": "面试岗位",
  "interview_score": 数字(面试得分0-100),
  "comprehensive_evaluation": "综合评价（1-3句话）",
  "highlights": ["亮点1", "亮点2"],
  "weaknesses": ["不足1", "不足2"]
}
无法识别的字符串字段填空字符串，数字填 null，列表填 []；得分必须在0到100之间。"""


async def parse_record_image(image_bytes: bytes, mime: str = "image/jpeg",
                             fallback_text: str = "") -> dict[str, Any]:
    """解析面试记录表照片；视觉模型失败时降级用 fallback_text 正则解析。"""
    raw_text = ""
    try:
        raw_text = await _vision_parse(image_bytes, mime)
        data = _normalize(_load_json(raw_text))
        data["parse_status"] = "parsed"
        data["raw_text"] = raw_text
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("视觉模型解析面试记录表失败，尝试兜底文本解析：%s", exc)

    if fallback_text:
        data = _parse_fallback_text(fallback_text)
        data["parse_status"] = "fallback"
        data["raw_text"] = fallback_text
        return data

    return {
        "position": "", "interview_score": None, "comprehensive_evaluation": "",
        "highlights": [], "weaknesses": [], "raw_text": "",
        "parse_status": "failed",
    }


# ================================================================ 视觉大模型
async def _vision_parse(image_bytes: bytes, mime: str) -> str:
    """OpenAI 兼容多模态调用（base64 图片 + 结构化抽取提示词）。"""
    import httpx

    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY 未配置，无法使用视觉模型")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": settings.LLM_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": _SYSTEM_PROMPT},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 800,
    }
    url = settings.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        resp = await client.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"视觉模型 HTTP {resp.status_code}: {resp.text[:200]}")
    content = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
    if not content:
        raise RuntimeError("视觉模型返回空内容")
    return content


def _load_json(text: str) -> dict[str, Any]:
    from app.ai.utils.output_parser import parse_json
    data = parse_json(text)
    return data if isinstance(data, dict) else {}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    score = data.get("interview_score")
    try:
        score = round(float(score), 2) if score not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        score = None
    if score is not None and not 0 <= score <= 100:
        raise ValueError("面试得分必须在0到100之间")
    return {
        "position": str(data.get("position") or "").strip()[:64] or None,
        "interview_score": score,
        "comprehensive_evaluation": str(data.get("comprehensive_evaluation") or "").strip() or None,
        "highlights": [str(x) for x in (data.get("highlights") or []) if str(x).strip()],
        "weaknesses": [str(x) for x in (data.get("weaknesses") or []) if str(x).strip()],
    }


# ================================================================ 兜底：正则解析手工文本
_FIELD = r"(?:岗位|应聘岗位|position)|(?:面试得分|得分|score)|(?:综合评价|评价|评语)|(?:亮点|优势)|(?:不足|待提升|短板)"
_STOP = r"(?=\n|$|(?:岗位|应聘岗位)\s*[：:]|(?:面试得分|得分)\s*[：:]|(?:综合评价|评价|评语)\s*[：:]|(?:亮点|优势)\s*[：:]|(?:不足|待提升|短板)\s*[：:])"


def _parse_fallback_text(text: str) -> dict[str, Any]:
    sm = re.search(r"(?:面试得分|得分|score)\s*[：:=]\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    pm = re.search(r"(?:岗位|应聘岗位|position)\s*[：:=]\s*(.+?)" + _STOP, text, re.I)
    em = re.search(r"(?:综合评价|评价|评语)\s*[：:=]\s*(.+?)" + _STOP, text, re.I)
    hm = re.search(r"(?:亮点|优势)\s*[：:=]\s*(.+?)" + _STOP, text, re.I)
    wm = re.search(r"(?:不足|待提升|短板)\s*[：:=]\s*(.+?)" + _STOP, text, re.I)
    split_items = lambda s: [x.strip(" •·、;；") for x in re.split(r"[、;；\n]|(?<=[^\d])。", s or "") if x.strip()]
    return {
        "position": pm.group(1).strip()[:64] if pm else None,
        "interview_score": (round(float(sm.group(1)), 2)
                            if sm and 0 <= float(sm.group(1)) <= 100 else None),
        "comprehensive_evaluation": em.group(1).strip() if em else None,
        "highlights": split_items(hm.group(1)) if hm else [],
        "weaknesses": split_items(wm.group(1)) if wm else [],
    }


__all__ = ["parse_record_image", "INTERVIEW_RECORD_COLLECTION"]
