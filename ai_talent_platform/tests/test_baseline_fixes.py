"""
模块：tests/test_baseline_fixes.py
职责：架构组基线评审 21 项问题修复的守门测试（离线，不依赖 MySQL / Redis / LLM）。

标签：[架构组] [P1]（21 项问题回归守门）

覆盖点（对应评审清单编号）：
1  分页：PageParams() 直接构造即得 int 与可用 skip / limit（历史 FieldInfo 泄漏回归）
2  SAVEPOINT：unique_write_guard 冲突只抛 DuplicateError；外层事务激活时不整体
   rollback，契约破坏（事务非激活）时回滚恢复；create / create_many / update 已接线
3  LLM 长调用前 BaseAgent 主动归还数据库连接
4  JWT：access_token 带 jti 与浮点 iat；登出吊销后失效；吊销 refresh 换新被拒；
   用户级下线（revoke_user_tokens）使存量令牌失效
5  CORS：通配符模式强制关闭 credentials（测试后自动还原）
6  生产环境 db_auto_create_tables 恒为 False（Alembic 门控）
7  请求结束清理 AIContext（TraceIdMiddleware），get_current_user 写入 set_context
9  审计链路：publish_audit 注入 trace_id 并送达订阅方；mask_audit_data 脱敏；
   AuditService.log 任何失败不抛出；register_audit_handlers 幂等；lifespan 已接线；
   Agent 调用发布 ai.call_finished
10 健康检查收敛：ping_database / ping_pgsql / check_connection 已删除，
   health._probe_database 为唯一实现
11/12 单轨契约：schemas.common 不再有 PageData / SuccessResponse / PageQuerySchema，
   core.response.ApiResponse 为唯一响应模型
13 LazyHolder：惰性创建（含 None 结果）只算一次、reset / aclose 生效
14 get_db 委托 session_scope（唯一事务块）
15 501 占位统一：挂载占位路由与 8 个骨架路由返回同一 not_implemented_response 契约
16 死配置：DATABASE_URL_SYNC / PGSQL_URL_SYNC 已从 Settings 删除（PGSQL_SERVER_URL 保留）
17 app/utils/logger.py 转发层已删除
18 reset_engine 同时释放 PgSQL 单例（源码级校验，避免引入被禁依赖）
19 requirements.txt 为合法 UTF-8 且无 BOM
20 日志敏感字段脱敏 + X-Trace-Id 白名单（非法值重新生成，防日志注入）
21 限流：RATE_LIMIT_ENABLED 门控生效；Redis 可用时走固定窗口计数（多 worker 共享）
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from app.ai.agents.base_agent import BaseAgent
from app.ai.context import clear_context, get_context, set_context
from app.config.logging_config import redact_sensitive, trace_id_ctx
from app.config.settings import Settings, settings
from app.core.exceptions import AuthError, DuplicateError
from app.core.permissions import visible_organization_ids
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.token_revocation import (
    is_token_revoked,
    reset_revocation_store,
    revoke_user_tokens,
)
from app.core.middleware import RateLimitMiddleware
from app.infrastructure.messaging.event_bus import (
    EVENT_AI_CALL_FINISHED,
    EVENT_AUDIT_LOG,
    event_bus,
)
from app.models.user import User
from app.patterns.singleton import LazyHolder
from app.repositories.base import unique_write_guard
from app.services.audit_service import (
    AuditService,
    mask_audit_data,
    publish_audit,
    register_audit_handlers,
    unregister_audit_handlers,
)
from app.services.auth_service import AuthService

ROOT = Path(__file__).resolve().parent.parent


# ==================================================================== 1 分页
def test_page_params_direct_construction_yields_usable_ints():
    """直接 PageParams() 构造（历史 bug：默认值是 Query FieldInfo，.skip 抛 TypeError）。"""
    from app.core.pagination import PageParams, build_page_result

    p = PageParams()
    assert isinstance(p.page, int) and p.page == settings.DEFAULT_PAGE
    assert isinstance(p.page_size, int) and p.page_size == settings.DEFAULT_PAGE_SIZE
    assert isinstance(p.skip, int) and p.skip == 0
    assert p.limit == settings.DEFAULT_PAGE_SIZE

    p3 = PageParams(page=3, page_size=50)
    assert p3.skip == 100 and p3.limit == 50

    # 非法值兜底：0 页码回退默认；超大 page_size 截断到 MAX_PAGE_SIZE
    assert PageParams(page=0).page == settings.DEFAULT_PAGE
    assert PageParams(page_size=99999).page_size == settings.MAX_PAGE_SIZE

    result = build_page_result([], 0, p3)
    assert result.total == 0 and result.page == 3 and result.page_size == 50


# ==================================================================== 2 SAVEPOINT
class _PassThroughSavepoint:
    """模拟 begin_nested() 的成功路径（SAVEPOINT 建立/释放，无异常）。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _make_guard_db() -> MagicMock:
    db = MagicMock()
    db.begin_nested.return_value = _PassThroughSavepoint()
    db.get_transaction.return_value = MagicMock(is_active=True)
    db.rollback = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_unique_write_guard_success_does_not_rollback():
    """守卫内正常完成：不触发任何回滚，外层事务继续。"""
    db = _make_guard_db()
    async with unique_write_guard(db, "写入失败"):
        pass
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_unique_write_guard_conflict_keeps_outer_transaction():
    """唯一冲突：只抛 DuplicateError，不整体 rollback（前序写入不丢失的契约）。"""
    db = _make_guard_db()
    with pytest.raises(DuplicateError, match="手机号"):
        async with unique_write_guard(db, "手机号已存在"):
            raise IntegrityError(
                "INSERT INTO talents", {}, Exception("UNIQUE constraint failed: mobile")
            )
    db.rollback.assert_not_called()  # 外层事务激活 -> SAVEPOINT 语义，禁止整体回滚
    db.begin_nested.assert_called_once()


