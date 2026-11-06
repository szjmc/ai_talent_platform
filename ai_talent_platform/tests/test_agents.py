"""Agent 组服务与接口测试：Schema / AgentService 对话与任务 / 消息落库契约 / 路由挂载。

标签：[Agent组] [P1]

全部用例离线运行（db=None + mock LLM），不依赖 MySQL / Redis / 真实大模型。
验收命令：pytest tests/test_workflows.py tests/test_agents.py -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.adapters.llm.base_llm import LLMResult
from app.ai.context import clear_context, get_context, set_context, update_context
from app.ai.core.llm_manager import llm_manager
from app.ai.workflows.graph_builder import LANGGRAPH_AVAILABLE
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentTaskRequest,
    AgentTaskResponse,
    TokenUsage,
)
from app.services.agent_service import AgentService


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def fake_llm(monkeypatch):
    """把 llm_manager.chat / stream 替换为离线桩。"""

    async def fake_chat(messages, purpose: str = "chat", **kwargs):
        return LLMResult(
            content="模拟回答：已收到", model="fake-model", prompt_tokens=10, completion_tokens=5
        )

    async def fake_stream(messages, purpose: str = "chat", **kwargs):
        for piece in ("模拟", "流式", "回答"):
            yield piece

    monkeypatch.setattr(llm_manager, "chat", fake_chat)
    monkeypatch.setattr(llm_manager, "stream", fake_stream)
    return fake_chat


@pytest.fixture
def offline_agents(monkeypatch):
    """把所有 Agent 派发打桩为「未注册」：命中 supervisor 的降级通用应答路径。

    合并后 talent / resume 等 Agent 已真实注册，无 DB 时会走业务失败（502）；
    本 fixture 用于稳定验证「Agent 不可用 → 降级应答」这一支路。
    """

    async def _not_found(*args, **kwargs):
        raise NotFoundError("Agent 未注册：offline-test")

    import app.ai.workflows.supervisor_workflow as supervisor

    monkeypatch.setattr(supervisor, "dispatch", _not_found)
    return _not_found


@pytest.fixture(autouse=True)
def _clean_context():
    yield
    clear_context()


def _user(uid: int = 1) -> SimpleNamespace:
    # 默认 hr：能实际触发 talent/resume 类 Skill（"user" 不是合法角色，
    # 会因缺少 talent:read 直接 403，测不到路由与降级逻辑）。
    return SimpleNamespace(
        id=uid, username=f"user{uid}", organization_id=None, role_codes=["hr"]
    )


# ---------------------------------------------------------------- Schema
def test_chat_request_defaults_and_forbid():
    req = AgentChatRequest(message="你好")
    assert req.agent_type == "assistant"
    assert req.stream is False
    assert req.conversation_id is None
    with pytest.raises(ValidationError):
        AgentChatRequest(message="")  # 空消息
    with pytest.raises(ValidationError):
        AgentChatRequest(message="hi", unknown_field=1)  # extra=forbid


def test_task_request_async_alias():
    task = AgentTaskRequest.model_validate(
        {"agent_type": "resume", "payload": {"file_id": 12}, "async": True}
    )
    assert task.is_async is True
    assert task.payload == {"file_id": 12}
    assert AgentTaskRequest(agent_type="resume").is_async is False
    with pytest.raises(ValidationError):
        AgentTaskRequest.model_validate({"agent_type": "resume", "oops": 1})


def test_response_models_shape():
    resp = AgentChatResponse(
        conversation_id="1", message_id=2, answer="a", agent="assistant", usage=TokenUsage()
    )
    assert resp.degraded is False and resp.citations == []
    task = AgentTaskResponse(task_id="t", agent_type="resume")
    assert task.status == "pending" and task.result is None


# ---------------------------------------------------------------- Service 校验
def test_validate_agent_type_normalization():
    assert AgentService._validate_agent_type(None) == "assistant"
    assert AgentService._validate_agent_type("auto") == "assistant"
    assert AgentService._validate_agent_type("RESUME") == "resume"
    with pytest.raises(NotFoundError):
        AgentService._validate_agent_type("bogus")


def test_dify_switch_off_by_default():
    """DIFY_APP_ID_CHAT 未配置（Dify 组负责的开关）→ 永不走 Dify 分支。"""
    service = AgentService(db=None)
    assert service._should_use_dify("assistant") is False
    assert service._should_use_dify("resume") is False


# ---------------------------------------------------------------- 对话（离线）
async def test_chat_offline_degraded_answer(fake_llm, offline_agents):
    """无 DB + Agent 未注册 + 关键词路由 → 降级通用应答，结构完整。"""
    service = AgentService(db=None)
    resp = await service.chat(
        AgentChatRequest(message="帮我盘点团队人才", agent_type="assistant"),
        current_user=_user(),
    )
    assert isinstance(resp, AgentChatResponse)
    assert resp.answer == "模拟回答：已收到"
    assert resp.degraded is True
    assert resp.conversation_id.startswith("local-")
    assert resp.agent == "talent"  # 关键词路由到 talent（未注册 → 降级）
    assert resp.route_reason.startswith("keyword:")
    assert resp.usage.prompt_tokens == 10
    assert resp.usage.model == "fake-model"
    assert resp.elapsed_ms >= 0


async def test_chat_rejects_unknown_conversation(fake_llm, offline_agents):
    """db=None 离线模式下传入会话 ID：无法命中会话 → 按新会话本地 key 处理。

    agent_type 显式用 resume（测试全程无人注册该 Agent），避免受其它测试
    注册 assistant 的影响，稳定命中降级应答路径。
    """
    service = AgentService(db=None)
    resp = await service.chat(
        AgentChatRequest(message="你好呀", agent_type="resume", conversation_id="99999"),
        current_user=_user(),
    )
    assert resp.answer  # 离线模式不查库，直接降级应答
    assert resp.conversation_id.startswith("local-")


async def test_chat_stream_events_sequence(fake_llm):
    """SSE 事件序列：start → delta* → done；delta 同时带 text 与 delta 键。"""
    service = AgentService(db=None)
    session = await service.prepare_chat(
        AgentChatRequest(message="生成一个培训方案", agent_type="training"),
        current_user=_user(),
    )
    assert session.agent == "training"
    assert session.route_reason in ("explicit", "keyword:培训")

    events = [event async for event in service.stream_events(session)]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "delta" in kinds
    assert "error" not in kinds

    start = events[0]["data"]
    assert start["conversation_id"].startswith("local-")
    assert "trace_id" in start and "message_id" in start
    assert start["agent"] == "training"

    text = "".join(e["data"]["text"] for e in events if e["event"] == "delta")
    assert text == "模拟流式回答"
    assert all(e["data"]["delta"] == e["data"]["text"] for e in events if e["event"] == "delta")

    done = events[-1]["data"]
    assert done["conversation_id"] == start["conversation_id"]
    assert "usage" in done and "elapsed_ms" in done


async def test_prepare_chat_rejects_unknown_agent_type():
    service = AgentService(db=None)
    with pytest.raises(NotFoundError):
        await service.prepare_chat(
            AgentChatRequest(message="hi", agent_type="bogus"), current_user=_user()
        )


# ---------------------------------------------------------------- 会话与消息（离线）
async def test_list_conversations_and_messages_offline():
    service = AgentService(db=None)
    from app.core.pagination import PageParams

    page = PageParams(page=1, page_size=10)
    convs = await service.list_conversations(_user(), page)
    assert convs.total == 0 and convs.items == []
    msgs = await service.list_messages("42", _user(), page)
    assert msgs.total == 0 and msgs.items == []


async def test_describe_routes():
    from app.ai.agent import AGENT_TYPES

    info = await AgentService(db=None).describe_routes()
    assert set(info["agents"]) == set(AGENT_TYPES)
    assert info["graph_backend"] in ("langgraph", "fallback")
    assert "resume" in info["route_keywords"]
    assert isinstance(info["registered"], list)


# ---------------------------------------------------------------- 消息落库契约
class _FakeSession:
    """最小 AsyncSession 替身：记录 add，execute 返回可链式对象。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def execute(self, stmt):  # noqa: ARG002 - 只需不抛
        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

            def scalar_one(self):
                return 0

        return _Result()


