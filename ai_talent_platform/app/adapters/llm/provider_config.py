"""
模块：app/adapters/llm/provider_config.py
职责：LLM 供应商配置目录（供应商 -> 默认地址 / 默认模型 / 专属环境变量）。

标签：[架构组] [P1]  依赖：settings（模型与密钥的唯一配置来源）

============================ 实现思路与代码要点 ============================
为什么单独抽一层：
- 平台不把「某个模型 / 某个厂商」写死在业务代码或适配器里，而是集中维护一份
  「供应商目录」；新增 DeepSeek、Moonshot、自建 vLLM 时只加一条目录 + 一个适配器类；
- 配置优先级（高 -> 低）：
      1. 调用方显式传入（model / api_key / base_url 参数）
      2. 供应商专属配置（DEEPSEEK_MODEL / DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL ...）
      3. 通用配置（LLM_MODEL / LLM_API_KEY / LLM_BASE_URL，仅对当前供应商生效）
      4. 目录内置默认值（见 PROVIDER_SPECS）
- LLM_MODEL 只有「与当前供应商匹配」时才作为通用默认值生效（例如 provider=deepseek
  而 LLM_MODEL 仍是 qwen-plus 时不会被误用，自动回落到 deepseek 的目录默认模型）。

扩展方式：
    PROVIDER_SPECS["moonshot"] = LLMProviderSpec(
        name="moonshot",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        api_key_attr="MOONSHOT_API_KEY",
        model_prefixes=("moonshot",),
    )
    然后在适配器文件里 @llm_registry.register("moonshot") 即可，无需改调度代码。
=========================================================================
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.config.settings import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 供应商目录
@dataclass(frozen=True)
class LLMProviderSpec:
    """一个 LLM 供应商的静态描述（默认值 + 配置项名称 + 模型命名特征）。"""

    name: str
    label: str
    default_base_url: str
    default_model: str
    default_embed_model: str = ""
    api_key_attr: str = ""
    base_url_attr: str = ""
    model_attr: str = ""
    requires_api_key: bool = True
    # 模型名前缀特征：用于判断 LLM_MODEL 是否属于当前供应商，避免切换厂商后误用旧模型名
    model_prefixes: tuple[str, ...] = ()
    # 用途 -> 推荐模型（不写默认用途 "default"，由 LLM_MODEL 决定）
    models_by_purpose: Mapping[str, str] = field(default_factory=dict)

    def preferred_model(self, purpose: str) -> str | None:
        return self.models_by_purpose.get(purpose)


_QWEN_PURPOSE_MODELS = {
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
}

_DEEPSEEK_PURPOSE_MODELS = {
    "chat": "deepseek-chat",
    "assistant": "deepseek-chat",
    "resume": "deepseek-chat",
    "resume_parse": "deepseek-chat",
    "talent": "deepseek-reasoner",
    "talent_portrait": "deepseek-reasoner",
    "matching": "deepseek-chat",
    "assessment": "deepseek-chat",
    "training": "deepseek-chat",
    "knowledge": "deepseek-chat",
    "sql": "deepseek-chat",
    "summary": "deepseek-chat",
}

_OPENAI_PURPOSE_MODELS = {
    "chat": "gpt-4o-mini",
    "assistant": "gpt-4o-mini",
    "resume": "gpt-4o-mini",
    "resume_parse": "gpt-4o-mini",
    "talent": "gpt-4o",
    "talent_portrait": "gpt-4o",
    "matching": "gpt-4o-mini",
    "assessment": "gpt-4o-mini",
    "training": "gpt-4o-mini",
    "knowledge": "gpt-4o-mini",
    "sql": "gpt-4o-mini",
    "summary": "gpt-4o-mini",
}

_OLLAMA_PURPOSE_MODELS = {
    "chat": "qwen2.5:7b",
    "assistant": "qwen2.5:7b",
    "resume": "qwen2.5:7b",
    "talent": "qwen2.5:14b",
    "matching": "qwen2.5:7b",
    "sql": "qwen2.5:7b",
    "summary": "qwen2.5:7b",
}


PROVIDER_SPECS: dict[str, LLMProviderSpec] = {
    "qwen": LLMProviderSpec(
        name="qwen",
        label="通义千问（阿里云 DashScope 兼容模式）",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        default_embed_model="text-embedding-v3",
        api_key_attr="DASHSCOPE_API_KEY",
        base_url_attr="DASHSCOPE_BASE_URL",
        model_attr="DASHSCOPE_MODEL",
        model_prefixes=("qwen", "qwq"),
        models_by_purpose=_QWEN_PURPOSE_MODELS,
    ),
    "deepseek": LLMProviderSpec(
        name="deepseek",
        label="DeepSeek（OpenAI 兼容协议）",
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        default_embed_model="",  # DeepSeek 不提供向量接口，需另配 LLM_EMBED_PROVIDER
        api_key_attr="DEEPSEEK_API_KEY",
        base_url_attr="DEEPSEEK_BASE_URL",
        model_attr="DEEPSEEK_MODEL",
        model_prefixes=("deepseek",),
        models_by_purpose=_DEEPSEEK_PURPOSE_MODELS,
    ),
    "openai": LLMProviderSpec(
        name="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        default_embed_model="text-embedding-3-small",
        api_key_attr="OPENAI_API_KEY",
        base_url_attr="OPENAI_BASE_URL",
        model_attr="OPENAI_MODEL",
        model_prefixes=("gpt-", "chatgpt", "o1", "o3", "o4"),
        models_by_purpose=_OPENAI_PURPOSE_MODELS,
    ),
    "ollama": LLMProviderSpec(
        name="ollama",
        label="Ollama 本地模型",
        default_base_url="http://127.0.0.1:11434",
        default_model="qwen2.5:7b",
        default_embed_model="bge-m3",
        base_url_attr="OLLAMA_BASE_URL",
        model_attr="OLLAMA_MODEL",
        requires_api_key=False,
        model_prefixes=(),
        models_by_purpose=_OLLAMA_PURPOSE_MODELS,
    ),
}


# ------------------------------------------------------------------ 目录查询
def get_provider_spec(provider: str | None) -> LLMProviderSpec | None:
    """按名称取供应商目录；未登记返回 None（允许自定义注册的供应商）。"""
    if not provider:
        return None
    return PROVIDER_SPECS.get(str(provider).strip().lower())


def provider_names() -> list[str]:
    return sorted(PROVIDER_SPECS)


def _setting(attr: str | None) -> str:
    """读取 settings 上的字符串配置（缺失 / None 一律视为空串）。"""
    if not attr:
        return ""
    value = getattr(settings, attr, "")
    return str(value).strip() if value is not None else ""


def resolve_api_key(provider: str, explicit: str | None = None) -> str:
    """密钥优先级：显式参数 > 供应商专属配置 > LLM_API_KEY（仅当前供应商）。

    通用 LLM_API_KEY 只在 provider 与 settings.LLM_PROVIDER 一致时兜底，
    避免把 A 厂商的密钥发给 B 厂商（例如 DeepSeek 对话 + qwen 向量时误用同一个 key）。
    """
    if explicit is not None:
        return explicit
    spec = get_provider_spec(provider)
    provider_key = _setting(spec.api_key_attr if spec else "")
    if provider_key:
        return provider_key
    is_current = str(settings.LLM_PROVIDER or "").strip().lower() == str(provider).strip().lower()
    return _setting("LLM_API_KEY") if is_current else ""


def resolve_base_url(provider: str, explicit: str | None = None) -> str:
    """地址优先级：显式参数 > 供应商专属配置 > LLM_BASE_URL（仅当前供应商）> 目录默认值。

    通用 LLM_BASE_URL 只在 provider 与 settings.LLM_PROVIDER 一致时生效，
    避免切换供应商后沿用上一个厂商的地址（例如残留的 DashScope 地址）。
    """
    if explicit is not None:
        return explicit.rstrip("/")
    spec = get_provider_spec(provider)
    provider_base = _setting(spec.base_url_attr if spec else "")
    if provider_base:
        return provider_base.rstrip("/")

    generic_base = _setting("LLM_BASE_URL")
    is_current = str(settings.LLM_PROVIDER or "").strip().lower() == str(provider).strip().lower()
    if is_current and generic_base and not _is_other_provider_default(provider, generic_base):
        return generic_base.rstrip("/")
    return (spec.default_base_url if spec else generic_base).rstrip("/")


def _is_other_provider_default(provider: str, base_url: str) -> bool:
    """判断地址是否为「其他供应商」的默认地址（.env 残留场景，例如切到 deepseek 仍留着 DashScope 地址）。"""
    normalized = base_url.rstrip("/")
    for name, spec in PROVIDER_SPECS.items():
        if name != provider and spec.default_base_url.rstrip("/") == normalized:
            return True
    return False


def model_matches_provider(provider: str, model: str | None) -> bool:
    """判断模型名是否属于该供应商。

    - 自定义供应商（目录未登记）一律视为匹配；
    - 原生供应商按前缀判断；OpenAI兼容自定义端点允许完整ID，但排除残留的其他目录默认模型；
    - 无前缀特征的供应商（Ollama 本地模型名）需排除「明显属于其他供应商」的模型名，
      避免 provider=ollama 时误用残留的 qwen-plus / gpt-4o 默认值。
    """
    if not model:
        return False
    spec = get_provider_spec(provider)
    lowered = str(model).strip().lower()
    if spec is None:
        return True
    # openai也是兼容协议适配器：私有网关/聚合服务的显式模型ID不遵循GPT命名。
    # 原生OpenAI与其他厂商仍保留前缀检查，避免切换供应商时误用旧模型。
    if spec.name == "openai" and uses_custom_endpoint(provider):
        # 保留切换厂商时忽略旧目录默认模型的行为；确需该别名可用专属配置/用途映射明确指定。
        return lowered not in {
            other.default_model.lower() for name, other in PROVIDER_SPECS.items() if name != spec.name
        }
    if spec.model_prefixes:
        return any(lowered.startswith(prefix.lower()) for prefix in spec.model_prefixes)
    for name, other in PROVIDER_SPECS.items():
        if name == provider or not other.model_prefixes:
            continue
        if any(lowered.startswith(prefix.lower()) for prefix in other.model_prefixes):
            return False
    return True


def uses_custom_endpoint(provider: str) -> bool:
    """是否配置了目录默认地址之外的服务；不根据模型品牌猜测服务端能力。"""
    spec = get_provider_spec(provider)
    return spec is not None and resolve_base_url(provider) != spec.default_base_url.rstrip("/")


def resolve_model(provider: str, explicit: str | None = None) -> str:
    """模型优先级：显式参数 > 供应商专属配置 > LLM_MODEL（须匹配供应商）> 目录默认模型。"""
    if explicit:
        return explicit
    spec = get_provider_spec(provider)
    provider_model = _setting(spec.model_attr if spec else "")
    if provider_model:
        return provider_model
    generic = _setting("LLM_MODEL")
    is_current = str(settings.LLM_PROVIDER or "").strip().lower() == str(provider).strip().lower()
    if is_current and generic and model_matches_provider(provider, generic):
        return generic
    if spec:
        return spec.default_model
    return generic


def resolve_embed_model(provider: str, explicit: str | None = None) -> str:
    """向量模型优先级：显式参数 > LLM_EMBED_MODEL > 目录默认向量模型（可能为空）。"""
    if explicit:
        return explicit
    spec = get_provider_spec(provider)
    return _setting("LLM_EMBED_MODEL") or (spec.default_embed_model if spec else "")


def provider_configured(provider: str) -> bool:
    """供应商是否具备调用条件（本地 Ollama 不需要密钥）。"""
    spec = get_provider_spec(provider)
    if spec is not None and not spec.requires_api_key:
        return True
    return bool(resolve_api_key(provider))


def same_provider_models(provider: str) -> list[str]:
    """该供应商目录内可用的模型清单（去重，用于降级链推导）。"""
    spec = get_provider_spec(provider)
    if spec is None:
        return []
    ordered = list(spec.models_by_purpose.values()) + [spec.default_model]
    return list(dict.fromkeys(model for model in ordered if model))


# ------------------------------------------------------------------ 覆盖配置解析
def parse_model_overrides(raw: str | None = None) -> dict[str, str]:
    """解析「用途 -> 模型」覆盖配置，支持三种写法：

        LLM_MODEL_MAP='{"chat": "deepseek-chat", "talent": "deepseek-reasoner"}'
        LLM_MODEL_MAP='chat=deepseek-chat,talent=deepseek-reasoner'
        LLM_MODEL_MAP='deepseek.chat=deepseek-reasoner'   # 供应商限定（优先级更高）

    返回的键统一小写；供应商限定键保留 `provider.purpose` 形式。
    """
    text = (settings.LLM_MODEL_MAP if raw is None else raw) or ""
    text = text.strip()
    if not text:
        return {}

    parsed: dict[str, Any] = {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            logger.warning("LLM_MODEL_MAP 不是合法 JSON，已忽略：%s", text[:200])
            return {}
    else:
        for item in text.replace(";", ",").split(","):
            chunk = item.strip()
            if not chunk or "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            parsed[key.strip()] = value.strip()

    result: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(value, str) or not value.strip():
            continue
        result[str(key).strip().lower()] = value.strip()
    return result


def parse_fallback_overrides(raw: str | None = None) -> dict[str, list[str]]:
    """解析模型降级链追加配置，例如：

        LLM_FALLBACK_MODELS='deepseek-reasoner=deepseek-chat,deepseek-chat=deepseek-reasoner'
        LLM_FALLBACK_MODELS='qwen-max=qwen-plus/qwen-turbo'    # 兼容旧厂商别名分隔
        LLM_FALLBACK_MODELS='org/main=org/backup|other/backup' # 完整ID使用 | 分隔
        LLM_FALLBACK_MODELS='{"org/main": ["org/backup"]}'   # JSON数组无歧义

    注意：键值统一用 `=` 分隔（模型名本身可能含 `:`，如 ollama 的 qwen2.5:7b）。
    """
    text = (settings.LLM_FALLBACK_MODELS if raw is None else raw) or ""
    text = text.strip()
    result: dict[str, list[str]] = {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            logger.warning("LLM_FALLBACK_MODELS 不是合法JSON，已忽略")
            return {}
        for key, value in parsed.items():
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                result[str(key).strip()] = [item.strip() for item in value if item.strip()]
        return result

    for item in text.replace(";", ",").split(","):
        chunk = item.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        # 只拆分已知原生别名的旧斜杠写法；未知namespace/model必须完整保留。
        models = []
        for part in value.split("|"):
            pieces = [piece.strip() for piece in part.split("/")]
            legacy_aliases = len(pieces) > 1 and all(
                piece and any(
                    piece in same_provider_models(name)
                    or (name == "ollama" and ":" in piece)
                    for name, spec in PROVIDER_SPECS.items()
                ) for piece in pieces
            )
            models.extend(pieces if legacy_aliases else [part.strip()])
        if key.strip():
            # 空列表明确关闭该模型的降级，不能再回落内置链。
            models = [model for model in models if model]
            result[key.strip()] = models
    return result


def describe_providers() -> list[dict[str, Any]]:
    """供应商目录与当前配置状态（脱敏，不含密钥），供 /system/info 排障。"""
    current = (settings.LLM_PROVIDER or "").lower()
    return [
        {
            "provider": spec.name,
            "label": spec.label,
            "current": spec.name == current,
            "default_model": spec.default_model,
            "base_url": resolve_base_url(spec.name),
            "embed_model": resolve_embed_model(spec.name),
            "api_key_configured": provider_configured(spec.name),
            "requires_api_key": spec.requires_api_key,
        }
        for spec in PROVIDER_SPECS.values()
    ]


__all__ = [
    "LLMProviderSpec",
    "PROVIDER_SPECS",
    "describe_providers",
    "get_provider_spec",
    "model_matches_provider",
    "parse_fallback_overrides",
    "parse_model_overrides",
    "provider_configured",
    "provider_names",
    "resolve_api_key",
    "resolve_base_url",
    "resolve_embed_model",
    "resolve_model",
    "same_provider_models",
    "uses_custom_endpoint",
]
