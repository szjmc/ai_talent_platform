"""模块：tests/test_storage_and_llm_adapters.py
职责：MinIO / OSS 存储适配器与 DeepSeek LLM 适配器的离线验收用例。

标签：[架构组] [P0]（对象存储 + 多模型适配，全部离线，不依赖真实 MinIO / OSS / DeepSeek）

覆盖点：
1. 存储注册表包含 local / minio / oss / s3，默认后端可配置、未知后端回退 local；
2. MinIO 适配器：SigV4 签名、PUT / GET / HEAD / DELETE、预签名 URL、体积校验、路径穿越防护；
3. OSS 适配器：Signature V1、虚拟主机地址、CRUD、预签名 URL、密钥缺失报错；
4. DeepSeek 适配器：OpenAI 协议复用、模型可配置、reasoner 参数过滤、无 embedding 显式提示；
5. 模型选择器与 LLMManager 的供应商感知（deepseek 不误用 qwen 模型、向量供应商可独立配置）。
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.llm.base_llm import ChatMessage
from app.adapters.llm.deepseek_adapter import DeepSeekAdapter
from app.adapters.llm.openai_adapter import OpenAIAdapter
from app.adapters.llm.provider_config import provider_configured
from app.adapters.llm.qwen_adapter import QwenAdapter
from app.adapters.storage.base_storage import (
    available_backends,
    get_storage,
    storage_registry,
)
from app.adapters.storage.minio_adapter import MinioStorageAdapter
from app.adapters.storage.oss_adapter import OSSStorageAdapter
from app.ai.core.llm_manager import llm_manager
from app.ai.core.model_selector import (
    fallback_models,
    select_embedding_model,
    select_model,
)
from app.config.settings import settings
from app.core.exceptions import AIError, ExternalServiceError, FileError, ParamError
from app.patterns.registry import llm_registry


# ==================================================================== 工具
def _object_handler(requests: list[httpx.Request]):
    """模拟对象存储：PUT / GET / HEAD / DELETE；键含 missing 时返回 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        missing = "missing" in request.url.path
        if request.method == "PUT":
            return httpx.Response(200, headers={"ETag": '"etag-1"'})
        if request.method == "GET":
            if missing:
                return httpx.Response(
                    404,
                    text="<Error><Code>NoSuchKey</Code><Message>not found</Message></Error>",
                )
            return httpx.Response(200, content=b"hello")
        if request.method == "HEAD":
            if missing:
                return httpx.Response(404)
            return httpx.Response(
                200,
                headers={
                    "content-length": "5",
                    "etag": '"etag-1"',
                    "content-type": "text/plain",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(405)

    return handler


# ==================================================================== 存储注册表
def test_storage_registry_contains_all_backends():
    assert {"local", "minio", "oss", "s3"} <= set(available_backends())
    assert {"local", "minio", "oss", "s3"} <= set(storage_registry.keys())


def test_get_storage_follows_settings_and_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "minio")
    storage = get_storage()
    assert storage.name == "minio"

    # 未注册的后端：回退 local，保证业务不中断
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "unknown-backend")
    assert get_storage().name == "local"


@pytest.mark.asyncio
async def test_object_storage_default_subdir_is_common():
    requests: list[httpx.Request] = []
    adapter = MinioStorageAdapter(transport=httpx.MockTransport(_object_handler(requests)))
    key = await adapter.save(b"hello", "demo.txt")
    assert key.startswith("common/") and key.endswith(".txt")


def test_local_storage_presigned_url_falls_back_to_url():
    from app.adapters.storage.local_storage_adapter import LocalStorageAdapter

    storage = LocalStorageAdapter()
    assert storage.presigned_url("a/b.txt") == storage.url("a/b.txt")


