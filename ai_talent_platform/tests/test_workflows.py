"""Agent 组工作流测试：graph_builder 状态图 + supervisor 总路由（LangGraph）。

标签：[Agent组] [P1]

全部用例离线运行：不依赖 MySQL / Redis / 真实大模型（LLM 统一 mock）。
验收命令：pytest tests/test_workflows.py tests/test_agents.py -q
"""

from __future__ import annotations

import pytest

from app.adapters.llm.base_llm import LLMResult
from app.ai.agent import AGENT_TYPES
from app.ai.core.llm_manager import llm_manager
from app.ai.memory.state_manager import state_manager
from app.ai.workflows.graph_builder import (
    END,
    LANGGRAPH_AVAILABLE,
    build_graph,
    route_by_status,
    run_workflow,
)
from app.ai.workflows.supervisor_workflow import decide_route, run_supervisor
from app.core.exceptions import AIError, NotFoundError
from app.patterns.registry import agent_registry


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def fake_llm(monkeypatch):
    """把 llm_manager.chat / stream 替换为离线桩。"""

    async def fake_chat(messages, purpose: str = "chat", **kwargs):
        return LLMResult(content="模拟回答：已收到", model="fake-model", prompt_tokens=10, completion_tokens=5)

    async def fake_stream(messages, purpose: str = "chat", **kwargs):
        for piece in ("模拟", "流式", "回答"):
            yield piece

    monkeypatch.setattr(llm_manager, "chat", fake_chat)
    monkeypatch.setattr(llm_manager, "stream", fake_stream)
    return fake_chat


@pytest.fixture
def agent_registry_guard():
    """注册表快照隔离：测试内注册的 Agent 结束后还原，避免污染其它用例。"""
    original = dict(agent_registry._items)
    yield agent_registry
    agent_registry._items.clear()
    agent_registry._items.update(original)


@pytest.fixture(autouse=True)
def _clean_context():
    from app.ai.context import clear_context

    yield
    clear_context()


# ---------------------------------------------------------------- graph_builder
def test_langgraph_available_in_test_env():
    """验收环境已装 langgraph（requirements.txt 固定 0.2.67）。"""
    assert LANGGRAPH_AVAILABLE is True


def test_build_graph_validates_entry_point():
    with pytest.raises(ValueError):
        build_graph({"a": (lambda s: None)}, [], "missing")
    with pytest.raises(ValueError):
        build_graph({}, [], "a")


async def test_linear_graph_runs_to_done():
    async def node_a(state):
        return {"data": {**(state.get("data") or {}), "a": 1}}

    async def node_b(state):
        return {"data": {**(state.get("data") or {}), "b": 2}, "status": "done"}

    graph = build_graph({"a": node_a, "b": node_b}, [("a", "b")], "a")
    out = await run_workflow(graph, {})
    assert out["status"] == "done"
    assert out["data"] == {"a": 1, "b": 2}
    assert out["errors"] == []


async def test_conditional_edges_route_by_status():
    async def entry(state):
        return {"status": "failed" if (state.get("payload") or {}).get("boom") else "done"}

    async def ok_node(state):
        return {"data": {"path": "ok"}}

    async def failed_node(state):
        return {"data": {"path": "failed"}}

    graph = build_graph(
        {"entry": entry, "ok": ok_node, "failed": failed_node},
        [("ok", END), ("failed", END)],
        "entry",
        [("entry", route_by_status, {"done": "ok", "failed": "failed"})],
    )
    good = await run_workflow(graph, {})
    assert good["data"]["path"] == "ok"
    bad = await run_workflow(graph, {"payload": {"boom": True}})
    assert bad["data"]["path"] == "failed"
    assert route_by_status({"status": "need_human"}) == "need_human"


async def test_run_workflow_folds_node_exception_into_state():
    async def boom(state):
        raise RuntimeError("节点炸了")

    graph = build_graph({"boom": boom}, [], "boom")
    out = await run_workflow(graph, {})  # 不裸抛
    assert out["status"] == "failed"
    assert any("节点炸了" in err for err in out["errors"])


async def test_breakpoint_save_and_resume():
    """每步断点写入 state_manager；resume=True 从断点恢复且不重复执行。"""
    calls = {"n": 0}

    async def one(state):
        calls["n"] += 1
        return {"data": {"step": 1}}

    async def two(state):
        calls["n"] += 1
        return {"data": {"step": 2}, "status": "done"}

    graph = build_graph(
        {"one": one, "two": two}, [("one", "two")], "one", use_checkpoint=True
    )
    thread_id = "test-breakpoint-resume"
    out1 = await run_workflow(graph, {}, thread_id=thread_id)
    assert calls["n"] == 2 and out1["status"] == "done"

    saved = await state_manager.load(thread_id)
    assert saved is not None and saved["status"] == "done"

    out2 = await run_workflow(graph, {}, thread_id=thread_id, resume=True)
    assert calls["n"] == 2, "resume 不应重跑节点"
    assert out2["status"] == "done"


async def test_fallback_executor_parity():
    """langgraph 缺失时的内置执行器：同图同结果（离线降级契约）。"""

    async def entry(state):
        return {"status": "failed" if (state.get("payload") or {}).get("bad") else "done"}

    async def ok_node(state):
        return {"data": {"path": "ok"}}

    async def failed_node(state):
        return {"data": {"path": "failed"}}

    def make(force: bool):
        return build_graph(
            {"entry": entry, "ok": ok_node, "failed": failed_node},
            [("ok", END), ("failed", END)],
            "entry",
            [("entry", route_by_status, {"done": "ok", "failed": "failed"})],
            force_fallback=force,
        )

    lang_out = await run_workflow(make(False), {})
    fallback_out = await run_workflow(make(True), {})
    assert lang_out["data"] == fallback_out["data"] == {"path": "ok"}

    bad_lang = await run_workflow(make(False), {"payload": {"bad": True}})
    bad_fallback = await run_workflow(make(True), {"payload": {"bad": True}})
    assert bad_lang["data"] == bad_fallback["data"] == {"path": "failed"}