@pytest.mark.asyncio
async def test_unique_write_guard_broken_contract_rolls_back_to_recover():
    """契约破坏（进入时有未 flush 脏写）：事务非激活时回滚恢复会话，仍抛 DuplicateError。"""
    db = _make_guard_db()
    db.get_transaction.return_value = MagicMock(is_active=False)
    with pytest.raises(DuplicateError, match="冲突"):
        async with unique_write_guard(db, "写入冲突"):
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))
    db.rollback.assert_awaited_once()


def test_repository_write_methods_enter_unique_guard():
    """create / create_many / update 三个写方法都在 unique_write_guard 内 flush。"""
    src = (ROOT / "app/repositories/base.py").read_text(encoding="utf-8")
    for method in ("create", "create_many", "update"):
        match = re.search(
            rf"async def {method}\(.*?(?=\n    async def |\nclass |\Z)", src, re.S
        )
        assert match, f"未找到 BaseRepository.{method}"
        assert "async with unique_write_guard(" in match.group(0), (
            f"BaseRepository.{method} 未在 unique_write_guard 内执行写入"
        )


# ==================================================================== 3 LLM 前归还连接
@pytest.mark.asyncio
async def test_base_agent_releases_db_connection_before_llm(monkeypatch):
    """build_prompt 之后必须调用 release_connection 归还连接（连接池红线）。"""
    import app.infrastructure.database.session as session_mod

    released: list[object] = []

    async def fake_release(session):
        released.append(session)
        return True

    monkeypatch.setattr(session_mod, "release_connection", fake_release)

    class _Agent(BaseAgent):
        name = "probe"

        async def build_prompt(self, payload):
            return []

        async def parse_output(self, raw):
            return {}

    fake_db = object()
    agent = _Agent(db=fake_db)
    await agent._release_db_before_llm()
    assert released == [fake_db]

    # db 为空（纯脚本调用）时静默跳过
    await _Agent(db=None)._release_db_before_llm()
    assert released == [fake_db]