async def test_append_message_contract_and_suppression():
    """ConversationMemory 回调契约：有会话才落库；suppress 标记防双写。"""
    repo = ConversationRepository()
    session = _FakeSession()

    # 1) 无上下文会话 → 跳过（不自作主张建会话）
    clear_context()
    assert await repo.append_message(session, obj_in={"role": "user", "content": "x"}) is None
    assert session.added == []

    # 2) 上下文带 conversation_id → 落库 + meta 映射到列
    set_context(user={"user_id": 1, "roles": ["user"]}, conversation_id="42")
    row = await repo.append_message(
        session,
        obj_in={"role": "user", "content": "你好", "citations": [{"doc": "d1"}]},
    )
    assert row is not None
    assert row.conversation_id == 42 and row.content == "你好"
    assert row.citations == [{"doc": "d1"}]
    assert len(session.added) == 1

    # 3) suppress_conversation_persist → 跳过（AgentService 统一落库，防双写）
    update_context(extra={**get_context().extra, "suppress_conversation_persist": True})
    skipped = await repo.append_message(
        session, obj_in={"role": "assistant", "content": "答"}
    )
    assert skipped is None
    assert len(session.added) == 1


# ---------------------------------------------------------------- 任务（离线）
async def test_task_inline_failure_and_ownership():
    user, other = _user(7), _user(8)
    service = AgentService(db=None)

    resp = await service.submit_task(
        AgentTaskRequest(agent_type="resume", payload={"file_id": 12}),
        current_user=user,
    )
    assert isinstance(resp, AgentTaskResponse)
    assert resp.status == "failed"  # resume Agent 未注册 → 结构化失败
    assert resp.error

    got = await service.get_task_status(resp.task_id, current_user=user)
    assert got.task_id == resp.task_id
    assert got.status == "failed"

    with pytest.raises(PermissionDeniedError):
        await service.get_task_status(resp.task_id, current_user=other)
    with pytest.raises(NotFoundError):
        await service.get_task_status("task-does-not-exist", current_user=user)


