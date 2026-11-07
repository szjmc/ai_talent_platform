"""
模块：tests/test_invite_link.py
职责：邀约链接生成的回退链守门（避免「未配置就直接业务失败」再次发生）。

标签：[Agent组] [P1]（邀约链接：配置缺失时的可观测降级）

为什么需要：
    生产/开发环境常常没配 INVITE_BASE_URL，此时 `POST /invites` 直接抛
    `1002 未配置 INVITE_BASE_URL / FRONTEND_URL / PUBLIC_BASE_URL，无法生成邀约链接`，
    前端只看到一句「业务异常」，整个邀约功能不可用 —— 而链接本身完全可以由
    当前请求的 Host 推导出来。

    修复方式：按 INVITE_BASE_URL -> FRONTEND_URL -> PUBLIC_BASE_URL -> 请求 Host 依次回退，
    最后一级打 WARNING 提醒生产需显式配置（反向代理下 Host 可能是内网地址）。

本用例守住：
    1. 三级配置按优先级生效，且结尾斜杠不会产生双斜杠；
    2. 全空但有请求上下文时回退到 Host，并产生 WARNING（可观测，不静默）；
    3. 全空且无请求上下文时仍然明确报错（不编造一个假域名）；
    4. 中间件确实把请求 Host 注入上下文（含 X-Forwarded-* 反代场景）。
全部离线运行，不依赖数据库。
"""

from __future__ import annotations

import logging

import pytest

from app.config.settings import settings
from app.core.exceptions import BusinessRuleError
from app.core.middleware import request_base_url, request_base_url_ctx
from app.services.invitation_service import build_invitation_link


@pytest.fixture(autouse=True)
def _clear_link_settings(monkeypatch):
    """每个用例都从「三级配置全空」开始，避免受环境变量影响。"""
    monkeypatch.setattr(settings, "INVITE_BASE_URL", "", raising=False)
    monkeypatch.setattr(settings, "FRONTEND_URL", "", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "", raising=False)
    yield


@pytest.mark.parametrize(
    ("attr", "value", "expected"),
    [
        ("INVITE_BASE_URL", "https://invite.example.com", "https://invite.example.com/invite/tk"),
        ("FRONTEND_URL", "https://hr.example.com", "https://hr.example.com/invite/tk"),
        ("PUBLIC_BASE_URL", "https://api.example.com", "https://api.example.com/invite/tk"),
    ],
)
def test_configured_base_wins_in_priority_order(monkeypatch, attr, value, expected):
    """三级配置任一存在时直接用配置，不走 Host 兜底。"""
    monkeypatch.setattr(settings, attr, value, raising=False)
    token = request_base_url_ctx.set("http://should-not-be-used")
    try:
        assert build_invitation_link("tk") == expected
    finally:
        request_base_url_ctx.reset(token)


def test_trailing_slash_does_not_produce_double_slash(monkeypatch):
    """配置里带结尾斜杠时不能拼出 `//invite`。"""
    monkeypatch.setattr(settings, "INVITE_BASE_URL", "https://hr.example.com/", raising=False)
    assert build_invitation_link("tk") == "https://hr.example.com/invite/tk"


def test_falls_back_to_request_host_with_warning(caplog):
    """全空但有请求上下文：回退到 Host，并且必须打 WARNING（不能静默）。"""
    token = request_base_url_ctx.set("http://localhost:8000")
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.invitation_service"):
            url = build_invitation_link("tk")
    finally:
        request_base_url_ctx.reset(token)

    assert url == "http://localhost:8000/invite/tk"
    assert any("INVITE_BASE_URL" in r.message or "回退" in r.message for r in caplog.records), \
        "回退到请求 Host 时必须留下 WARNING，否则生产环境无从察觉链接指向内网"


def test_priority_order_falls_through_all_levels(monkeypatch):
    """INVITE_BASE_URL 为空时依次落到 FRONTEND_URL（而不是直接跳到 Host）。"""
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://hr.example.com", raising=False)
    token = request_base_url_ctx.set("http://internal:8000")
    try:
        assert build_invitation_link("tk") == "https://hr.example.com/invite/tk"
    finally:
        request_base_url_ctx.reset(token)


def test_no_context_and_no_config_raises_clear_error():
    """全空且没有请求上下文时不能编造域名，必须给出可执行的配置指引。"""
    with pytest.raises(BusinessRuleError) as exc:
        build_invitation_link("tk")
    message = str(exc.value)
    assert "INVITE_BASE_URL" in message
    assert ".env" in message


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"host": "hr.example.com"}, "http://hr.example.com"),
        ({"host": "hr.example.com", "x-forwarded-proto": "https"},
         "https://hr.example.com"),
        ({"host": "internal:8000", "x-forwarded-host": "hr.example.com", "x-forwarded-proto": "https"},
         "https://hr.example.com"),
        ({"host": "a.com, b.com", "x-forwarded-proto": "https, http"}, "https://a.com"),
    ],
)
def test_request_base_url_honours_proxy_headers(headers, expected):
    """反向代理场景：优先 X-Forwarded-*，多值取第一个。"""

    class _URL:
        scheme = "http"

    class _Request:
        url = _URL()

        def __init__(self, hdrs):
            self.headers = hdrs

    assert request_base_url(_Request(headers)) == expected


def test_middleware_sets_base_url_context():
    """中间件必须把 Host 注入上下文，否则 Service 层拿不到兜底值。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"base": request_base_url_ctx.get()}

    from starlette.middleware.base import BaseHTTPMiddleware

    class _BaseUrlMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = request_base_url_ctx.set(request_base_url(request))
            try:
                return await call_next(request)
            finally:
                request_base_url_ctx.reset(token)

    app.add_middleware(_BaseUrlMiddleware)
    with TestClient(app) as client:
        resp = client.get("/probe", headers={"host": "hr.example.com"})
    assert resp.json()["base"] == "http://hr.example.com"