# ==================================================================== 4 JWT 吊销
@pytest.mark.asyncio
async def test_access_token_carries_jti_and_logout_revokes():
    """access_token 含 jti + 浮点 iat；logout 吊销后 is_token_revoked=True。"""
    reset_revocation_store()
    try:
        token = create_access_token(7, username="alice", roles=["hr"])
        payload = decode_token(token)
        assert payload.get("jti"), "access_token 必须携带 jti（否则无法定点吊销）"
        assert isinstance(payload.get("iat"), float)
        assert await is_token_revoked(payload) is False

        user = User(id=7, username="alice")
        service = AuthService(db=None)
        await service.logout(user, access_token=token)
        assert await is_token_revoked(payload) is True, "登出后旧 access_token 必须立即失效"
    finally:
        reset_revocation_store()


@pytest.mark.asyncio
async def test_refresh_token_revoked_by_logout_is_rejected():
    """登出时吊销的 refresh_token 换新必须被拒（且不触达数据库）。"""
    reset_revocation_store()
    try:
        refresh = create_refresh_token(7)
        user = User(id=7, username="alice")
        service = AuthService(db=None)
        await service.logout(user, refresh_token=refresh)

        with pytest.raises(AuthError, match="重新登录"):
            await service.refresh(refresh)
    finally:
        reset_revocation_store()


@pytest.mark.asyncio
async def test_user_level_kick_revokes_existing_tokens():
    """revoke_user_tokens（改密 / 踢人）让此前签发的存量令牌全部失效。"""
    reset_revocation_store()
    try:
        token = create_access_token(7, username="alice")
        payload = decode_token(token)
        assert await is_token_revoked(payload) is False
        await asyncio.sleep(0.01)
        await revoke_user_tokens(7)
        assert await is_token_revoked(payload) is True, "用户级下线后存量令牌必须失效"
    finally:
        reset_revocation_store()


# ==================================================================== 5/6 配置门控
def test_cors_credentials_disabled_for_wildcard(monkeypatch):
    """通配符来源 + credentials 是浏览器拒绝组合：* 模式必须关闭凭证。"""
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*")
    assert settings.cors_allow_credentials is False
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://a.example.com")
    assert settings.cors_allow_credentials is True


def test_prod_disables_auto_create_tables(monkeypatch):
    """生产环境恒不 create_all（表结构走 Alembic）；dev/test 受开关控制。"""
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "DB_AUTO_CREATE_TABLES", True)
    assert settings.db_auto_create_tables is False

    monkeypatch.setattr(settings, "APP_ENV", "dev")
    assert settings.db_auto_create_tables is True
    monkeypatch.setattr(settings, "DB_AUTO_CREATE_TABLES", False)
    assert settings.db_auto_create_tables is False


# ==================================================================== 7 AI 上下文
@pytest.mark.asyncio
async def test_request_clears_ai_context_after_response(client, monkeypatch):
    """请求结束 TraceIdMiddleware 必须清理 AIContext，防止身份串到下一个请求。"""
    import app.core.middleware as middleware_mod

    calls: list[str] = []
    original = middleware_mod.clear_context

    def spy() -> None:
        calls.append("cleared")
        original()

    monkeypatch.setattr(middleware_mod, "clear_context", spy)
    clear_context()
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert calls, "TraceIdMiddleware 未调用 clear_context（AI 上下文可能泄漏）"
    finally:
        clear_context()


def test_get_current_user_writes_ai_context():
    """get_current_user 必须写入 set_context（否则 Skills 权限校验 403、数据过滤失效）。"""
    src = (ROOT / "app/core/dependencies.py").read_text(encoding="utf-8")
    match = re.search(r"async def get_current_user\(.*?(?=\nasync def |\n# ---)", src, re.S)
    assert match, "未找到 get_current_user"
    assert "set_context(" in match.group(0), "get_current_user 未接线 AIContext.set_context"


