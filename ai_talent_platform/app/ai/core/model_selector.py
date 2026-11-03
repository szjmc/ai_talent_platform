"""
模块：app/ai/core/model_selector.py
职责：按任务类型选择合适的大模型。

标签：[架构组] [P1]（被 llm_manager 依赖）

============================ 实现思路与代码要点 ============================
用途 -> 模型策略（示例，按 settings 可覆盖）：
    resume_parse   结构化输出稳定（qwen-plus / gpt-4o-mini）
    talent_portrait 推理能力较强（qwen-max / gpt-4o）
    matching       长上下文 + 推理（qwen-plus）
    assistant      成本优先 + 流式（qwen-turbo）
    embed          向量模型（text-embedding-v3 等，维度需与 Milvus 集合一致）

实现要点：
- 策略表集中定义，不要在业务代码里写模型名；
- 支持按环境变量覆盖（LLM_MODEL 为默认，特定用途可用 LLM_MODEL_RESUME 等）；
- 记录「实际使用模型」到结果表，便于效果对比与降级回溯。
=========================================================================
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 用途 -> 模型（默认按通义千问；换厂商时只改这张表 + settings）
MODEL_BY_PURPOSE: dict[str, str] = {
    "default": settings.LLM_MODEL,
    "chat": "qwen-turbo",
    "assistant": "qwen-turbo",
    "resume": "qwen-plus",
    "resume_parse": "qwen-plus",
    "talent": "qwen-max",
    "talent_portrait": "qwen-max",
    "matching": "qwen-plus",
    "assessment": "qwen-plus",
    "training": "qwen-plus",
    "knowledge": "qwen-plus",
    "sql": "qwen-plus",
    "summary": "qwen-turbo",
    "embed": "text-embedding-v3",
}

# 降级链：主模型不可用（超时 / 限流 / 模型下线）时的备用顺序
FALLBACK_CHAIN: dict[str, list[str]] = {
    "qwen-max": ["qwen-plus", "qwen-turbo"],
    "qwen-plus": ["qwen-turbo", "qwen-max"],
    "qwen-turbo": ["qwen-plus"],
    "gpt-4o": ["gpt-4o-mini"],
    "gpt-4o-mini": ["gpt-4o"],
}

# 用途 -> 温度（结构化输出要低温，创意类可高温）
TEMPERATURE_BY_PURPOSE: dict[str, float] = {
    "default": 0.3,
    "chat": 0.6,
    "assistant": 0.6,
    "resume": 0.1,
    "resume_parse": 0.1,
    "talent": 0.2,
    "talent_portrait": 0.2,
    "matching": 0.2,
    "assessment": 0.2,
    "training": 0.3,
    "sql": 0.0,
    "summary": 0.2,
}


def _env_override(purpose: str) -> str | None:
    """按用途读取环境变量覆盖：LLM_MODEL_RESUME / LLM_MODEL_SQL ..."""
    key = f"LLM_MODEL_{purpose.upper()}"
    value = os.getenv(key)
    return value.strip() if value and value.strip() else None


def select_model(purpose: str | None = None) -> str:
    """按用途选模型：环境变量 > 策略表 > settings.LLM_MODEL。"""
    purpose = (purpose or "default").strip()
    return (
        _env_override(purpose)
        or MODEL_BY_PURPOSE.get(purpose)
        or MODEL_BY_PURPOSE.get("default")
        or settings.LLM_MODEL
    )


def select_temperature(purpose: str | None = None) -> float:
    """按用途返回推荐温度。"""
    return TEMPERATURE_BY_PURPOSE.get((purpose or "default").strip(), TEMPERATURE_BY_PURPOSE["default"])


def fallback_models(model: str) -> list[str]:
    """返回该模型的降级序列（不含自身）。"""
    return list(FALLBACK_CHAIN.get(model, []))


def select_embedding_model() -> str:
    """向量模型（维度需与 Milvus 集合一致）。"""
    return os.getenv("LLM_EMBED_MODEL") or MODEL_BY_PURPOSE.get("embed") or "text-embedding-v3"


def describe_policies() -> dict[str, Any]:
    """导出策略表（便于 /system/info 展示与排障）。"""
    return {
        "model_by_purpose": dict(MODEL_BY_PURPOSE),
        "temperature_by_purpose": dict(TEMPERATURE_BY_PURPOSE),
        "fallback_chain": {k: list(v) for k, v in FALLBACK_CHAIN.items()},
        "embedding_model": select_embedding_model(),
        "default_model": settings.LLM_MODEL,
        "provider": settings.LLM_PROVIDER,
    }


__all__ = [
    "FALLBACK_CHAIN",
    "MODEL_BY_PURPOSE",
    "TEMPERATURE_BY_PURPOSE",
    "describe_policies",
    "fallback_models",
    "select_embedding_model",
    "select_model",
    "select_temperature",
]
