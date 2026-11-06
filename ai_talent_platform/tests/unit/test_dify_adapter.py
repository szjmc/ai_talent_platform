"""MATCH-006 Dify 适配器单元测试（httpx.MockTransport，不依赖真实 Dify 服务）。

必须说清楚的一点：
    这些用例证明的是「适配器自身的行为正确」（请求构造、错误分类、重试策略、输出解析），
    **不代表与真实 Dify 服务联调通过**。真实联调需 Dify 组给出契约（T-1）后再做。

为什么用 asyncio.run 而不用 pytest-asyncio：
    项目 requirements.txt 未包含 pytest-asyncio（属开发依赖缺口，已记入报告），
    用标准库 asyncio.run 跑协程即可，不让测试依赖额外的插件。

标签：[Dify组] [P1]（Dify 适配器单元用例，MockTransport 离线）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest

from app.adapters.dify.workflow_adapter import (
    DifyWorkflowAdapter,
    DifyAuthError,
    DifyNotConfiguredError,
    DifyParseError,
    DifyRequestError,
    DifyTimeoutError,
    DifyUnavailableError,
    DifyWorkflowFailedError,
)

# ------------------------------------------------------------------ 工具

Handler = Callable[[httpx.Request], httpx.Response]


def _adapter(handler: Handler, **kwargs: Any) -> DifyWorkflowAdapter:
    """构造一个指向假 Dify 的适配器（base_delay=0 让重试不等待）。"""
    return DifyWorkflowAdapter(
        base_url="http://dify.test/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        base_delay=0.0,
        **kwargs,
    )


def _ok(outputs: dict[str, Any], status: str = "succeeded") -> dict[str, Any]:
    """构造 Dify blocking 模式的成功响应体。"""
    return {
        "data": {
            "id": "run-123",
            "status": status,
            "outputs": outputs,
            "error": None,
            "elapsed_time": 1.2,
            "total_tokens": 88,
        }
    }


def _run(adapter: DifyWorkflowAdapter, coro_factory: Callable[[], Any]) -> Any:
    """在独立事件循环里跑一次调用，并确保关闭客户端。"""

    async def main() -> Any:
        try:
            return await coro_factory()
        finally:
            await adapter.aclose()

    return asyncio.run(main())


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


# ------------------------------------------------------------------ 1. 正常请求


def test_run_workflow_success():
    """正常返回：请求体、鉴权头、输出解析都要对。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ok({"result": '{"job_title": "Python 工程师", "skills": ["Python"]}'}))

    adapter = _adapter(handler)
    result = _run(adapter, lambda: adapter.run_job_profile_extraction("招 Python 工程师，3 年经验"))

    assert result["workflow_key"] == "job_profile_extraction"
    assert result["status"] == "succeeded"
    assert result["run_id"] == "run-123"
    # outputs.result 是 JSON 字符串，必须被解析成结构体（上层直接用 parsed）
    assert result["parsed"] == {"job_title": "Python 工程师", "skills": ["Python"]}
    # 原始 outputs 保留，便于审计
    assert "result" in result["outputs"]

    request = captured[0]
    assert request.url.path == "/v1/workflows/run"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = _body(request)
    assert body["response_mode"] == "blocking"
    assert body["user"] == "ai-talent-platform"
    assert body["inputs"] == {"jd_text": "招 Python 工程师，3 年经验"}


def test_outputs_without_result_field():
    """多字段输出形态：没有 result 时把整个 outputs 当结果。"""
    adapter = _adapter(lambda request: httpx.Response(200, json=_ok({"job_title": "x", "skills": ["Go"]})))
    result = _run(adapter, lambda: adapter.run_workflow("match_explanation", {"evidence_pack": "{}"}))
    assert result["parsed"] == {"job_title": "x", "skills": ["Go"]}


def test_match_explanation_serializes_pack():
    """证据包对象要序列化成 JSON 字符串再送 Dify（不直接塞对象）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ok({"result": "{}"}))

    from app.schemas.matching import MatchEvidencePack
    from app.schemas.position import JobProfileParsed

    pack = MatchEvidencePack(
        position_id=7,
        talent_id=12,
        job_profile_summary=JobProfileParsed(job_title="Python 工程师"),
    )

    adapter = _adapter(handler)
    _run(adapter, lambda: adapter.run_match_explanation(pack))

    raw = _body(captured[0])["inputs"]["evidence_pack"]
    assert isinstance(raw, str)
    assert json.loads(raw)["talent_id"] == 12


# ------------------------------------------------------------------ 2. 鉴权类错误


def test_401_raises_auth_error_without_retry():
    """401 属于「换 Key 才能解决」，不能重试。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"message": "invalid api key"})

    adapter = _adapter(handler)
    with pytest.raises(DifyAuthError) as exc:
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 1
    assert exc.value.code == 5014


def test_403_raises_auth_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    adapter = _adapter(handler)
    with pytest.raises(DifyAuthError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 1


# ------------------------------------------------------------------ 3. 服务端错误与重试


def test_500_retries_then_raises_unavailable():
    """5xx 可重试：默认 3 次后失败，错误要能识别为「服务不可用」。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    adapter = _adapter(handler)
    with pytest.raises(DifyUnavailableError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 3


def test_500_then_success_recovers():
    """重试确实能救回来后端抖动。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json=_ok({"result": '{"ok": true}'}))

    adapter = _adapter(handler)
    result = _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert result["parsed"] == {"ok": True}
    assert calls["n"] == 2