def test_routers_pass_current_user_for_data_scope():
    """issue 8 接线：用户 / 人才路由把 current_user 传入 Service 做行级数据范围过滤。"""
    for name in ("user_router.py", "talent_router.py"):
        src = (ROOT / "app/routers" / name).read_text(encoding="utf-8")
        assert "current_user=user" in src, f"{name} 未把 current_user 传给 Service"


def test_visible_organization_ids_matrix():
    """issue 8 行级范围纯函数：ALL / 部门子树 / 仅本人 三态。"""
    assert visible_organization_ids(["hr"], 1, [2, 3]) is None  # 管理角色 -> 全公司
    assert visible_organization_ids(["manager"], 1, [2, 3]) == {1, 2, 3}  # 本部门含子树
    assert visible_organization_ids(["employee"], 1, None) == set()  # SELF -> created_by 过滤
    assert visible_organization_ids(["manager"], None, None) == set()  # 无部门 -> 空集


# ==================================================================== 9 审计链路
@pytest.mark.asyncio
async def test_publish_audit_delivers_with_trace_id():
    """publish_audit 发布的事件携带发布时刻的 trace_id，订阅方可收到。"""
    received: list[dict] = []

    async def probe(payload):
        received.append(payload)

    event_bus.subscribe(EVENT_AUDIT_LOG, probe)
    token = trace_id_ctx.set("trace-baseline-1")
    try:
        publish_audit(
            action="login",
            module="auth",
            user_id=1,
            username="alice",
            status="success",
        )
        await event_bus.drain(timeout=2.0)
    finally:
        trace_id_ctx.reset(token)
        event_bus.unsubscribe(EVENT_AUDIT_LOG, probe)

    assert received, "audit.log 订阅方未收到事件（event_bus 未接通？）"
    assert received[0]["action"] == "login"
    assert received[0]["trace_id"] == "trace-baseline-1"


def test_mask_audit_data_masks_sensitive_fields():
    """审计落库前必须脱敏密码 / 令牌 / 密钥 / 证件号。"""
    masked = mask_audit_data(
        {
            "password": "hunter2",
            "token": "eyJhbGciOi.secret.sig",
            "api_key": "sk-123",
            "id_card": "310101",
            "username": "alice",
        }
    )
    assert masked is not None
    assert masked["password"] == "***"
    assert masked["token"] == "***"
    assert masked["api_key"] == "***"
    assert masked["id_card"] == "***"
    assert masked["username"] == "alice"  # 非敏感字段保持可读


@pytest.mark.asyncio
async def test_audit_log_never_raises_on_failure():
    """AuditService.log 任何写入失败只记日志：审计绝不能打断主业务。"""
    service = AuditService(db=None)  # db=None -> 内部必然失败，验证兜底
    result = await service.log(action="login", module="auth", user_id=1, username="alice")
    assert result is None  # 失败静默返回，不抛出


@pytest.mark.asyncio
async def test_register_audit_handlers_idempotent():
    """register_audit_handlers 可重复调用（lifespan + 测试都会调），订阅不重复。"""
    unregister_audit_handlers()  # 归零，消除其他入口影响
    try:
        register_audit_handlers()
        register_audit_handlers()
        assert len(event_bus.handlers(EVENT_AUDIT_LOG)) == 1
        assert len(event_bus.handlers(EVENT_AI_CALL_FINISHED)) == 1
    finally:
        unregister_audit_handlers()
        assert len(event_bus.handlers(EVENT_AUDIT_LOG)) == 0


def test_lifespan_wires_audit_handlers_and_drain():
    """lifespan 启动注册审计订阅、停机 drain 等待落库（静态接线校验）。"""
    src = (ROOT / "app/core/lifespan.py").read_text(encoding="utf-8")
    assert "register_audit_handlers()" in src, "lifespan 未注册审计订阅"
    assert "event_bus.drain" in src, "lifespan 停机未 drain 审计事件"