async def test_fallback_max_steps_guard():
    """自环图触发 AGENT_MAX_STEPS 防死循环保护。"""

    async def loop(state):
        return {"retries": int(state.get("retries") or 0) + 1}

    graph = build_graph({"loop": loop}, [("loop", "loop")], "loop", force_fallback=True)
    out = await run_workflow(graph, {})
    assert out["status"] == "failed"
    assert any("最大步数" in err for err in out["errors"])


# ---------------------------------------------------------------- supervisor 路由
async def test_route_explicit_agent_type():
    final = await run_supervisor({"agent_type": "resume", "message": "你好"}, mode="route")
    assert final["agent"] == "resume"
    assert final["route_reason"] == "explicit"


async def test_route_by_keyword():
    final = await run_supervisor(
        {"agent_type": "assistant", "message": "帮我解析这份简历"}, mode="route"
    )
    assert final["agent"] == "resume"
    assert final["route_reason"].startswith("keyword:")

    final2 = await run_supervisor(
        {"agent_type": "assistant", "message": "为算法专家岗位推荐候选人匹配"}, mode="route"
    )
    assert final2["agent"] == "matching"


async def test_route_default_assistant():
    final = await run_supervisor(
        {"agent_type": "assistant", "message": "查询高级工程师的晋升条件"}, mode="route"
    )
    assert final["agent"] == "assistant"
    assert final["route_reason"] == "default"


def test_decide_route_pure_rules():
    assert decide_route({"agent_type": "talent"})[0] == "talent"
    assert decide_route({"agent_type": "bogus", "message": "解析简历"})[0] == "resume"
    assert decide_route({"message": "生成培训方案"}) == ("training", "keyword:培训")
    assert decide_route({})[0] == "assistant"


async def test_unknown_agent_type_in_service_validation():
    from app.services.agent_service import AgentService

    with pytest.raises(NotFoundError):
        AgentService._validate_agent_type("bogus")


# ---------------------------------------------------------------- supervisor 执行
async def test_full_mode_falls_back_to_generic_llm(fake_llm):
    """Agent 未注册 + 对话请求 → 降级通用应答（degraded=True）。"""
    final = await run_supervisor(
        {"agent_type": "training", "message": "给我一个培训方案"}, mode="full"
    )
    assert final["status"] == "done"
    assert final["degraded"] is True
    assert "模拟回答" in final["answer"]
    assert final["usage"]["prompt_tokens"] == 10


async def test_full_mode_task_without_agent_fails_structured():
    """结构化任务（无 message）且 Agent 未注册 → 业务失败 + error_code=3001。"""
    final = await run_supervisor({"agent_type": "resume"}, mode="full")
    assert final["status"] == "failed"
    last = (final.get("answers") or [])[-1]["result"]
    assert last["ok"] is False
    assert last["error_code"] == NotFoundError("x").code


async def test_full_mode_dispatches_registered_agent(agent_registry_guard):
    """已注册 Agent：走 dispatch 唯一入口，结果聚合进终态。"""

    class FakeAgent:
        def __init__(self, db=None, user=None, context=None):
            self.db, self.user, self.context = db, user, context

        async def run(self, payload):
            return {
                "ok": True,
                "agent": "talent",
                "result": {"answer": "人才画像结果"},
                "tokens": {"prompt": 3, "completion": 4, "model": "fake"},
            }

    agent_registry_guard.add("talent", FakeAgent)
    final = await run_supervisor(
        {"agent_type": "talent", "message": "盘点团队人才"}, mode="full"
    )
    assert final["status"] == "done"
    assert final["answer"] == "人才画像结果"
    assert final["route_reason"] == "explicit"
    assert final["usage"]["prompt_tokens"] == 3
    assert final["usage"]["model"] == "fake"
    assert final["degraded"] is False


async def test_supervisor_retries_transient_error_once(agent_registry_guard):
    """AIError（瞬时）→ 调度层重试一次后成功。"""
    attempts = {"n": 0}

    class FlakyAgent:
        def __init__(self, db=None, user=None, context=None):
            pass

        async def run(self, payload):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise AIError("LLM 超时")
            return {"ok": True, "result": {"answer": "第二次成功"}, "tokens": {}}

    agent_registry_guard.add("assessment", FlakyAgent)
    final = await run_supervisor({"agent_type": "assessment", "message": "测评审议"}, mode="full")
    assert final["status"] == "done"
    assert attempts["n"] == 2
    assert final["retries"] == 1
    assert final["answer"] == "第二次成功"


async def test_supervisor_fails_after_retry_budget(agent_registry_guard):
    """持续超时 → 重试耗尽后 status=failed（不无限重试）。"""
    attempts = {"n": 0}

    class DeadAgent:
        def __init__(self, db=None, user=None, context=None):
            pass

        async def run(self, payload):
            attempts["n"] += 1
            raise AIError("持续超时")

    agent_registry_guard.add("matching", DeadAgent)
    final = await run_supervisor({"agent_type": "matching", "message": "人岗匹配"}, mode="full")
    assert final["status"] == "failed"
    assert attempts["n"] == 2
    assert any("持续超时" in err for err in final["errors"])
