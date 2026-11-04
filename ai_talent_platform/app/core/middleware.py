"""
模块：app/core/middleware.py
职责：全局中间件（请求追踪、访问日志、耗时统计、跨域、安全响应头、限流）。

标签：[架构组] [P1]（trace_id / 访问日志 / 限流 / 安全响应头）

实现思路：
1. TraceIdMiddleware：为每个请求生成 trace_id（优先复用请求头 X-Trace-Id），
   写入 contextvar 供日志使用，并回写到响应头；
2. AccessLogMiddleware：记录 method / path / 状态码 / 耗时 / 客户端 IP，慢请求 warning；
3. CORSMiddleware：来源从 settings.CORS_ORIGINS 读取；
4. SecurityHeadersMiddleware：添加 X-Content-Type-Options 等安全头；
5. RateLimitMiddleware（可选，默认开启、进程内令牌桶）：防止接口被刷。
   生产环境建议换成 Redis 实现（infrastructure/cache）以支持多实例；
6. register_middlewares(app) 统一注册。注意：中间件注册顺序与执行顺序相反，
   因此 trace_id 必须最后注册，才能最先执行。

注意：不要在这里做鉴权（鉴权在 core/dependencies），也不要在这里写业务逻辑。
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config.logging_config import get_logger, trace_id_ctx
from app.config.settings import settings

logger = get_logger("app.access")

TRACE_HEADER = "X-Trace-Id"
PROCESS_HEADER = "X-Process-Time-Ms"
SLOW_REQUEST_MS = 1000


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 trace_id。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get(TRACE_HEADER) or uuid.uuid4().hex[:16]
        token = trace_id_ctx.set(trace_id)
        request.state.trace_id = trace_id
        try:
            response = await call_next(request)
        finally:
            trace_id_ctx.reset(token)
        response.headers[TRACE_HEADER] = trace_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """访问日志 + 耗时统计。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            cost_ms = (time.perf_counter() - start) * 1000
            status_code = response.status_code if response is not None else 500
            ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else "-")
            )
            log_fn = logger.warning if cost_ms >= SLOW_REQUEST_MS else logger.info
            log_fn(
                "%s %s -> %s | %.1fms | ip=%s",
                request.method,
                request.url.path,
                status_code,
                cost_ms,
                ip,
            )
            if response is not None:
                response.headers[PROCESS_HEADER] = f"{cost_ms:.1f}"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头（防嗅探、防点击劫持、基础 CSP）。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """简易进程内限流（滑动窗口）。

    仅用于开发/单实例场景：生产请使用 Redis 令牌桶（infrastructure/cache/redis.py）。
    """

    EXEMPT_PATHS = ("/docs", "/redoc", "/openapi.json", "/health", "/health/ready", "/")

    def __init__(self, app: ASGIApp, limit: int = 300, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in self.EXEMPT_PATHS:
            return await call_next(request)

        key = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        now = time.time()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            from app.core.response import api_response

            return api_response(
                code=429,
                message="请求过于频繁，请稍后再试",
                http_status=429,
            )
        bucket.append(now)
        return await call_next(request)


def register_middlewares(app: FastAPI) -> None:
    """注册全部中间件（注意注册顺序：后注册先执行）。"""
    # 1) 限流
    app.add_middleware(RateLimitMiddleware, limit=300, window_seconds=60)
    # 2) 安全头
    app.add_middleware(SecurityHeadersMiddleware)
    # 3) 访问日志
    app.add_middleware(AccessLogMiddleware)
    # 4) 跨域（放在日志之后，保证预检请求也被记录）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[TRACE_HEADER, PROCESS_HEADER],
    )
    # 5) trace_id 最后注册 -> 最先执行，保证其它中间件日志都带 trace_id
    app.add_middleware(TraceIdMiddleware)
    logger.info("中间件注册完成：限流/安全头/访问日志/CORS/链路追踪")