@pytest.mark.asyncio
async def test_base_agent_publishes_ai_call_finished():
    """Agent AI 调用完成必须发布 ai.call_finished（AuditService 订阅落库）。"""
    received: list[dict] = []

    async def probe(payload):
        received.append(payload)

    class _Agent(BaseAgent):
        name = "probe-agent"

        async def build_prompt(self, payload):
            return []

        async def parse_output(self, raw):
            return {}

    event_bus.subscribe(EVENT_AI_CALL_FINISHED, probe)
    try:
        agent = _Agent()
        agent._publish_ai_call(
            status="success",
            elapsed_ms=12.5,
            model="deepseek-chat",
            tokens={"prompt": 10, "completion": 20},
        )
        await event_bus.drain(timeout=2.0)
    finally:
        event_bus.unsubscribe(EVENT_AI_CALL_FINISHED, probe)

    assert received, "ai.call_finished 未送达订阅方"
    payload = received[0]
    assert payload["agent"] == "probe-agent"
    assert payload["model"] == "deepseek-chat"
    assert payload["prompt_tokens"] == 10 and payload["completion_tokens"] == 20
    assert payload["elapsed_ms"] == 12.5


# ==================================================================== 10-18 冗余收敛
def test_legacy_ping_helpers_removed():
    """issue 10：三套 SELECT 1 收敛到 health._probe_database，ping_* / check_connection 删除。"""
    import app.infrastructure.database.health as health_mod
    import app.infrastructure.database.mysql as mysql_mod
    import app.infrastructure.database.postgres as postgres_mod
    import app.infrastructure.database.session as session_mod

    assert not hasattr(mysql_mod, "ping_database")
    assert not hasattr(postgres_mod, "ping_pgsql")
    assert not hasattr(session_mod, "check_connection")
    assert hasattr(health_mod, "_probe_database")


def test_single_pagination_and_response_contract():
    """issue 11/12：分页与响应模型单轨（schemas.common 旧模型删除，ApiResponse 唯一）。"""
    import app.core.response as response_mod
    import app.schemas.common as schemas_common

    for legacy in ("PageData", "PageQuerySchema", "SuccessResponse"):
        assert not hasattr(schemas_common, legacy), f"schemas.common 仍残留 {legacy}"
    for current in ("ApiResponse", "PageResult", "success", "error", "api_response"):
        assert hasattr(response_mod, current), f"core.response 缺少 {current}"


def test_lazy_holder_caches_none_and_resets():
    """issue 13：LazyHolder 创建（含 None 结果）只发生一次，reset 后重建。"""
    calls: list[int] = []

    def factory():
        calls.append(1)
        return None

    holder = LazyHolder("probe", factory)
    assert holder.get() is None and holder.created is True
    assert holder.get() is None
    assert len(calls) == 1, "factory 返回 None 也必须只计算一次"

    holder.reset()
    assert holder.created is False and holder.peek() is None
    holder.get()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_lazy_holder_aclose_invokes_closer():
    """aclose 调用 closer 释放并清空，下次 get 重新创建。"""
    closed: list[str] = []

    async def closer(value: str) -> None:
        closed.append(value)

    holder = LazyHolder("probe-engine", lambda: "engine", closer)
    assert holder.get() == "engine"
    await holder.aclose()
    assert closed == ["engine"]
    assert holder.peek() is None and holder.created is False


def test_get_db_delegates_to_session_scope():
    """issue 14：get_db 与 session_scope 不再维护两份事务块（get_db 委托 session_scope）。"""
    src = (ROOT / "app/infrastructure/database/session.py").read_text(encoding="utf-8")
    match = re.search(r"async def get_db\(\).*?(?=\nasync def |\Z)", src, re.S)
    assert match, "未找到 get_db"
    assert "session_scope" in match.group(0), "get_db 未委托 session_scope"


