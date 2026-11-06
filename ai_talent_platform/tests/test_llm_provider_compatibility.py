"""公共模型配置兼容回归：原生厂商、兼容服务、供应商隔离和失败降级。

标签：[架构组]+[Vanna组] [P1]（MockTransport验证完整请求，不访问外部服务）

默认策略沿用目录，显式模型及密钥优先级可追溯；所有配置修改仅在测试中生效。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.llm.base_llm import BaseLLM, ChatMessage
from app.adapters.llm.provider_config import (
    PROVIDER_SPECS, parse_fallback_overrides, resolve_base_url, resolve_model,
)
from app.ai.core.llm_manager import llm_manager
from app.ai.core.model_selector import fallback_models, select_model
from app.config.settings import settings
from app.core.exceptions import AIError


@pytest.fixture(autouse=True)
def isolated_provider_config(monkeypatch):
    """防止本地.env、进程用途覆盖和上个用例的实例缓存影响兼容性结论。"""
    import os

    for key in list(os.environ):
        if key.startswith("LLM_MODEL_"):
            monkeypatch.delenv(key)
    for spec in PROVIDER_SPECS.values():
        for attr in (spec.api_key_attr, spec.base_url_attr, spec.model_attr):
            if attr:
                monkeypatch.setattr(settings, attr, "")
    for key, value in {
        "LLM_PROVIDER": "qwen", "LLM_MODEL": "qwen-plus", "LLM_MODEL_MAP": "",
        "LLM_BASE_URL": PROVIDER_SPECS["qwen"].default_base_url,
        "LLM_API_KEY": "offline-generic-key", "LLM_FALLBACK_MODELS": "",
        "LLM_EMBED_PROVIDER": "", "LLM_EMBED_MODEL": "", "LLM_MAX_RETRIES": 0,
    }.items():
        monkeypatch.setattr(settings, key, value)
    llm_manager.clear_instances()

    async def no_record(*args, **kwargs):
        return None

    monkeypatch.setattr(llm_manager, "_record", no_record)
    yield
    llm_manager.clear_instances()


@pytest.mark.parametrize("provider", ["qwen", "deepseek", "openai", "ollama"])
@pytest.mark.parametrize("purpose", ["chat", "resume", "talent", "sql", "summary"])
def test_native_provider_purpose_defaults_are_preserved(monkeypatch, provider, purpose):
    """切换原生厂商时，即使仍有qwen默认值也不能破坏同事原来的推荐策略。"""
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    spec = PROVIDER_SPECS[provider]
    expected = spec.models_by_purpose[purpose]
    adapter = llm_manager.get_llm(purpose)
    assert select_model(purpose) == adapter.model == expected
    assert adapter.base_url == spec.default_base_url
    assert adapter is llm_manager.get_llm(purpose)


@pytest.mark.parametrize("model", ["deepseek-ai/DeepSeek-V4.1-Flash", "Qwen/Qwen3-8B", "private-model"])
@pytest.mark.parametrize("purpose", ["sql", "summary"])
def test_compatible_endpoint_keeps_explicit_model_id(monkeypatch, model, purpose):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://compatible.example/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", model)
    assert select_model(purpose) == resolve_model("openai") == model
    assert llm_manager.get_llm(purpose).model == model
    assert fallback_models(model) == []  # 未配置的完整ID不推导官方别名。


@pytest.mark.parametrize("provider", ["qwen", "deepseek", "openai", "ollama"])
def test_provider_model_and_endpoint_priority(monkeypatch, provider):
    spec = PROVIDER_SPECS[provider]
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, spec.model_attr, "dedicated/custom-model")
    monkeypatch.setattr(settings, spec.base_url_attr, "https://dedicated.example/v1")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://generic.example/v1")
    if spec.api_key_attr:
        monkeypatch.setattr(settings, spec.api_key_attr, "offline-dedicated-key")
        monkeypatch.setattr(settings, "LLM_API_KEY", "")
    adapter = llm_manager.get_llm("sql")
    assert adapter.model == "dedicated/custom-model"
    assert adapter.base_url == resolve_base_url(provider) == "https://dedicated.example/v1"
    if spec.api_key_attr:
        assert adapter.api_key == "offline-dedicated-key"
        assert llm_manager.status()["api_key_configured"] is True
    assert llm_manager.status()["base_url"] == adapter.base_url

    monkeypatch.setattr(settings, "LLM_MODEL_MAP", f"{provider}.sql=mapped/model")
    assert select_model("sql") == "mapped/model"
    monkeypatch.setenv("LLM_MODEL_SQL", "environment/model")
    assert select_model("sql") == "environment/model"
    monkeypatch.setenv(f"LLM_MODEL_{provider.upper()}_SQL", "provider-environment/model")
    assert select_model("sql") == "provider-environment/model"


def test_separate_embedding_provider_does_not_receive_chat_credentials(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "LLM_API_KEY", "offline-chat-key")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://chat-gateway.example/v1")
    monkeypatch.setattr(settings, "LLM_EMBED_PROVIDER", "qwen")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "offline-vector-key")
    adapter = llm_manager.get_embedding_llm()
    assert adapter.api_key == "offline-vector-key"
    assert adapter.base_url == PROVIDER_SPECS["qwen"].default_base_url
    assert adapter.model == "text-embedding-v3"

    # 未提供非当前厂商的专属密钥时，不得兜底使用对话密钥。
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    llm_manager.clear_instances()
    assert llm_manager.get_embedding_llm().api_key == ""


@pytest.mark.parametrize("stale_model", ["qwen-plus", "deepseek-chat", "qwen2.5:7b"])
def test_openai_proxy_keeps_ignoring_other_provider_default_model(monkeypatch, stale_model):
    """旧环境残留厂商默认模型时仍用GPT推荐值；显式专属配置可覆盖这个保护。"""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", stale_model)
    assert select_model("sql") == "gpt-4o-mini"
    monkeypatch.setattr(settings, "OPENAI_MODEL", stale_model)
    assert select_model("sql") == stale_model


@pytest.mark.parametrize("raw,expected", [
    ("qwen-max=qwen-plus/qwen-turbo", {"qwen-max": ["qwen-plus", "qwen-turbo"]}),
    ("qwen-max=qwen-plus|qwen-turbo", {"qwen-max": ["qwen-plus", "qwen-turbo"]}),
    ("qwen2.5:14b=qwen2.5:7b/llama3:8b", {"qwen2.5:14b": ["qwen2.5:7b", "llama3:8b"]}),
    ("org/main=Qwen/model-b", {"org/main": ["Qwen/model-b"]}),
    ("org/main=deepseek-ai/DeepSeek-V4.1-Flash", {"org/main": ["deepseek-ai/DeepSeek-V4.1-Flash"]}),
    ("org/main=Qwen/model-b|other/model-c", {"org/main": ["Qwen/model-b", "other/model-c"]}),
    ('{"org/main": ["Qwen/model-b", "other/model-c"]}', {"org/main": ["Qwen/model-b", "other/model-c"]}),
    ("qwen-plus=", {"qwen-plus": []}),
    ('{"qwen-plus": []}', {"qwen-plus": []}),
])
def test_fallback_formats_keep_legacy_aliases_and_namespaced_ids(raw, expected):
    assert parse_fallback_overrides(raw) == expected


def test_explicit_empty_chain_disables_native_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "qwen-plus=")
    assert fallback_models("qwen-plus") == []


def test_known_openai_proxy_fallback_stays_compatible(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://proxy.example/v1")
    assert fallback_models("gpt-4o-mini") == ["gpt-4o"]


def offline_client(monkeypatch, handler):
    def client(adapter):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=adapter.default_headers())

    monkeypatch.setattr(BaseLLM, "client", client)


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["sql", "summary", "resume_parse", "matching"])
async def test_full_compatible_request_preserves_configuration(monkeypatch, purpose):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://compatible.example/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", "org/model")
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == "https://compatible.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer offline-generic-key"
        payload = json.loads(request.content)
        assert payload["model"] == "org/model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    offline_client(monkeypatch, respond)
    result = await llm_manager.chat([ChatMessage.user("test")], purpose=purpose)
    assert result.content == "ok" and result.model == "org/model"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_permanent_http_errors_do_not_retry_or_fallback(monkeypatch, status):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 3)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "offline failure"})

    offline_client(monkeypatch, respond)
    with pytest.raises(AIError):
        await llm_manager.chat([ChatMessage.user("test")], purpose="sql")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 409, 429, 500, 503])
async def test_recoverable_http_errors_keep_native_fallback(monkeypatch, status):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o-mini")
    models = []

    def respond(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "gpt-4o-mini":
            return httpx.Response(status, json={"error": "offline transient failure"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    offline_client(monkeypatch, respond)
    assert (await llm_manager.chat([ChatMessage.user("test")])).model == "gpt-4o"
    assert models == ["gpt-4o-mini", "gpt-4o"]


@pytest.mark.asyncio
async def test_configured_namespaced_fallback_is_sent_intact(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://compatible.example/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", "org/main")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "org/main=Qwen/backup")
    models = []

    def respond(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "org/main":
            return httpx.Response(503, json={"error": "offline unavailable"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    offline_client(monkeypatch, respond)
    assert (await llm_manager.chat([ChatMessage.user("test")], purpose="sql")).model == "Qwen/backup"
    assert models == ["org/main", "Qwen/backup"]


@pytest.mark.asyncio
async def test_network_error_keeps_existing_manager_fallback(monkeypatch):
    """新增4xx分类不能误伤原有网络故障备用调用，HTTP请求仍由MockTransport处理。"""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o-mini")
    models = []

    def respond(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "gpt-4o-mini":
            raise httpx.ConnectError("offline connection failure", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    offline_client(monkeypatch, respond)
    assert (await llm_manager.chat([ChatMessage.user("test")])).model == "gpt-4o"
    assert models == ["gpt-4o-mini", "gpt-4o"]