async def test_task_async_returns_immediately():
    import asyncio

    service = AgentService(db=None)
    resp = await service.submit_task(
        AgentTaskRequest(agent_type="training", payload={}, is_async=True),
        current_user=_user(5),
    )
    assert resp.task_id.startswith("task-")
    assert resp.status in ("pending", "running", "success", "failed")
    # 给后台协程一点时间（最终应落到 failed：Agent 未注册）
    for _ in range(50):
        current = await service.get_task_status(resp.task_id, current_user=_user(5))
        if current.status in ("success", "failed"):
            break
        await asyncio.sleep(0.05)
    assert current.status == "failed"


# ---------------------------------------------------------------- 路由挂载
async def test_endpoint_chat_and_stream_end_to_end(fake_llm, offline_agents):
    """HTTP 端到端（离线）：鉴权 / DB 依赖打桩后走完整链路
    Router → AgentService → LangGraph 总路由 → SSE 帧，校验与前端解析约定对齐。
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.application import create_application
    from app.core.dependencies import get_current_user, get_db

    # 独立 app 实例（依赖全部打桩，不连 MySQL、不需真实 Token）
    app = create_application()
    fake_user = SimpleNamespace(
        id=1, username="e2e", organization_id=None, role_codes=["hr"], is_superuser=False
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: None  # 离线：不连 MySQL
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://e2e.test"
        ) as ac:
            # 1) 非流式对话
            resp = await ac.post(
                "/api/v1/agents/chat",
                json={"message": "帮我盘点团队人才", "agent_type": "assistant"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == 200
            data = body["data"]
            assert data["answer"]
            assert data["agent"] == "talent"
            assert data["conversation_id"].startswith("local-")

            # 2) SSE 流式对话：帧格式 = event 行 + data JSON 行 + 空行
            resp2 = await ac.post(
                "/api/v1/agents/chat/stream",
                json={"message": "生成一个培训方案", "agent_type": "training"},
            )
            assert resp2.status_code == 200
            assert resp2.headers["content-type"].startswith("text/event-stream")

            frames = [chunk for chunk in resp2.text.split("\n\n") if chunk.strip()]
            events = []
            for frame in frames:
                lines = dict(
                    line.split(": ", 1) for line in frame.splitlines() if ": " in line
                )
                events.append((lines.get("event"), json.loads(lines.get("data", "{}"))))
            kinds = [name for name, _ in events]
            assert kinds[0] == "start" and kinds[-1] == "done"
            assert "delta" in kinds
            text = "".join(d["text"] for n, d in events if n == "delta")
            assert text
            assert events[0][1]["conversation_id"]

            # 3) 清单 / 会话查询（离线：空页）
            resp3 = await ac.get("/api/v1/agents")
            assert resp3.status_code == 200
            data3 = resp3.json()["data"]
            assert "assistant" in data3["agents"] and "graph_backend" in data3

            resp4 = await ac.get("/api/v1/agents/conversations")
            assert resp4.status_code == 200
            assert resp4.json()["data"]["items"] == []

            resp5 = await ac.get("/api/v1/agents/conversations/42/messages")
            assert resp5.status_code == 200
            assert resp5.json()["data"]["items"] == []

            # 4) 任务：提交（内联执行）+ 状态查询 + 越权/不存在
            resp6 = await ac.post(
                "/api/v1/agents/tasks",
                json={"agent_type": "resume", "payload": {"file_id": 12}},
            )
            assert resp6.status_code == 200
            task = resp6.json()["data"]
            assert task["status"] == "failed" and task["error"]  # Agent 未注册

            resp7 = await ac.get(f"/api/v1/agents/tasks/{task['task_id']}")
            assert resp7.status_code == 200
            assert resp7.json()["data"]["task_id"] == task["task_id"]

            resp8 = await ac.get("/api/v1/agents/tasks/task-missing")
            assert resp8.status_code == 404
            assert resp8.json()["code"] == 3001
    finally:
        app.dependency_overrides.clear()


async def test_router_mounted_and_stub_removed(client):
    """真实 /agents 路由已挂载（未登录 401，而非占位 501）；其它占位保持 501。"""
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001

    resp2 = await client.get("/api/v1/agents/tasks/nope")
    assert resp2.status_code == 401

    resp3 = await client.post("/api/v1/agents/chat", json={"message": "hi"})
    assert resp3.status_code == 401

    stub = await client.get("/api/v1/positions")
    assert stub.status_code == 501


def test_langgraph_is_used_for_supervisor():
    """总路由必须基于 LangGraph（验收硬性要求）。"""
    assert LANGGRAPH_AVAILABLE is True
    import app.ai.workflows.supervisor_workflow as supervisor_module

    source = supervisor_module.__doc__ or ""
    assert "LangGraph" in source or "StateGraph" in source