# ==================================================================== 15 501 占位统一
_SKELETON_ROUTERS = (
    ("positions", "app.routers.position_router"),
    ("resumes", "app.routers.resume_router"),
    ("assessments", "app.routers.assessment_router"),
    ("trainings", "app.routers.training_router"),
    ("matchings", "app.routers.matching_router"),
    ("knowledge", "app.routers.knowledge_router"),
    ("agents", "app.routers.agent_router"),
)


@pytest.mark.asyncio
async def test_mounted_stub_returns_501_contract(client):
    """挂载的占位路由（router.py placeholder）：未登录也返回 501 + owner 提示。"""
    resp = await client.get("/api/v1/positions")
    assert resp.status_code == 501
    body = resp.json()
    assert body["code"] == 501
    assert body["data"]["module"] == "岗位管理"
    assert body["data"]["owner"] == "Agent组"
    assert body["data"]["hint"]


@pytest.mark.asyncio
async def test_skeleton_routers_share_stub_contract(client):
    """issue 15：8 个骨架路由占位端点与挂载占位同契约（501、结构一致、无权限拦截）。"""
    import importlib

    skeleton_app = FastAPI()
    prefixes: dict[str, str] = {}
    for prefix, module_name in _SKELETON_ROUTERS:
        router = importlib.import_module(module_name).router
        skeleton_app.include_router(router)
        prefixes[router.prefix] = prefix

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=skeleton_app),
        base_url="http://skeleton.test",
    ) as skel_client:
        for router_prefix, prefix in prefixes.items():
            skel_resp = await skel_client.get(router_prefix)
            assert skel_resp.status_code == 501, f"{router_prefix} 骨架占位未返回 501"
            assert skel_resp.json()["code"] == 501

            mount_resp = await client.get(f"/api/v1/{prefix}")
            assert mount_resp.status_code == 501, f"/api/v1/{prefix} 挂载占位未返回 501"

            skel_body, mount_body = skel_resp.json(), mount_resp.json()
            # 同一模块两处占位的契约必须逐字段一致（防漂移）
            assert skel_body["code"] == mount_body["code"]
            assert skel_body["message"] == mount_body["message"]
            assert skel_body["data"] == mount_body["data"]


# ==================================================================== 16/17/18 死配置与删除
def test_dead_config_and_logger_layer_removed():
    """issue 16/17：DATABASE_URL_SYNC / PGSQL_URL_SYNC 已删除；utils/logger.py 不存在。"""
    assert "DATABASE_URL_SYNC" not in Settings.model_fields
    assert "PGSQL_URL_SYNC" not in Settings.model_fields
    assert hasattr(settings, "PGSQL_SERVER_URL")  # 派生属性，有实际使用方，保留
    assert not (ROOT / "app/utils/logger.py").exists(), "utils/logger.py 应已删除"
    assert (ROOT / "app/utils/file_utils.py").exists()  # 其余工具保留


def test_reset_engine_releases_pgsql_too():
    """issue 18：reset_engine 必须同时释放 PgSQL（源码级，历史只重置 MySQL 的回归）。"""
    src = (ROOT / "app/infrastructure/database/session.py").read_text(encoding="utf-8")
    match = re.search(r"async def reset_engine\(\).*?(?=\nasync def |\Z)", src, re.S)
    assert match, "未找到 reset_engine"
    body = match.group(0)
    assert "dispose_pg_engine" in body, "reset_engine 未释放 PgSQL 单例"
    assert "_engine_holder" in body and "_session_holder" in body


# ==================================================================== 19 requirements 编码
def test_requirements_is_utf8_without_bom():
    """issue 19：requirements.txt 必须是无 BOM 的 UTF-8（pip/CI 解析可靠）。"""
    raw = (ROOT / "requirements.txt").read_bytes()
    assert not raw.startswith(b"\xff\xfe"), "requirements.txt 是 UTF-16 LE BOM"
    assert not raw.startswith(b"\xfe\xff"), "requirements.txt 是 UTF-16 BE BOM"
    assert not raw.startswith(b"\xef\xbb\xbf"), "requirements.txt 带 UTF-8 BOM"
    text = raw.decode("utf-8")  # 严格解码失败即编码不合法
    assert "fastapi" in text.lower()


