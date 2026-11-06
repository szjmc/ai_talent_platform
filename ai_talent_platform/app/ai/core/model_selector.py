"""
模块：app/ai/core/model_selector.py
职责：按任务类型 + 供应商选择合适的大模型（不把任何模型写死在业务代码里）。

标签：[架构组] [P1]（被 llm_manager 依赖）

============================ 实现思路与代码要点 ============================
选模型优先级（高 -> 低）：
    1. 环境变量按用途覆盖：LLM_MODEL_<PURPOSE>
       （供应商级：LLM_MODEL_<PROVIDER>_<PURPOSE>，例如 LLM_MODEL_DEEPSEEK_CHAT）
    2. LLM_MODEL_MAP 显式映射：'chat=deepseek-chat,talent=deepseek-reasoner'
       也可写供应商限定 'deepseek.chat=deepseek-reasoner'
    3. 供应商专属模型；或当前供应商匹配且不是目录默认值的 LLM_MODEL
    4. 供应商目录中的「用途推荐模型」（provider_config.PROVIDER_SPECS）
    5. LLM_MODEL（与供应商匹配时）
    6. 供应商目录默认模型

设计要点：
- 供应商目录（provider_config）是唯一模型来源，新增模型只改配置 / 目录，不改业务；
- deepseek / qwen / openai / ollama 各有独立默认值与降级链，切换 LLM_PROVIDER 后
  不会误用上一个厂商的模型名；
- 温度按用途给默认值，结构化输出低温、闲聊高温；调用方仍可显式覆盖。
=========================================================================
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.adapters.llm.provider_config import (
    PROVIDER_SPECS,
    get_provider_spec,
    model_matches_provider,
    parse_fallback_overrides,
    parse_model_overrides,
    provider_configured,
    resolve_embed_model,
    same_provider_models,
    uses_custom_endpoint,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_SPEC = PROVIDER_SPECS["qwen"]

# 用途 -> 模型（兼容旧引用：默认供应商 qwen 的推荐表，实际选择以 select_model 为准）
MODEL_BY_PURPOSE: dict[str, str] = {
    "default": settings.LLM_MODEL or _DEFAULT_SPEC.default_model,
    **_DEFAULT_SPEC.models_by_purpose,
    "embed": _DEFAULT_SPEC.default_embed_model,
}

# 降级链：主模型不可用（超时 / 限流 / 模型下线）时的备用顺序。
# 未列出的模型会按供应商目录自动推导同厂商备选（见 fallback_models）。
FALLBACK_CHAIN: dict[str, list[str]] = {
    "qwen-max": ["qwen-plus", "qwen-turbo"],
    "qwen-plus": ["qwen-turbo", "qwen-max"],
    "qwen-turbo": ["qwen-plus"],
    "deepseek-reasoner": ["deepseek-chat"],
    "deepseek-chat": ["deepseek-reasoner"],
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


# ------------------------------------------------------------------ 内部工具
def _provider(provider: str | None = None) -> str:
    """当前供应商名（统一小写；空值回落到 settings.LLM_PROVIDER）。"""
    return str(provider or settings.LLM_PROVIDER or "").strip().lower()


def _purpose(purpose: str | None) -> str:
    return str(purpose or "default").strip().lower()


def _env_override(purpose: str, provider: str) -> str | None:
    """按用途读取环境变量覆盖：

        LLM_MODEL_<PURPOSE>                通用
        LLM_MODEL_<PROVIDER>_<PURPOSE>     供应商级（优先级更高）
    """
    keys = (
        f"LLM_MODEL_{provider.upper()}_{purpose.upper()}" if provider else "",
        f"LLM_MODEL_{purpose.upper()}",
    )
    for key in keys:
        if not key:
            continue
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def _provider_for_model(model: str) -> str:
    """根据模型名推断所属供应商（用于自动降级链）。"""
    for name, spec in PROVIDER_SPECS.items():
        if spec.model_prefixes and any(
            model.lower().startswith(prefix.lower()) for prefix in spec.model_prefixes
        ):
            return name
    # Ollama 的模型名没有统一前缀，退化为当前供应商
    return _provider()


# ------------------------------------------------------------------ 模型选择
def select_model(purpose: str | None = None, provider: str | None = None) -> str:
    """按用途 + 供应商选模型（优先级见模块 docstring）。"""
    provider = _provider(provider)
    purpose = _purpose(purpose)
    spec = get_provider_spec(provider)

    # 1) 环境变量按用途覆盖
    env_value = _env_override(purpose, provider)
    if env_value:
        return env_value

    # 2) LLM_MODEL_MAP 显式映射（供应商限定优先）
    overrides = parse_model_overrides()
    for key in (f"{provider}.{purpose}", purpose, f"{provider}.default", "default"):
        mapped = overrides.get(key)
        if mapped:
            return mapped

    # 3) 专属模型优先于通用默认；用途映射和进程环境覆盖仍保持更高优先级。
    provider_model = str(getattr(settings, spec.model_attr, "") or "").strip() if spec and spec.model_attr else ""
    if provider_model:
        return provider_model
    generic_model = str(settings.LLM_MODEL or "").strip() if provider == _provider() else ""
    if (
        spec is not None
        and generic_model
        and generic_model != spec.default_model
        and model_matches_provider(provider, generic_model)
    ):
        return generic_model

    # 4) 供应商目录的用途推荐模型
    if spec is not None and purpose not in ("default", ""):
        preferred = spec.preferred_model(purpose)
        if preferred:
            return preferred

    # 5) 通用 LLM_MODEL（仅当模型名符合当前供应商命名） / 6) 目录默认模型
    if generic_model and model_matches_provider(provider, generic_model):
        return generic_model
    if spec is not None:
        return spec.default_model
    return generic_model


def select_provider(purpose: str | None = None) -> str:
    """选择供应商：向量用途可单独指定 LLM_EMBED_PROVIDER，其余用 LLM_PROVIDER。"""
    if _purpose(purpose) == "embed" and settings.LLM_EMBED_PROVIDER:
        return str(settings.LLM_EMBED_PROVIDER).strip().lower()
    return _provider()


def select_temperature(purpose: str | None = None) -> float:
    """按用途返回推荐温度。"""
    return TEMPERATURE_BY_PURPOSE.get(_purpose(purpose), TEMPERATURE_BY_PURPOSE["default"])


def fallback_models(model: str) -> list[str]:
    """返回该模型的降级序列（不含自身）。

    来源：LLM_FALLBACK_MODELS 配置 > 内置 FALLBACK_CHAIN > 供应商目录自动推导。
    """
    if not model:
        return []
    overrides = parse_fallback_overrides()
    if model in overrides:
        return [item for item in dict.fromkeys(overrides[model]) if item and item != model]

    current = _provider()
    if uses_custom_endpoint(current) and model not in same_provider_models(current):
        # 聚合服务完整ID仅按显式配置降级，不能推导为官方厂商别名。
        return []
    chain = list(FALLBACK_CHAIN.get(model) or [])

    if not chain:
        # 先看当前供应商目录是否含该模型（Ollama 的 qwen2.5:7b 等名称与其他厂商前缀重合），
        # 再按模型名前缀推断，避免跨厂商降级。
        provider = current
        if model not in same_provider_models(provider):
            if "/" in model:
                return []
            provider = _provider_for_model(model)
        chain = [candidate for candidate in same_provider_models(provider) if candidate != model][:2]

    # 去重且剔除自身，避免降级到同一个模型
    return [item for item in dict.fromkeys(chain) if item and item != model]


def select_embedding_model(provider: str | None = None) -> str:
    """向量模型（维度需与 Milvus 集合一致）；供应商不提供向量能力时返回空串。"""
    provider = _provider(provider)
    env_value = os.getenv(f"LLM_EMBED_MODEL_{provider.upper()}") or os.getenv("LLM_EMBED_MODEL")
    if env_value and env_value.strip():
        return env_value.strip()
    return resolve_embed_model(provider)


def embedding_available(provider: str | None = None) -> bool:
    """当前配置下是否具备向量化能力（模型名 + 密钥）。"""
    target = _provider(provider)
    spec = get_provider_spec(target)
    if spec is not None and not spec.default_embed_model and not select_embedding_model(target):
        return False
    return provider_configured(target)


def describe_policies(provider: str | None = None) -> dict[str, Any]:
    """导出策略表（便于 /system/info 展示与排障）。"""
    target = _provider(provider)
    spec = get_provider_spec(target)
    return {
        "provider": target,
        "default_model": select_model("default", target),
        "model_by_purpose": {
            purpose: select_model(purpose, target)
            for purpose in MODEL_BY_PURPOSE
            if purpose != "embed"
        },
        "provider_catalog": {
            name: {
                "default_model": item.default_model,
                "embed_model": item.default_embed_model,
                "models_by_purpose": dict(item.models_by_purpose),
            }
            for name, item in PROVIDER_SPECS.items()
        },
        "model_overrides": parse_model_overrides(),
        "temperature_by_purpose": dict(TEMPERATURE_BY_PURPOSE),
        "fallback_chain": {k: list(v) for k, v in FALLBACK_CHAIN.items()},
        "fallback_overrides": parse_fallback_overrides(),
        "embedding_provider": select_provider("embed"),
        "embedding_model": select_embedding_model(target),
        "embedding_available": embedding_available(target),
        "provider_configured": provider_configured(target),
        "provider_default_model": spec.default_model if spec else settings.LLM_MODEL,
    }


__all__ = [
    "FALLBACK_CHAIN",
    "MODEL_BY_PURPOSE",
    "TEMPERATURE_BY_PURPOSE",
    "describe_policies",
    "embedding_available",
    "fallback_models",
    "select_embedding_model",
    "select_model",
    "select_provider",
    "select_temperature",
]