# ==================================================================== MinIO
@pytest.mark.asyncio
async def test_minio_adapter_crud_and_signature(monkeypatch):
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_URL", "")
    requests: list[httpx.Request] = []
    adapter = MinioStorageAdapter(transport=httpx.MockTransport(_object_handler(requests)))

    key = await adapter.save(b"hello", "demo.txt", subdir="resume")
    assert key.startswith("resume/") and key.endswith(".txt")

    put_request = requests[-1]
    assert put_request.method == "PUT"
    assert put_request.url.path == f"/ai-talent/{key}"
    assert put_request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=")
    assert "SignedHeaders=" in put_request.headers["Authorization"]
    assert put_request.headers["x-amz-content-sha256"]
    assert put_request.headers["content-type"] == "text/plain"

    assert await adapter.exists(key) is True
    assert await adapter.size(key) == 5
    assert await adapter.read(key) == b"hello"
    assert adapter.url(key) == f"http://127.0.0.1:9000/ai-talent/{key}"
    assert await adapter.delete(key) is True

    presigned = adapter.presigned_url(key, 600)
    assert presigned.startswith(f"http://127.0.0.1:9000/ai-talent/{key}?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in presigned
    assert "X-Amz-Signature=" in presigned

    info = adapter.describe()
    assert info["bucket"] == "ai-talent" and info["name"] == "minio"
    assert "secret" not in str(info).lower()
    assert "max_size_mb" in info


@pytest.mark.asyncio
async def test_minio_adapter_missing_and_validation(monkeypatch):
    adapter = MinioStorageAdapter(transport=httpx.MockTransport(_object_handler([])))

    assert await adapter.exists("resume/missing.txt") is False
    assert await adapter.size("resume/missing.txt") == 0
    with pytest.raises(FileError):
        await adapter.read("resume/missing.txt")

    with pytest.raises(FileError):
        await adapter.read("../../etc/passwd")

    with pytest.raises(ParamError):
        await adapter.save("not-bytes", "x.txt")  # type: ignore[arg-type]

    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0)
    with pytest.raises(ParamError):
        await adapter.save(b"hello", "x.txt")


@pytest.mark.asyncio
async def test_minio_health_reports_bucket_state():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD" and request.url.path == "/ai-talent":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = MinioStorageAdapter(transport=httpx.MockTransport(handler))
    health = await adapter.health()
    assert health["ok"] is True and health["bucket"] == "ai-talent"
    assert health["backend"] == "minio" and health["name"] == "minio"


@pytest.mark.asyncio
async def test_object_storage_reports_missing_bucket():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            text="<Error><Code>NoSuchBucket</Code><Message>bucket not found</Message></Error>",
        )

    adapter = OSSStorageAdapter(
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        access_key_id="AK_ID",
        access_key_secret="AK_SECRET",
        bucket="missing-bucket",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ExternalServiceError) as exc:
        await adapter.read("a.txt")
    assert "存储桶不存在" in str(exc.value)


def test_s3_client_presigned_url_is_deterministic():
    from datetime import datetime, timezone

    from app.infrastructure.storage.s3_client import S3Client

    client = S3Client(
        endpoint="s3.example.com",
        access_key="AK",
        secret_key="SK",
        bucket="demo",
        region="ap-southeast-1",
        path_style=True,
    )
    moment = datetime(2026, 11, 4, 8, 0, 0, tzinfo=timezone.utc)
    first = client.presigned_url("a/b.txt", 60, now=moment)
    second = client.presigned_url("a/b.txt", 60, now=moment)
    assert first == second
    assert "X-Amz-Credential=AK%2F20261104%2Fap-southeast-1%2Fs3%2Faws4_request" in first
    assert "X-Amz-Expires=60" in first