# ==================================================================== 20 日志卫生
def test_redact_sensitive_masks_credentials():
    """日志脱敏：password / token / Bearer / JWT 不得以明文进入日志。"""
    line = (
        "login fail password=hunter2 token=abc123secret "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl "
        "api_key=sk-live-9527"
    )
    redacted = redact_sensitive(line)
    assert "hunter2" not in redacted
    assert "abc123secret" not in redacted
    assert "sk-live-9527" not in redacted
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted
    assert "***" in redacted


@pytest.mark.asyncio
async def test_trace_id_header_whitelisted(client):
    """X-Trace-Id 只接受 [A-Za-z0-9_-]{1,64}：非法值重新生成，防止日志注入。"""
    resp = await client.get("/health", headers={"X-Trace-Id": "bad value with space!"})
    echoed = resp.headers["X-Trace-Id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", echoed), f"非法入参未被拦截: {echoed!r}"
    assert echoed != "bad value with space!"

    resp2 = await client.get("/health", headers={"X-Trace-Id": "ok_trace-123"})
    assert resp2.headers["X-Trace-Id"] == "ok_trace-123"  # 合法值原样回传


# ==================================================================== 21 限流
def _patch_redis_counter(monkeypatch, counter: dict[str, int]) -> list[str]:
    """把 Redis 固定窗口计数替换为按 key 独立计数的桩（faithful 模拟 incr），返回捕获的 key。"""
    import app.infrastructure.cache.redis as redis_mod

    keys: list[str] = []

    def fake_get_redis():
        return object()  # 非 None -> 走 Redis 分支（而非进程内兜底）

    async def fake_incr(key: str, ttl: int) -> int:
        keys.append(key)
        counter[key] = counter.get(key, 0) + 1
        return counter[key]

    monkeypatch.setattr(redis_mod, "get_redis", fake_get_redis)
    monkeypatch.setattr(redis_mod, "incr_with_ttl", fake_incr)
    return keys


@pytest.mark.asyncio
async def test_rate_limit_enabled_gate(client, monkeypatch):
    """RATE_LIMIT_ENABLED=False 完全跳过计数；开启后非豁免路径走 Redis 固定窗口。"""
    counter: dict[str, int] = {}
    keys = _patch_redis_counter(monkeypatch, counter)

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    resp = await client.get("/api/v1/positions")
    assert resp.status_code == 501
    assert keys == [], "限流关闭时不应触碰计数器"

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    resp2 = await client.get("/api/v1/positions")
    assert resp2.status_code == 501
    assert keys, "限流开启后必须走 Redis 固定窗口计数"
    assert keys[0].startswith("ratelimit:"), f"key 格式应为 ratelimit:{{ip}}:{{window}}: {keys[0]}"


@pytest.mark.asyncio
async def test_rate_limit_redis_window_denies_over_limit(monkeypatch):
    """Redis 固定窗口：窗口内超过 limit 拒绝（多 worker 共享同一 key 即共享配额）。"""
    counter: dict[str, int] = {}
    keys = _patch_redis_counter(monkeypatch, counter)
    mw = RateLimitMiddleware(app=MagicMock(), limit=2, window_seconds=60)

    assert await mw._allow("10.0.0.1") is True
    assert await mw._allow("10.0.0.1") is True
    assert await mw._allow("10.0.0.1") is False  # 第 3 次超过 limit=2
    assert await mw._allow("10.0.0.2") is True  # 另一客户端配额独立
    assert all(k.startswith("ratelimit:10.0.0.1:") or k.startswith("ratelimit:10.0.0.2:") for k in keys)
