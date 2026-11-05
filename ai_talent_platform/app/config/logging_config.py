"""
模块：app/config/logging_config.py
职责：统一日志规范（控制台 + 滚动文件 + 请求链路 trace_id + 敏感信息脱敏）。

标签：[架构组] [P1]（统一格式 + trace_id + 滚动文件 + 脱敏）

实现思路：
1. 使用标准库 logging，定义统一格式：时间 | 级别 | trace_id | 模块 | 消息；
2. trace_id 由 TraceIdFilter 从 contextvars 注入，缺失时填 "-"，保证日志可串联一次请求；
3. 文件日志按大小轮转：多 worker 部署时优先使用 concurrent-log-handler
   （文件锁 + 单一滚动，避免多个进程同时 rotate 互相截断），
   未安装时回退标准 RotatingFileHandler（单进程语义，启动日志会注明）；
4. RedactingFormatter 对最终输出做敏感字段脱敏（password / token / secret /
   Bearer / JWT 等），防止密码与令牌原样落盘（trace_id 格式校验见 middleware）；
5. 对 uvicorn / sqlalchemy 的 logger 做收敛，避免重复输出；
6. setup_logging() 幂等，可在 lifespan 中反复调用。

使用：在应用启动时调用一次 setup_logging()，业务代码统一 `logger = get_logger(__name__)`。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from contextvars import ContextVar

from app.config.settings import settings

# 请求链路 ID：由中间件写入，日志过滤器读取
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(trace_id)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ------------------------------------------------------------------ 敏感信息脱敏
# 形如 password=xxx / "token": "xxx" / api_key: xxx（大小写不敏感），值替换为 ***
_KV_RE = re.compile(
    r"(?i)\b(password|passwd|old_password|new_password|password_hash|token|"
    r"access_token|refresh_token|secret|secret_key|jwt_secret|api_key|apikey|"
    r"authorization|refresh_token)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]+=*")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def redact_sensitive(text: str) -> str:
    """脱敏日志文本中的密码 / 令牌等敏感值（幂等、无异常）。"""
    if not text:
        return text
    text = _KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _BEARER_RE.sub(r"\1 ***", text)
    text = _JWT_RE.sub("***JWT***", text)
    return text


class TraceIdFilter(logging.Filter):
    """把当前请求的 trace_id 注入到日志记录中。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        record.trace_id = trace_id_ctx.get()
        return True


class RedactingFormatter(logging.Formatter):
    """格式化后对最终字符串做敏感信息脱敏（覆盖 message 与参数拼接结果）。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive(super().format(record))


_configured = False


def setup_logging() -> None:
    """初始化根 logger（幂等）。"""
    global _configured
    if _configured:
        return

    settings.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    formatter = RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    trace_filter = TraceIdFilter()

    # 1) 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    root.addHandler(console)

    # 2) 文件（按大小滚动；多 worker / 多进程用并发安全实现，避免轮转竞争）
    rotation_impl = "rotating"
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler as RotatingFileHandler

        rotation_impl = "concurrent"
    except ImportError:  # pragma: no cover - 未安装时回退标准实现
        RotatingFileHandler = logging.handlers.RotatingFileHandler  # type: ignore[assignment,misc]
    file_handler = RotatingFileHandler(  # type: ignore[call-arg]
        filename=os.path.join(settings.LOG_DIR, "app.log"),
        maxBytes=settings.LOG_ROTATION_MB * 1024 * 1024,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(trace_filter)
    root.addHandler(file_handler)

    # 3) 收敛第三方库日志
    for noisy in ("uvicorn.access", "aiomysql", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))

    _configured = True
    logging.getLogger(__name__).info(
        "日志系统初始化完成 level=%s rotation=%s redact=on", settings.LOG_LEVEL, rotation_impl
    )


def get_logger(name: str) -> logging.Logger:
    """获取 logger，未初始化时自动兜底初始化。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