# ==================================================================== OSS
@pytest.mark.asyncio
async def test_oss_adapter_crud_and_signature():
    requests: list[httpx.Request] = []
    adapter = OSSStorageAdapter(
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        access_key_id="AK_ID",
        access_key_secret="AK_SECRET",
        bucket="talent-bucket",
        transport=httpx.MockTransport(_object_handler(requests)),
    )

    key = await adapter.save(b"hello", "简历.PDF", subdir="knowledge")
    assert key.startswith("knowledge/") and key.endswith(".pdf")

    put_request = requests[-1]
    assert put_request.method == "PUT"
    assert put_request.url.host == "talent-bucket.oss-cn-hangzhou.aliyuncs.com"
    assert put_request.url.path == f"/{key}"
    assert put_request.headers["Authorization"].startswith("OSS AK_ID:")
    assert put_request.headers["Date"].endswith("GMT")
    assert put_request.headers["Content-Type"] == "application/pdf"

    assert await adapter.exists(key) is True
    assert await adapter.size(key) == 5
    assert await adapter.read(key) == b"hello"
    assert adapter.url(key) == f"https://talent-bucket.oss-cn-hangzhou.aliyuncs.com/{key}"
    assert await adapter.delete(key) is True

    presigned = adapter.presigned_url(key, 300)
    assert "OSSAccessKeyId=AK_ID" in presigned and "Expires=" in presigned
    assert "Signature=" in presigned and "AK_SECRET" not in presigned

    info = adapter.describe()
    assert info["bucket"] == "talent-bucket" and info["name"] == "oss"
    assert "AK_SECRET" not in str(info)


def test_oss_adapter_requires_credentials(monkeypatch):
    monkeypatch.setattr(settings, "OSS_BUCKET", "")
    with pytest.raises(ExternalServiceError):
        OSSStorageAdapter()

    monkeypatch.setattr(settings, "OSS_BUCKET", "demo")
    monkeypatch.setattr(settings, "OSS_ACCESS_KEY_ID", "")
    monkeypatch.setattr(settings, "OSS_ACCESS_KEY_SECRET", "")
    with pytest.raises(ExternalServiceError):
        OSSStorageAdapter()


def test_oss_presigned_url_is_deterministic():
    from datetime import datetime, timezone

    from app.infrastructure.storage.oss_client import OSSClient

    client = OSSClient(
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        access_key_id="AK_ID",
        access_key_secret="AK_SECRET",
        bucket="demo",
    )
    moment = datetime(2026, 11, 4, 8, 0, 0, tzinfo=timezone.utc)
    first = client.presigned_url("a/b.txt", 120, now=moment)
    assert first == client.presigned_url("a/b.txt", 120, now=moment)
    assert "OSSAccessKeyId=AK_ID" in first and "Expires=1793779320" in first