def test_timeout_raises_timeout_error():
    """超时要能被单独识别（上层可据此放宽超时或换通道）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("read timeout")

    adapter = _adapter(handler, max_attempts=2)
    with pytest.raises(DifyTimeoutError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 2


def test_connection_error_raises_unavailable():
    """连接失败（服务没起）归入「不可用」，而不是崩一个未捕获异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _adapter(handler, max_attempts=2)
    with pytest.raises(DifyUnavailableError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))


def test_422_not_retried():
    """参数类 4xx 重试无意义，必须立即失败。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, text="invalid inputs")

    adapter = _adapter(handler)
    with pytest.raises(DifyRequestError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 1


def test_404_hints_base_url():
    """404 多半是 base_url / 路径配错，错误信息要指出来。"""
    adapter = _adapter(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(DifyRequestError) as exc:
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert "DIFY_BASE_URL" in str(exc.value)


# ------------------------------------------------------------------ 4. 内容解析失败


def test_invalid_json_body_raises_parse_error():
    """响应体不是 JSON（如网关返回 HTML）-> DifyParseError，不重试。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    adapter = _adapter(handler)
    with pytest.raises(DifyParseError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 1


def test_non_json_result_raises_parse_error():
    """outputs.result 不是 JSON（prompt 没约束住）-> DifyParseError，让上层降级。"""
    adapter = _adapter(lambda request: httpx.Response(200, json=_ok({"result": "这不是 JSON，只是一段话"})))
    with pytest.raises(DifyParseError):
        _run(adapter, lambda: adapter.run_match_explanation({"position_id": 1}))


def test_missing_data_field_raises_parse_error():
    """响应缺少 data（部署形态不同）时明确报错，而不是猜。"""
    adapter = _adapter(lambda request: httpx.Response(200, json={"outputs": {}}))
    with pytest.raises(DifyParseError) as exc:
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert "data" in str(exc.value)


# ------------------------------------------------------------------ 5. 工作流内部失败


def test_workflow_status_failed():
    """HTTP 200 但 data.status=failed：属于工作流自身问题，重试无意义。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"data": {"id": "run-1", "status": "failed", "outputs": {}, "error": "prompt 变量未找到"}},
        )

    adapter = _adapter(handler)
    with pytest.raises(DifyWorkflowFailedError) as exc:
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert "prompt 变量未找到" in str(exc.value)
    assert calls["n"] == 1


# ------------------------------------------------------------------ 6. 前置校验（不该发请求）


def test_missing_api_key_raises_not_configured():
    """没配 Key 直接给可识别异常，上层立即降级，不要白发请求。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok({"result": "{}"}))

    adapter = DifyWorkflowAdapter(
        base_url="http://dify.test/v1",
        api_key="",
        transport=httpx.MockTransport(handler),
        base_delay=0.0,
    )
    assert adapter.configured is False
    with pytest.raises(DifyNotConfiguredError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("jd"))
    assert calls["n"] == 0


def test_missing_required_input():
    """缺必填入参时本地拒绝（不把半截参数发给 Dify）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok({"result": "{}"}))

    adapter = _adapter(handler)
    with pytest.raises(DifyRequestError) as exc:
        _run(adapter, lambda: adapter.run_workflow("job_profile_extraction", {"wrong": "x"}))
    assert "jd_text" in str(exc.value)
    assert calls["n"] == 0


def test_empty_jd_rejected_locally():
    adapter = _adapter(lambda request: httpx.Response(200, json=_ok({"result": "{}"})))
    with pytest.raises(DifyRequestError):
        _run(adapter, lambda: adapter.run_job_profile_extraction("   "))


def test_unknown_workflow_key():
    """未注册的工作流名要给出可用列表，便于排查拼写错误。"""
    adapter = _adapter(lambda request: httpx.Response(200, json=_ok({"result": "{}"})))
    with pytest.raises(DifyRequestError) as exc:
        _run(adapter, lambda: adapter.run_workflow("job_profile_extraction_v2", {}))
    assert "job_profile_extraction" in str(exc.value.data)


# ------------------------------------------------------------------ 7. 配置与生命周期


def test_workflows_listed_and_customizable():
    """工作流名可配置（Dify 组改契约时不用改代码）。"""
    from app.adapters.dify.workflow_adapter import DifyWorkflowSpec

    adapter = _adapter(
        lambda request: httpx.Response(200, json=_ok({"result": "{}"})),
        workflows={"custom": DifyWorkflowSpec(key="custom", required_inputs=("text",))},
    )
    assert adapter.workflows == ["custom"]


def test_configured_flag():
    adapter = _adapter(lambda request: httpx.Response(200, json=_ok({"result": "{}"})))
    assert adapter.configured is True


def test_injected_client_not_closed_by_adapter():
    """注入的客户端由调用方管理，适配器不得擅自关闭（避免影响其他调用方）。"""
    client = httpx.AsyncClient(
        base_url="http://dify.test/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_ok({"result": "{}"}))),
    )
    adapter = DifyWorkflowAdapter(api_key="k", client=client)

    asyncio.run(adapter.run_job_profile_extraction("jd"))
    asyncio.run(adapter.aclose())
    assert client.is_closed is False
    asyncio.run(client.aclose())
