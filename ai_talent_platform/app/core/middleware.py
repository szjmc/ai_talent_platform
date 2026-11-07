"""
模块：app/core/middleware.py
职责：全局中间件（请求追踪、访问日志、耗时统计、跨域、安全响应头、限流）。

标签：[架构组] [P1]（trace_id / 访问日志 / 限流 / 安全响应头）

实现思路：
1. TraceIdMiddleware：为每个请求生成 trace_id（优先复用请求头 X-Trace-Id，
   但必须通过 ^[A-Za-z0-9_-]{1,64}$ 白名单校验，非法值直接丢弃并重新生成，
   防止把换行 / 控制字符注入访问日志），写入 contextvar 供日志使用，并回写到响应头；
2. AccessLogMiddleware：记录 method / path / 状态码 / 耗时 / 客户端 IP，慢请求 warning；
3. CORSMiddleware：来源从 settings.CORS_ORIGINS 读取；通配符模式自动关闭
   allow_credentials（浏览器拒绝 *+credentials 组合；Bearer 鉴权不受影响）；
4. SecurityHeadersMiddleware：添加 X-Content-Type-Options 等安全头；
5. RateLimitMiddleware（settings.RATE_LIMIT_*）：Redis 启用时用固定窗口计数
   （infrastructure/cache/redis.incr_with_ttl，多实例共享），Redis 不可用时
   自动回退进程内滑动窗口，保证任何部署形态下限流都生效；
6. register_middlewares(app) 统一注册。注意：中间件注册顺序与执行顺序相反，
   因此 trace_id 必须最后注册，才能最先执行。

注意：不要在这里做鉴权（鉴权在 core/dependencies），也不要在这里写业务逻辑。
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.ai.context import clear_context
from app.config.logging_config import get_logger, trace_id_ctx
from app.config.settings import settings

logger = get_logger("app.access")

TRACE_HEADER = "X-Trace-Id"
PROCESS_HEADER = "X-Process-Time-Ms"
SLOW_REQUEST_MS = 1000

# trace_id 白名单：仅允许 URL 安全字符，长度 1-64；其余一律视为非法输入（防日志注入）
TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 当前请求的对外 base URL（scheme://host）。
# 用途：邀约链接等「需要绝对地址但配置项为空」的场景，回退到请求 Host，
# 而不是直接抛 1002 让业务不可用。用 ContextVar 而非层层传参，
# 是因为调用点在 Service 内部（build_invitation_link），拿不到 Request。
request_base_url_ctx: ContextVar[str] = ContextVar("request_base_url", default="")


def request_base_url(request: Request) -> str:
    """从请求推导对外 base URL（优先 X-Forwarded-*，兼容反向代理）。"""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not host:
        return ""
    return f"{proto}://{host}"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 trace_id（复用入参前先做格式校验）。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(TRACE_HEADER, "")
        trace_id = incoming if TRACE_ID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = trace_id_ctx.set(trace_id)
        base_token = request_base_url_ctx.set(request_base_url(request))
        request.state.trace_id = trace_id
        try:
            response = await call_next(request)
        finally:
            # 复位日志 trace_id + 清空请求级 AI 上下文（AIContext.set_context 由
            # get_current_user 写入），防止同 task 复用时身份泄漏到下一个请求
            trace_id_ctx.reset(token)
            request_base_url_ctx.reset(base_token)
            clear_context()
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
    """接口限流：Redis 固定窗口优先（多实例共享），进程内滑动窗口兜底。

    - Redis 启用且可用：`ratelimit:{ip}:{window_bucket}` 自增计数，TTL 对齐窗口，
      所有 worker / 实例共享同一份配额；
    - Redis 未启用或调用失败：回退本进程滑动窗口（单实例语义），并告警一次；
    - 阈值与窗口来自 settings.RATE_LIMIT_*，关闭见 RATE_LIMIT_ENABLED。
    """

    EXEMPT_PATHS = ("/docs", "/redoc", "/openapi.json", "/health", "/health/ready", "/")

    def __init__(self, app: ASGIApp, limit: int = 300, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._redis_warned = False

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return request.client.host if request.client else "unknown"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)
        path = request.url.path
        if request.method == "OPTIONS" or path in self.EXEMPT_PATHS:
            return await call_next(request)

        key = self._client_key(request)
        if not await self._allow(key):
            from app.core.response import api_response

            return api_response(
                code=429,
                message="请求过于频繁，请稍后再试",
                http_status=429,
            )
        return await call_next(request)

    async def _allow(self, key: str) -> bool:
        """返回 True 表示放行。"""
        redis_allowed = await self._redis_allow(key)
        if redis_allowed is not None:
            return redis_allowed
        return self._memory_allow(key)

    async def _redis_allow(self, key: str) -> bool | None:
        """Redis 固定窗口计数；Redis 不可用返回 None（调用方回退进程内）。"""
        from app.infrastructure.cache.redis import get_redis, incr_with_ttl

        client = get_redis()
        if client is None:
            return None
        try:
            bucket = int(time.time() // self.window)
            count = await incr_with_ttl(f"ratelimit:{key}:{bucket}", ttl=self.window + 1)
            return count <= self.limit
        except Exception:  # noqa: BLE001 - Redis 故障不能打挂限流，回退进程内
            if not self._redis_warned:
                logger.warning("Redis 限流不可用，已回退进程内限流（多实例部署请检查 Redis）")
                self._redis_warned = True
            return None

    def _memory_allow(self, key: str) -> bool:
        """进程内滑动窗口（Redis 关闭 / 故障时的兜底）。"""
        now = time.time()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


def register_middlewares(app: FastAPI) -> None:
    """注册全部中间件（注意注册顺序：后注册先执行）。"""
    # 1) 限流（最内层：先过安全头 / 日志 / CORS / trace，再计数，预检请求不计费）
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(
            RateLimitMiddleware,
            limit=settings.RATE_LIMIT_LIMIT,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
    # 2) 安全头
    app.add_middleware(SecurityHeadersMiddleware)
    # 3) 访问日志
    app.add_middleware(AccessLogMiddleware)
    # 4) 跨域（放在日志之后，保证预检请求也被记录）。
    #    通配符来源必须关闭 credentials（浏览器规范 + 安全）；Bearer 鉴权不受影响。
    if "*" in settings.cors_origin_list and not settings.cors_allow_credentials:
        logger.info("CORS 来源为通配符 *，已自动关闭 allow_credentials（如需携带凭据请配置明确来源）")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[TRACE_HEADER, PROCESS_HEADER],
    )
    # 5) trace_id 最后注册 -> 最先执行，保证其它中间件日志都带 trace_id
    app.add_middleware(TraceIdMiddleware)
    logger.info("中间件注册完成：限流/安全头/访问日志/CORS/链路追踪")
