"""
模块：app/ai/utils/retry_handler.py
职责：AI 调用重试（指数退避 + 超时 + 熔断保护）。

标签：[架构组] [P1]（被全部 LLM 适配器与 Agent 依赖）

============================ 实现思路与代码要点 ============================
1. 装饰器 @ai_retry(max_attempts=3, base_delay=0.5, retry_on=(TimeoutError, ExternalServiceError, AIError))；
2. 只重试「可恢复」错误（超时、429、5xx、连接错误），业务错误（4xx/校验失败）不重试；
3. 熔断：连续失败 N 次后短时间内快速失败，避免雪崩（进程内计数即可）；
4. 每次重试记录 warning 日志（trace_id 贯通）。
=========================================================================
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from typing import Any, Callable, Iterable

from app.core.exceptions import AIError, BusinessException, ExternalServiceError

logger = logging.getLogger(__name__)

# 默认可重试异常：超时 / 外部服务（含 AIError）/ 连接类错误
DEFAULT_RETRY_ON: tuple[type[BaseException], ...] = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    ExternalServiceError,
    AIError,
)


def is_permanent_http_error(exc: BaseException) -> bool:
    """识别携带HTTP状态的不可恢复请求错误，不改变无状态异常的既有处理。"""
    data = getattr(exc, "data", None)
    status = data.get("status") if isinstance(data, dict) else None
    return isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}


def is_retryable(exc: BaseException, retry_on: Iterable[type[BaseException]]) -> bool:
    """判断异常是否可重试：业务异常（参数/权限/规则）一律不重试。"""
    if isinstance(exc, BusinessException) and not isinstance(exc, (ExternalServiceError, AIError)):
        return False
    if is_permanent_http_error(exc):
        return False
    return isinstance(exc, tuple(retry_on))


def ai_retry(
    *,
    max_attempts: int | None = None,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: Iterable[type[BaseException]] = DEFAULT_RETRY_ON,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """异步重试装饰器（指数退避 + 抖动）。

    :param max_attempts: 最大尝试次数（含首次）；None 时取 settings.LLM_MAX_RETRIES + 1
    :param base_delay: 首次重试基准延迟（秒），之后按 2 的指数增长并加抖动
    :param max_delay: 单次延迟上限（秒）
    :param retry_on: 触发重试的异常类型集合
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from app.config.settings import settings

            attempts = max_attempts or (settings.LLM_MAX_RETRIES + 1)
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - 需要判断是否重试
                    last_exc = exc
                    if not is_retryable(exc, retry_on) or attempt >= attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay += random.uniform(0, base_delay)  # 抖动，避免同时重试
                    logger.warning(
                        "%s 第 %s/%s 次调用失败，%.2fs 后重试：%s",
                        func.__qualname__,
                        attempt,
                        attempts,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # pragma: no cover - 循环内已处理所有出口

        return wrapper

    return decorator


class CircuitBreakerOpenError(AIError):
    """熔断器打开（短期快速失败）。"""

    code = 5002
    message = "外部服务熔断中，请稍后重试"


class SimpleCircuitBreaker:
    """进程内简单熔断器：连续失败达到阈值后打开，reset_seconds 后进入半开。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str = "default", fail_threshold: int = 5, reset_seconds: float = 30.0) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_seconds = reset_seconds
        self._fail_count = 0
        self._opened_at: float | None = None

    # ------------------------------------------------------------------ 状态
    @property
    def fail_count(self) -> int:
        return self._fail_count

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return self.CLOSED
        if time.monotonic() - self._opened_at >= self.reset_seconds:
            return self.HALF_OPEN
        return self.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state != self.OPEN

    def check(self) -> None:
        """熔断器打开时直接抛错（快速失败）。"""
        if self.state == self.OPEN:
            raise CircuitBreakerOpenError(f"熔断器 {self.name} 已打开，{self.reset_seconds}s 后重试")

    # ------------------------------------------------------------------ 记录
    def record_success(self) -> None:
        self._fail_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._fail_count += 1
        if self._fail_count >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "熔断器 %s 打开（连续失败 %s 次，%ss 后半开）",
                self.name,
                self._fail_count,
                self.reset_seconds,
            )

    def reset(self) -> None:
        self.record_success()

    # ------------------------------------------------------------------ 调用
    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """带熔断保护的调用：打开时快速失败，成功清零、失败计数。"""
        self.check()
        try:
            result = func(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
        except BaseException as exc:
            if is_retryable(exc, DEFAULT_RETRY_ON):
                self.record_failure()
            raise
        self.record_success()
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "fail_count": self._fail_count,
            "fail_threshold": self.fail_threshold,
            "reset_seconds": self.reset_seconds,
        }


__all__ = [
    "CircuitBreakerOpenError",
    "SimpleCircuitBreaker",
    "ai_retry",
    "is_retryable",
    "is_permanent_http_error",
]
