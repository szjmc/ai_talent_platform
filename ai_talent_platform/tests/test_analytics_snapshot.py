"""Vanna结果快照专项测试：方案A权限、Redis降级、契约与两种报告一致性。

标签：[Vanna组] [P0]（查询结果复用与权限隔离）

复用公共Redis JSON封装，客户端使用内存测试替身；模型与分析查询使用假适配器。
所有异步场景用asyncio.run执行，不依赖额外异步测试插件或真实服务。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.core.exceptions import AIError, NotFoundError, PermissionDeniedError
from app.infrastructure.cache import redis as cache
from app.schemas.analytics import ReportRequest
from app.services import analytics_service, analytics_snapshot, report_store
from app.services.analytics_service import AnalyticsService
from tests.test_report_generation import FakeAdapter, FakeUser, upload_dir



class _HolderStub:
    """适配架构组 LazyHolder 单例的测试替身（旧测试直接 patch 模块级 _client）。"""

    def __init__(self, client):
        self._client = client

    def get(self):
        return self._client

    def peek(self):
        return self._client

    async def aclose(self):
        self._client = None


def _set_cache_client(monkeypatch, client):
    """把 Redis 客户端注入 holder（等价于历史上对模块级 _client 的 patch）。"""
    monkeypatch.setattr(cache, "_client_holder", _HolderStub(client))

class FakeRedis:
    """只替代Redis传输，不替代项目JSON序列化与快照业务逻辑。"""

    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.writes = 0
        self.fail_read = False
        self.fail_write = False

    async def set(self, key, value, *, ex):
        if self.fail_write:
            raise ConnectionError("redis://private-credential")
        self.values[key] = value
        self.ttls[key] = ex
        self.writes += 1
        return True

    async def get(self, key):
        if self.fail_read:
            raise ConnectionError("redis://private-credential")
        return self.values.get(key)


@pytest.fixture
def redis_cache(monkeypatch):
    client = FakeRedis()
    _set_cache_client(monkeypatch, client)
    monkeypatch.setattr(settings, "REDIS_DEFAULT_TTL", 300)
    return client


def user(**changes):
    """默认使用普通admin，避免超管标志掩盖角色/权限变化。"""
    values = dict(
        id=7, username="tester", real_name="测试员", is_superuser=False,
        organization_id=3, role_codes=["admin"],
    )
    values.update(changes)
    return SimpleNamespace(**values)


def scope(current_user):
    return {
        "user_id": current_user.id, "organization_id": current_user.organization_id,
        "roles": AnalyticsService._roles(current_user),
    }


def query(current_user=None, adapter=None):
    return asyncio.run(AnalyticsService(adapter=adapter or FakeAdapter()).nl2sql_ask(
        "各部门人数", current_user or user(),
    ))


def test_snapshot_uses_public_redis_and_existing_ttl(redis_cache):
    result = query()
    assert len(result.query_id) == 32
    key = "analytics:snapshot:" + result.query_id
    record = json.loads(redis_cache.values[key])
    assert redis_cache.ttls[key] == 300
    assert record["principal"]["user_id"] == 7
    assert record["principal"]["data_scope"] == "all"
    assert record["result"]["rows"] == result.rows
    assert datetime.fromisoformat(record["expires_at"]) == result.expires_at
    assert (result.expires_at - datetime.fromisoformat(record["created_at"])).total_seconds() == 300
    loaded = asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user())))
    assert loaded.model_dump(mode="json") == result.model_dump(mode="json")
    assert redis_cache.writes == 1  # 读取不续期。


@pytest.mark.parametrize("current_user", [user(), FakeUser()])
def test_admin_and_superuser_can_save_snapshot(current_user, redis_cache):
    assert query(current_user).query_id is not None


@pytest.mark.parametrize("roles", [["hr"], ["manager"]])
def test_non_export_or_department_user_keeps_query_without_snapshot(roles, redis_cache):
    result = query(user(role_codes=roles))
    assert result.rows
    assert result.query_id is None and result.expires_at is None
    assert any("当前权限暂不支持" in warning for warning in result.warnings)
    assert redis_cache.writes == 0


def test_user_without_query_permission_never_calls_model(redis_cache):
    adapter = FakeAdapter()
    with pytest.raises(PermissionDeniedError):
        query(user(role_codes=["employee"]), adapter)
    assert adapter.calls == []
    assert redis_cache.writes == 0


def test_no_redis_does_not_return_fake_snapshot_id(monkeypatch):
    _set_cache_client(monkeypatch, None)
    result = query()
    assert result.rows
    assert result.query_id is None and result.expires_at is None
    assert any("Redis不可用" in warning for warning in result.warnings)


def test_redis_write_failure_keeps_data_and_hides_credentials(redis_cache, caplog):
    redis_cache.fail_write = True
    result = query()
    assert result.rows
    assert result.query_id is None and result.expires_at is None
    assert any("快照保存失败" in warning for warning in result.warnings)
    assert "private-credential" not in caplog.text


def test_snapshot_canonical_json_is_immutable(redis_cache):
    original = asyncio.run(FakeAdapter().ask("测试序列化"))
    original.rows = [[date(2026, 11, 4), Decimal("1.25")]]
    result = asyncio.run(analytics_snapshot.save_snapshot(original, scope(user())))
    assert result.rows == original.model_dump(mode="json")["rows"]
    result.rows[0][1] = "changed"
    loaded = asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user())))
    assert loaded.rows == [["2026-11-04", "1.25"]]
    assert original.rows == [[date(2026, 11, 4), Decimal("1.25")]]


@pytest.mark.parametrize("changes", [
    {"id": 8}, {"organization_id": 4}, {"role_codes": ["admin", "hr"]},
    {"role_codes": ["hr"]}, {"role_codes": ["manager"]},
])
def test_identity_or_permission_changes_deny_snapshot(changes, redis_cache):
    result = query()
    with pytest.raises(PermissionDeniedError):
        asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user(**changes))))


def test_analysis_policy_changes_invalidate_snapshot(redis_cache, monkeypatch):
    result = query()
    monkeypatch.setattr(settings, "NL2SQL_MAX_ROWS", 100)
    with pytest.raises(PermissionDeniedError, match="权限已变化"):
        asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user())))


@pytest.mark.parametrize("mutation", ["expired", "bad_date", "bad_result", "version", "missing", "bad_json"])
def test_expired_missing_or_corrupt_snapshot_is_explicit_error(mutation, redis_cache):
    result = query()
    key = "analytics:snapshot:" + result.query_id
    record = json.loads(redis_cache.values[key])
    if mutation == "expired":
        record["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif mutation == "bad_date":
        record["expires_at"] = "invalid"
    elif mutation == "bad_result":
        record["result"] = {}
    elif mutation == "version":
        record["version"] = 999
    redis_cache.values[key] = json.dumps(record)
    if mutation == "missing":
        del redis_cache.values[key]
    elif mutation == "bad_json":
        redis_cache.values[key] = "not json"
    with pytest.raises(NotFoundError):
        asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user())))


def test_redis_read_failure_does_not_expose_details(redis_cache, caplog):
    result = query()
    redis_cache.fail_read = True
    with pytest.raises(AIError) as exc:
        asyncio.run(analytics_snapshot.load_snapshot(result.query_id, scope(user())))
    assert exc.value.code == 5002
    assert "private-credential" not in str(exc.value)
    assert "private-credential" not in caplog.text


def test_snapshot_export_reuses_result_in_pdf_and_word(redis_cache, upload_dir, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    adapter = FakeAdapter()
    service = AnalyticsService(adapter=adapter)
    result = asyncio.run(service.nl2sql_ask("各部门人数", user()))
    captured = []
    original_renderer = analytics_service.render_report

    def render(payload, fmt):
        captured.append((payload.sql, payload.columns, payload.rows,
                         payload.chart.model_dump(mode="json"), payload.insight.model_dump(mode="json")))
        return original_renderer(payload, fmt)

    async def forbidden(*args, **kwargs):
        raise AssertionError("快照导出不应再次调用模型或分析查询")

    monkeypatch.setattr(adapter, "ask", forbidden)
    monkeypatch.setattr(analytics_service, "render_report", render)
    for fmt in ("pdf", "docx"):
        item = asyncio.run(service.create_report(ReportRequest(query_id=result.query_id, format=fmt), user()))
        assert report_store.load_meta(item.report_id)["source_query_id"] == result.query_id
        assert item.size > 0
        path, _ = asyncio.run(service.get_report_file(item.report_id, user()))
        assert path.exists()
    assert captured[0] == captured[1]
    assert captured[0] == (result.sql, result.columns, result.rows,
                           result.chart.model_dump(mode="json"), result.insight_detail.model_dump(mode="json"))
    assert len(adapter.calls) == 1
    assert redis_cache.writes == 1


def test_snapshot_expiration_does_not_delete_generated_report(redis_cache, upload_dir, monkeypatch):
    """临时结果与正式报告分别管理，Redis过期不应导致已生成文件消失。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())
    result = asyncio.run(service.nl2sql_ask("各部门人数", user()))
    item = asyncio.run(service.create_report(ReportRequest(query_id=result.query_id), user()))
    redis_cache.values.clear()
    path, _ = asyncio.run(service.get_report_file(item.report_id, user()))
    assert path.exists()
    with pytest.raises(NotFoundError):
        asyncio.run(service.create_report(ReportRequest(query_id=result.query_id), user()))