# ==================================================================== DeepSeek
def test_deepseek_registered_and_model_configurable(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    assert "deepseek" in llm_registry

    adapter = DeepSeekAdapter()
    assert adapter.provider == "deepseek"
    assert adapter.base_url == "https://api.deepseek.com/v1"
    assert adapter.model == "deepseek-chat"
    assert adapter.supports_embedding is False

    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-reasoner")
    assert DeepSeekAdapter().model == "deepseek-reasoner"

    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://proxy.example.com/v1")
    assert DeepSeekAdapter().base_url == "https://proxy.example.com/v1"


def test_deepseek_uses_provider_credentials(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-deepseek")
    adapter = DeepSeekAdapter()
    assert adapter.api_key == "sk-deepseek"
    assert provider_configured("deepseek") is True
    assert provider_configured("ollama") is True  # 本地模型无需密钥


@pytest.mark.asyncio
async def test_deepseek_chat_reuses_openai_protocol(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_post(self, path: str, payload: dict) -> dict:
        seen["path"] = path
        seen["payload"] = payload
        return {
            "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            "model": payload["model"],
        }

    monkeypatch.setattr(OpenAIAdapter, "_post", fake_post)
    adapter = DeepSeekAdapter(model="deepseek-chat")
    result = await adapter.chat([ChatMessage.user("hi")], temperature=0.2)

    assert seen["path"] == "/chat/completions"
    assert seen["payload"]["model"] == "deepseek-chat"
    assert seen["payload"]["temperature"] == 0.2
    assert result.content == "你好" and result.total_tokens == 5


@pytest.mark.asyncio
async def test_deepseek_reasoner_filters_unsupported_params(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_post(self, path: str, payload: dict) -> dict:
        seen["payload"] = payload
        return {
            "choices": [
                {"message": {"content": "", "reasoning_content": "先分析再回答"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }

    monkeypatch.setattr(OpenAIAdapter, "_post", fake_post)
    adapter = DeepSeekAdapter(model="deepseek-reasoner")
    result = await adapter.chat(
        [ChatMessage.user("hi")],
        temperature=0.8,
        top_p=0.9,
        presence_penalty=0.1,
    )

    payload = seen["payload"]
    assert "temperature" not in payload and "top_p" not in payload
    assert "presence_penalty" not in payload
    # content 为空时回退到思维链，不返回空结果
    assert result.content == "先分析再回答"


@pytest.mark.asyncio
async def test_deepseek_reasoner_stream_filters_unsupported_params(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_stream(self, messages, **kwargs):
        seen["kwargs"] = kwargs
        yield "ok"

    monkeypatch.setattr(OpenAIAdapter, "stream", fake_stream)
    adapter = DeepSeekAdapter(model="deepseek-reasoner")
    chunks = [
        chunk
        async for chunk in adapter.stream(
            [ChatMessage.user("hi")], temperature=0.9, top_p=0.5
        )
    ]
    assert chunks == ["ok"]
    assert "temperature" not in seen["kwargs"] and "top_p" not in seen["kwargs"]


@pytest.mark.asyncio
async def test_deepseek_embedding_is_not_silently_faked():
    adapter = DeepSeekAdapter()
    with pytest.raises(AIError) as exc:
        await adapter.embed(["hello"])
    assert "LLM_EMBED_PROVIDER" in str(exc.value)


@pytest.mark.asyncio
async def test_openai_adapter_list_models(monkeypatch):
    async def fake_get(self, path: str) -> dict:
        assert path == "/models"
        return {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}

    monkeypatch.setattr(OpenAIAdapter, "_get", fake_get)
    assert await OpenAIAdapter().list_models() == ["gpt-4o-mini", "gpt-4o"]


# ==================================================================== 模型选择
def test_model_selector_is_provider_aware(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    # 本用例验证目录默认值，不能受开发者.env中显式模型覆盖影响。
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "")
    # 残留的 qwen 模型名不应被误用
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_MODEL_MAP", "")
    monkeypatch.setattr(settings, "LLM_EMBED_MODEL", "")
    monkeypatch.delenv("LLM_MODEL_CHAT", raising=False)
    monkeypatch.delenv("LLM_MODEL_DEEPSEEK_CHAT", raising=False)

    assert select_model("chat") == "deepseek-chat"
    assert select_model("talent") == "deepseek-reasoner"
    assert select_model("default") == "deepseek-chat"
    assert select_embedding_model() == ""  # DeepSeek 无向量模型

    # 显式 LLM_MODEL 优先（非目录默认值）
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-reasoner")
    assert select_model("chat") == "deepseek-reasoner"

    # LLM_MODEL_MAP 优先级高于目录推荐模型
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_MODEL_MAP", "chat=deepseek-reasoner")
    assert select_model("chat") == "deepseek-reasoner"

    # 环境变量按用途覆盖（供应商级优先）
    monkeypatch.setenv("LLM_MODEL_DEEPSEEK_CHAT", "deepseek-custom")
    assert select_model("chat") == "deepseek-custom"


def test_model_selector_does_not_reuse_qwen_model_for_ollama(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_MODEL_MAP", "")
    assert select_model("chat") == "qwen2.5:7b"  # 目录默认本地模型

    # 显式指定本地模型名时正常生效
    monkeypatch.setattr(settings, "LLM_MODEL", "llama3.1:8b")
    assert select_model("chat") == "llama3.1:8b"


def test_openai_chat_payload_excludes_internal_options(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_post(self, path: str, payload: dict) -> dict:
        seen["payload"] = payload
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(OpenAIAdapter, "_post", fake_post)
    adapter = QwenAdapter(model="qwen-plus")  # options 中带 embed_model
    import asyncio

    asyncio.run(adapter.chat([ChatMessage.user("hi")]))
    assert "embed_model" not in seen["payload"]


def test_fallback_chain_supports_deepseek(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "")
    assert fallback_models("deepseek-reasoner")[0] == "deepseek-chat"
    assert fallback_models("deepseek-chat")[0] == "deepseek-reasoner"

    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "deepseek-chat=deepseek-chat-v3.2")
    assert fallback_models("deepseek-chat") == ["deepseek-chat-v3.2"]


def test_generic_api_key_is_not_sent_to_other_providers(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-generic")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    # 当前供应商可用通用密钥
    assert DeepSeekAdapter().api_key == "sk-generic"
    # 非当前供应商不得误用（避免密钥泄漏到其他厂商）
    assert QwenAdapter().api_key == ""
    assert provider_configured("qwen") is False


def test_generic_base_url_does_not_leak_across_providers(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "")
    assert DeepSeekAdapter().base_url == "https://api.deepseek.com/v1"

    # 当前供应商显式配置通用地址时仍然生效（代理 / 私有网关场景）
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://llm-proxy.example.com/v1")
    assert DeepSeekAdapter().base_url == "https://llm-proxy.example.com/v1"


def test_fallback_chain_stays_within_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "")
    assert fallback_models("qwen2.5:7b") == ["qwen2.5:14b"]


def test_qwen_embedding_model_honors_settings(monkeypatch):
    monkeypatch.setattr(settings, "LLM_EMBED_MODEL", "text-embedding-v4")
    assert QwenAdapter().options["embed_model"] == "text-embedding-v4"


# ==================================================================== LLMManager
def test_llm_manager_creates_deepseek_adapter(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "")
    monkeypatch.setattr(settings, "LLM_MODEL_MAP", "")
    monkeypatch.delenv("LLM_MODEL_CHAT", raising=False)
    monkeypatch.delenv("LLM_MODEL_DEEPSEEK_CHAT", raising=False)
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_EMBED_PROVIDER", "")
    monkeypatch.setattr(settings, "LLM_EMBED_MODEL", "")
    llm_manager.clear_instances()
    try:
        llm = llm_manager.get_llm("chat")
        assert isinstance(llm, DeepSeekAdapter)
        assert llm.model == "deepseek-chat"

        # DeepSeek 无向量能力：未指定 LLM_EMBED_PROVIDER 时给出可操作报错
        with pytest.raises(AIError) as exc:
            llm_manager.get_embedding_llm()
        assert "LLM_EMBED_PROVIDER" in str(exc.value)
    finally:
        llm_manager.clear_instances()


def test_explicit_deepseek_model_overrides_catalog_default(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_MODEL_MAP", "")
    monkeypatch.delenv("LLM_MODEL_CHAT", raising=False)
    monkeypatch.delenv("LLM_MODEL_DEEPSEEK_CHAT", raising=False)
    llm_manager.clear_instances()
    try:
        assert select_model("chat") == "deepseek-flash"
        assert llm_manager.get_llm("chat").model == "deepseek-flash"
    finally:
        llm_manager.clear_instances()


def test_llm_manager_supports_separate_embedding_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "LLM_EMBED_PROVIDER", "qwen")
    monkeypatch.setattr(settings, "LLM_EMBED_MODEL", "")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-qwen")
    llm_manager.clear_instances()
    try:
        embed_llm = llm_manager.get_embedding_llm()
        assert isinstance(embed_llm, QwenAdapter)
        assert embed_llm.model == "text-embedding-v3"
        assert embed_llm.options["embed_model"] == "text-embedding-v3"

        status = llm_manager.status()
        assert status["provider"] == "deepseek"
        assert status["embedding_provider"] == "qwen"
        assert "deepseek" in status["registered_providers"]
    finally:
        llm_manager.clear_instances()
