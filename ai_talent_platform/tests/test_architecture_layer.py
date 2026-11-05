"""架构组能力层测试：上下文 / 输出解析 / 重试 / LLM 管理 / Agent 调度 / 记忆 / 存储 / 事件 / 策略。

标签：[架构组] [P0]（架构组 38 用例验收）

全部用例离线运行，不依赖真实大模型、Milvus、Redis 与 PgSQL。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.llm.base_llm import (
    BaseLLM,
    ChatMessage,
    LLMResponseError,
    LLMResult,
    LLMRetryableError,
)
from app.ai.agent import AGENT_TYPES, ai_runtime_status, dispatch, dispatch_stream
from app.ai.context import (
    clear_context,
    context_scope,
    get_context,
    set_context,
    update_context,
)
from app.ai.core.llm_manager import LLMManager, llm_manager
from app.ai.core.model_selector import (
    fallback_models,
    select_model,
    select_temperature,
)
from app.ai.core.token_manager import TokenManager
from app.ai.memory.conversation_memory import ConversationMemory
from app.ai.memory.state_manager import StateManager, to_jsonable
from app.ai.utils.output_parser import extract_json, parse_json, parse_model
from app.ai.utils.retry_handler import (
    CircuitBreakerOpenError,
    SimpleCircuitBreaker,
    ai_retry,
)
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessRuleError, NotFoundError
from app.infrastructure.messaging.event_bus import EventBus
from app.patterns.registry import agent_registry, llm_registry
from app.patterns.strategy import (
    HardFilterStrategy,
    HybridStrategy,
    MatchStrategyFactory,
    SoftMatchStrategy,
)


# ==================================================================== 测试替身
class FakeLLM(BaseLLM):
    """离线 LLM 适配器：按 model 决定成功 / 失败，用于验证降级链。"""

    provider = "fake"
    default_model = "fake-model"

    def __init__(self, model: str | None = None, **kwargs: Any) -> None:
        super().__init__(model or self.default_model, **kwargs)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, *, temperature=None, max_tokens=None, **kwargs) -> LLMResult:
        model = kwargs.get("model") or self.model
        self.calls.append({"model": model, "messages": list(messages)})
        if model == "broken-model":
            raise LLMRetryableError("模型不可用")
        return LLMResult(
            content='{"score": 90, "level": "高级"}',
            model=model,
            prompt_tokens=10,
            completion_tokens=5,
            elapsed_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _ResultSchema(BaseModel):
    score: int = 0
    level: str = ""


# ==================================================================== 上下文
def test_context_set_get_clear():
    clear_context()
    assert get_context().is_anonymous is True
    set_context({"user_id": 7, "roles": ["hr"], "organization_id": 3}, trace_id="t-1")
    ctx = get_context()
    assert ctx.user_id == 7 and ctx.roles == ["hr"] and ctx.organization_id == 3
    assert ctx.trace_id == "t-1" and ctx.data_scope == "all"  # hr 的数据范围为全量
    assert ctx.has_permission("talent:read") is True
    clear_context()
    assert get_context().is_anonymous is True


def test_context_from_user_object():
    class User:
        id = 9
        username = "zhangsan"
        organization_id = 2
        role_codes = ["super_admin"]

    with context_scope(User()) as ctx:
        assert ctx.user_id == 9 and ctx.is_super_admin is True
        assert ctx.data_scope == "all"
    assert get_context().is_anonymous is True


def test_update_context_and_to_dict():
    clear_context()
    set_context({"user_id": 1, "roles": ["employee"]})
    update_context(conversation_id="c-1")
    assert get_context().conversation_id == "c-1"
    assert set(get_context().to_dict()) >= {"user_id", "roles", "data_scope"}
    clear_context()


# ==================================================================== 输出解析
@pytest.mark.parametrize(
    "text",
    [
        '{"name": "张三", "years": 5}',
        '```json\n{"name": "张三", "years": 5}\n```',
        '好的，结果如下：\n{"name": "张三", "years": 5}\n以上仅供参考',
        '{"name": "张三", "years": 5,}',
        "{'name': '张三', 'years': 5}",
    ],
)
def test_parse_json_tolerant(text):
    data = parse_json(text)
    assert data["name"] == "张三"
    assert data["years"] == 5


def test_parse_json_list_and_invalid():
    assert parse_json("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises(AIError):
        parse_json("这不是 JSON，也没有大括号")


def test_extract_json_snippet():
    assert extract_json('前缀 {"a": 1} 后缀') == '{"a": 1}'


def test_parse_model_with_pydantic():
    model = parse_model('```json\n{"score": 88, "level": "高级"}\n```', _ResultSchema)
    assert model.score == 88 and model.level == "高级"
    with pytest.raises(AIError):
        parse_model("无 JSON 内容", _ResultSchema)


# ==================================================================== 重试与熔断
@pytest.mark.asyncio
async def test_ai_retry_retries_then_succeeds():
    state = {"count": 0}

    @ai_retry(max_attempts=3, base_delay=0.001)
    async def flaky() -> str:
        state["count"] += 1
        if state["count"] < 3:
            raise LLMRetryableError("临时失败")
        return "ok"

    assert await flaky() == "ok"
    assert state["count"] == 3


@pytest.mark.asyncio
async def test_ai_retry_does_not_retry_business_error():
    state = {"count": 0}

    @ai_retry(max_attempts=3, base_delay=0.001)
    async def business_error() -> None:
        state["count"] += 1
        raise BusinessRuleError("参数不合法")

    with pytest.raises(BusinessRuleError):
        await business_error()
    assert state["count"] == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_resets(monkeypatch):
    breaker = SimpleCircuitBreaker(name="demo", fail_threshold=2, reset_seconds=1)
    now = [0.0]
    monkeypatch.setattr("app.ai.utils.retry_handler.time.monotonic", lambda: now[0])

    async def always_fail() -> None:
        raise LLMRetryableError("boom")

    with pytest.raises(LLMRetryableError):
        await breaker.call(always_fail)
    assert breaker.state == "closed"
    with pytest.raises(LLMRetryableError):
        await breaker.call(always_fail)
    assert breaker.state == "open"

    with pytest.raises(CircuitBreakerOpenError):
        await breaker.call(always_fail)

    now[0] = 5.0  # 超过 reset_seconds -> 半开
    assert breaker.state == "half_open"
    breaker.record_success()
    assert breaker.is_closed and breaker.fail_count == 0


# ==================================================================== 模型选择
def test_select_model_and_fallback(monkeypatch):
    assert select_model() == settings.LLM_MODEL or select_model() == "qwen-plus"
    monkeypatch.setenv("LLM_MODEL_MATCHING", "custom-model")
    assert select_model("matching") == "custom-model"
    assert select_temperature("resume_parse") < select_temperature("assistant")
    assert fallback_models("qwen-max")[0] == "qwen-plus"


# ==================================================================== Token 配额
@pytest.mark.asyncio
async def test_token_manager_records_and_limits():
    manager = TokenManager(daily_limit=100)
    entry = await manager.record(1, "qwen-plus", prompt_tokens=60, completion_tokens=40, purpose="chat")
    assert entry["total_tokens"] == 100 and entry["used_today"] == 100
    assert await manager.usage_today(1) == 100
    with pytest.raises(AIError):
        await manager.check_quota(1, need=1)
    summary = await manager.usage_summary(1)
    assert summary["remaining"] == 0 and summary["by_model"] == {"qwen-plus": 100}
    assert manager.estimate_cost("qwen-plus", 1000, 0) > 0


# ==================================================================== LLM 管理
@pytest.fixture()
def fake_provider(monkeypatch):
    """注册 fake provider 并让 LLMManager 使用它。"""
    llm_registry.add("fake", FakeLLM)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr("app.ai.core.llm_manager.select_model", lambda purpose=None: "fake-model")
    monkeypatch.setattr("app.ai.core.llm_manager.select_embedding_model", lambda: "fake-embed")
    llm_manager.clear_instances()
    yield
    llm_manager.clear_instances()


@pytest.mark.asyncio
async def test_llm_manager_chat_and_status(fake_provider):
    result = await llm_manager.chat(
        [ChatMessage.user("给我打分")], purpose="chat", user_id=1, trace_id="t-1"
    )
    assert isinstance(result, LLMResult) and result.model == "fake-model"
    assert result.total_tokens == 15
    status = llm_manager.status()
    assert status["provider"] == "fake" and "fake" in status["registered_providers"]


@pytest.mark.asyncio
async def test_llm_manager_fallback_chain(fake_provider, monkeypatch):
    monkeypatch.setattr("app.ai.core.llm_manager.select_model", lambda purpose=None: "broken-model")
    monkeypatch.setattr("app.ai.core.llm_manager.fallback_models", lambda model: ["fake-model"])
    result = await llm_manager.chat([ChatMessage.user("hi")], purpose="chat")
    assert result.model == "fake-model"


@pytest.mark.asyncio
async def test_llm_manager_embed(fake_provider):
    vectors = await llm_manager.embed(["a", "b"])
    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert await llm_manager.embed([]) == []


# ==================================================================== Agent 基类与调度
class FakeAgent:
    """Agent 替身：直接复用 BaseAgent 的 run 流程。"""

    def __init__(self) -> None:
        from app.ai.agents.base_agent import BaseAgent

        class _Agent(BaseAgent):
            name = "assistant"
            prompt_name = "resume"

            async def build_prompt(self, payload):
                return [ChatMessage.user(payload.get("question", "请回答"))]

            async def parse_output(self, raw):
                return parse_json(raw)

            async def persist(self, result, payload):
                return {"saved": True}

        self.cls = _Agent

    def build(self, **kwargs):
        return self.cls(**kwargs)


@pytest.mark.asyncio
async def test_base_agent_run_success(fake_provider):
    agent = FakeAgent().build()
    result = await agent.run({"question": "打分"})
    assert result["ok"] is True
    assert result["result"] == {"score": 90, "level": "高级"}
    assert result["persisted"] == {"saved": True}
    assert result["tokens"]["prompt"] == 10
    assert result["prompt_version"]


@pytest.mark.asyncio
async def test_base_agent_run_returns_structured_error(fake_provider, monkeypatch):
    async def broken_chat(*args, **kwargs):
        raise AIError("模型不可用")

    monkeypatch.setattr(llm_manager, "chat", broken_chat)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 0)
    agent = FakeAgent().build()
    result = await agent.run({})
    assert result["ok"] is False and result["error_code"] == 5002


@pytest.mark.asyncio
async def test_dispatch_unknown_type():
    with pytest.raises(NotFoundError):
        await dispatch("not-exist", {})


@pytest.mark.asyncio
async def test_dispatch_and_stream(fake_provider):
    agent_cls = FakeAgent().cls
    agent_registry.add("assistant", agent_cls)
    result = await dispatch("assistant", {"question": "打分"})
    assert result["ok"] is True and result["agent"] == "assistant"

    chunks = [chunk async for chunk in dispatch_stream("assistant", {"question": "打分"})]
    assert "".join(chunks)


def test_ai_runtime_status_contract(fake_provider):
    status = ai_runtime_status()
    assert set(status) >= {"llm", "skills", "agents", "milvus", "redis", "pgsql"}
    assert "matching_score" in status["skills"]


# ==================================================================== 记忆与状态
@pytest.mark.asyncio
async def test_conversation_memory_window():
    memory = ConversationMemory(conversation_id="mem-test", window=3)
    await memory.clear()
    for index in range(5):
        await memory.append({"role": "user", "content": f"第 {index} 条"})
    recent = await memory.get_recent()
    assert len(recent) == 3 and recent[-1]["content"] == "第 4 条"
    messages = await memory.get_context_messages()
    assert isinstance(messages[0], ChatMessage)
    await memory.clear()
    assert await memory.get_recent() == []


@pytest.mark.asyncio
async def test_conversation_memory_summarize_without_llm():
    memory = ConversationMemory(conversation_id="mem-summary", window=2)
    await memory.clear()
    for index in range(6):
        await memory.append({"role": "user", "content": f"消息 {index}"})
    summary = await memory.summarize_if_needed(use_llm=False)
    assert summary and "历史对话摘要" in summary
    assert len(await memory.get_recent()) <= 2
    await memory.clear()


@pytest.mark.asyncio
async def test_state_manager_serialization():
    manager = StateManager(prefix="test:state")
    await manager.save("t1", {"step": "parsed", "at": datetime(2026, 11, 3, 10, 0, 0)})
    loaded = await manager.load("t1")
    assert loaded["step"] == "parsed" and loaded["at"] == "2026-11-03T10:00:00"
    await manager.update("t1", step="scored")
    assert (await manager.load("t1"))["step"] == "scored"
    assert await manager.exists("t1") is True
    await manager.delete("t1")
    assert await manager.load("t1") is None
    assert to_jsonable({"a": {1, 2}})["a"]


# ==================================================================== 存储
@pytest.mark.asyncio
async def test_local_storage_roundtrip():
    from app.adapters.storage.base_storage import get_storage
    from app.infrastructure.storage import file_storage

    storage = get_storage("local")
    path = await storage.save(b"hello", "demo.txt", subdir="pytest")
    assert await storage.exists(path) is True
    assert await storage.read(path) == b"hello"
    assert await storage.size(path) == 5
    assert storage.url(path).startswith(settings.API_PREFIX)
    assert await storage.delete(path) is True
    assert await storage.exists(path) is False
    assert "max_size_mb" in storage.describe()
    assert file_storage.storage_root().exists()


def test_storage_blocks_path_traversal():
    from app.core.exceptions import FileError
    from app.infrastructure.storage import file_storage

    with pytest.raises(FileError):
        file_storage.safe_resolve("../../etc/passwd")


# ==================================================================== 事件总线
@pytest.mark.asyncio
async def test_event_bus_publish_and_failure_isolation():
    bus = EventBus()
    received: list[Any] = []

    bus.subscribe("demo.event", lambda payload: received.append(payload))
    bus.subscribe(
        "demo.event",
        lambda payload: (_ for _ in ()).throw(RuntimeError("处理器炸了")),
    )
    await bus.publish_await("demo.event", {"id": 1})
    assert received == [{"id": 1}]
    stats = bus.stats()
    assert stats["handled"] == 1 and stats["failed"] == 1

    bus.publish("demo.event", {"id": 2})
    await bus.drain()
    assert len(received) == 2


@pytest.mark.asyncio
async def test_event_bus_publish_non_blocking():
    bus = EventBus()
    events: list[Any] = []

    async def handler(payload):
        await asyncio.sleep(0)
        events.append(payload)

    bus.subscribe("async.event", handler)
    task = bus.publish("async.event", {"id": 3})
    assert task is not None
    await bus.drain()
    assert events == [{"id": 3}]


# ==================================================================== 匹配策略
TALENTS = [
    {"id": 1, "name": "张三", "skill_tags": ["Python", "FastAPI"], "work_years": 5, "education": "本科"},
    {"id": 2, "name": "李四", "skill_tags": ["Java"], "work_years": 1, "education": "大专"},
]
POSITION = {
    "id": 10,
    "title": "后端工程师",
    "skill_tags": ["Python", "FastAPI"],
    "requirements": {"min_work_years": 3, "min_education": "本科", "required_skills": ["Python"]},
}


@pytest.mark.asyncio
async def test_hard_filter_strategy():
    result = await HardFilterStrategy().match(POSITION, TALENTS)
    assert [item["talent_id"] for item in result] == [1]


@pytest.mark.asyncio
async def test_soft_match_strategy_sorted():
    result = await SoftMatchStrategy().match(POSITION, TALENTS)
    assert result[0]["talent_id"] == 1 and result[0]["score"] > result[-1]["score"]
    assert set(result[0]["dimensions"]) == {"skill", "experience", "education", "quality"}


@pytest.mark.asyncio
async def test_hybrid_strategy_and_factory():
    result = await HybridStrategy().match(POSITION, TALENTS)
    assert [item["talent_id"] for item in result] == [1]
    strategy = MatchStrategyFactory.create("hybrid")
    assert strategy.name == "hybrid"
    assert MatchStrategyFactory.create(None).name == "hybrid"
    assert MatchStrategyFactory.available() == ["hard", "hybrid", "soft"]
    empty = await strategy.match(POSITION, [])
    assert empty == []


# ==================================================================== LLM 适配器错误映射
class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_openai_adapter_maps_status_errors():
    from app.adapters.llm.openai_adapter import OpenAIAdapter

    adapter = OpenAIAdapter(model="gpt-4o-mini")
    with pytest.raises(AIError):
        await adapter._raise_for_status(_FakeResponse(401, "unauthorized"))
    with pytest.raises(LLMRetryableError):
        await adapter._raise_for_status(_FakeResponse(429, "rate limit"))
    with pytest.raises(AIError):
        await adapter._raise_for_status(_FakeResponse(400, "bad request"))
    await adapter._raise_for_status(_FakeResponse(200, "ok"))


@pytest.mark.asyncio
async def test_openai_adapter_chat_and_embed(monkeypatch):
    from app.adapters.llm.openai_adapter import OpenAIAdapter

    adapter = OpenAIAdapter(model="gpt-4o-mini")

    async def fake_post(self, path: str, payload: dict) -> dict:
        if path.endswith("embeddings"):
            return {"data": [{"embedding": [0.1, 0.2]}]}
        return {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            "model": "gpt-4o-mini",
        }

    monkeypatch.setattr(OpenAIAdapter, "_post", fake_post)
    result = await adapter.chat([ChatMessage.user("hi")])
    assert result.content == "hello" and result.total_tokens == 7
    generated = await adapter.generate("hi", system="你是助手")
    assert generated.content == "hello"
    assert await adapter.embed(["a"]) == [[0.1, 0.2]]

    async def bad_post(self, path: str, payload: dict) -> dict:
        return {"choices": []}

    monkeypatch.setattr(OpenAIAdapter, "_post", bad_post)
    with pytest.raises(LLMResponseError):
        await adapter.chat([ChatMessage.user("hi")])


def test_adapters_registered_and_describe():
    assert {"openai", "qwen", "ollama"} <= set(llm_registry.keys())
    from app.adapters.llm.ollama_adapter import OllamaAdapter
    from app.adapters.llm.qwen_adapter import QwenAdapter

    qwen = QwenAdapter()
    assert qwen.provider == "qwen" and qwen.base_url.startswith("https://")
    assert qwen.options["embed_model"] == "text-embedding-v3"
    ollama = OllamaAdapter()
    assert ollama.provider == "ollama" and ollama.default_base_url.startswith("http://127.0.0.1")
    assert set(qwen.describe()) >= {"provider", "model", "base_url"}


# ==================================================================== 系统接口
def test_system_endpoints_expose_ai_runtime():
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    info = client.get("/api/v1/system/info").json()
    assert info["code"] in (0, 200) or info.get("success") is not False
    data = info["data"]
    assert "ai_ready" in data and "skills" in data["ai_ready"]
    skills = client.get("/api/v1/system/skills").json()
    names = {item["name"] for item in skills["data"]}
    assert {"knowledge_search", "sql_query", "matching_score"} <= names
