"""
模块：app/config/logging_config.py
职责：统一日志规范（控制台 + 滚动文件 + 请求链路 trace_id）。

标签：[架构组] [P1]（统一格式 + trace_id + 滚动文件）

实现思路：
1. 使用标准库 logging，定义统一格式：时间 | 级别 | trace_id | 模块 | 消息；
2. trace_id 由 TraceIdFilter 从 contextvars 注入，缺失时填 "-"，保证日志可串联一次请求；
3. 文件日志使用 RotatingFileHandler，按大小轮转，避免磁盘写满；
4. 对 uvicorn / sqlalchemy 的 logger 做收敛，避免重复输出；
5. setup_logging() 幂等，可在 lifespan 中反复调用。

使用：在应用启动时调用一次 setup_logging()，业务代码统一 `logger = get_logger(__name__)`。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextvars import ContextVar

from app.config.settings import settings

# 请求链路 ID：由中间件写入，日志过滤器读取
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(trace_id)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class TraceIdFilter(logging.Filter):
    """把当前请求的 trace_id 注入到日志记录中。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        record.trace_id = trace_id_ctx.get()
        return True


_configured = False


def setup_logging() -> None:
    """初始化根 logger（幂等）。"""
    global _configured
    if _configured:
        return

    settings.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    trace_filter = TraceIdFilter()

    # 1) 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    root.addHandler(console)

    # 2) 文件（按大小滚动）
    file_handler = logging.handlers.RotatingFileHandler(
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
    logging.getLogger(__name__).info("日志系统初始化完成 level=%s", settings.LOG_LEVEL)


def get_logger(name: str) -> logging.Logger:
    """获取 logger，未初始化时自动兜底初始化。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
