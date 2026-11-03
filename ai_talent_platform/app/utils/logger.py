"""
模块：app/utils/logger.py
职责：日志工具兼容层。

标签：[架构组] [P1]（日志入口兼容层，实现在 config/logging_config）

说明：真正的日志配置在 app/config/logging_config.py（统一格式 + trace_id + 滚动文件）。
本文件只是给习惯 `from app.utils.logger import get_logger` 的同学提供同一入口，
避免两套日志实现。

新增代码推荐直接使用：
    from app.config.logging_config import get_logger
"""

from app.config.logging_config import get_logger, setup_logging, trace_id_ctx

__all__ = ["get_logger", "setup_logging", "trace_id_ctx"]