def test_snapshot_without_insight_does_not_generate_new_explanation(redis_cache, upload_dir, monkeypatch):
    """页面没生成解读时，导出也不新增模型调用来补写不同内容。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))

    class NoInsightAdapter(FakeAdapter):
        async def ask(self, *args, **kwargs):
            result = await super().ask(*args, **kwargs)
            result.insight = None
            result.insight_detail = None
            return result

    service = AnalyticsService(adapter=NoInsightAdapter())
    result = asyncio.run(service.nl2sql_ask("各部门人数", user(), with_insight=False))
    item = asyncio.run(service.create_report(ReportRequest(query_id=result.query_id), user()))
    assert item.size > 0
    assert len(service.adapter.calls) == 1


@pytest.mark.parametrize("ttl", [0, -1])
def test_invalid_ttl_keeps_query_without_snapshot(ttl, redis_cache, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_DEFAULT_TTL", ttl)
    result = query()
    assert result.rows and result.query_id is None
    assert redis_cache.writes == 0


@pytest.mark.parametrize("phase", ["write", "read"])
def test_stalled_redis_is_bounded_by_existing_timeout(phase, redis_cache, monkeypatch):
    """连接不返回时同样降级/报业务错误，且等待中的协程被取消。"""
    saved = query() if phase == "read" else None
    cancelled = []

    async def stalled(*args, **kwargs):
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(settings, "NL2SQL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(redis_cache, "set" if phase == "write" else "get", stalled)
    if phase == "write":
        result = query()
        assert result.rows and result.query_id is None
        assert any("快照保存失败" in warning for warning in result.warnings)
    else:
        with pytest.raises(AIError, match="快照服务不可用"):
            asyncio.run(analytics_snapshot.load_snapshot(saved.query_id, scope(user())))
    assert cancelled == [True]


def test_failed_snapshot_export_never_requeries(redis_cache, monkeypatch):
    service = AnalyticsService(adapter=FakeAdapter())

    async def forbidden(*args, **kwargs):
        raise AssertionError("不得静默重新问数")

    monkeypatch.setattr(service, "nl2sql_ask", forbidden)
    with pytest.raises(NotFoundError):
        asyncio.run(service.create_report(ReportRequest(query_id="a" * 32), user()))
    _set_cache_client(monkeypatch, None)
    with pytest.raises(AIError):
        asyncio.run(service.create_report(ReportRequest(query_id="a" * 32), user()))


def test_service_cannot_skip_export_permission():
    with pytest.raises(PermissionDeniedError, match="analytics:export"):
        asyncio.run(AnalyticsService(adapter=FakeAdapter()).create_report(
            ReportRequest(question="各部门人数"), user(role_codes=["hr"]),
        ))


@pytest.mark.parametrize("payload", [
    {}, {"question": "问题", "query_id": "a" * 32}, {"query_id": "invalid"},
    {"query_id": "a" * 32, "max_rows": 200}, {"query_id": "a" * 32, "chart_type": "auto"},
    {"query_id": "a" * 32, "rows": []}, {"query_id": "a" * 32, "sql": "SELECT 1"},
    {"question": "   "},
])
def test_report_source_contract_rejects_mixed_or_forged_payload(payload):
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(payload)


def test_snapshot_query_and_export_api_contract(redis_cache, upload_dir, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.core.application import create_application
    from app.core.dependencies import get_current_user
    from app.routers.analytics_router import get_service

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())
    app = create_application()
    app.dependency_overrides[get_current_user] = lambda: user()
    app.dependency_overrides[get_service] = lambda: service

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"{settings.API_PREFIX}/analytics/nl2sql", json={"question": "各部门人数"})
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["query_id"] and data["expires_at"]
            for fmt in ("pdf", "docx"):
                exported = await client.post(f"{settings.API_PREFIX}/analytics/report", json={
                    "query_id": data["query_id"], "format": fmt,
                })
                assert exported.status_code == 200
                assert exported.json()["data"]["url"]
            bad = await client.post(f"{settings.API_PREFIX}/analytics/report", json={
                "query_id": data["query_id"], "question": "伪造问题",
            })
            assert bad.status_code == 422
            assert len(service.adapter.calls) == 1

    asyncio.run(scenario())
