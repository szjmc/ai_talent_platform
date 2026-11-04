"""
模块：app/infrastructure/database/health.py
职责：数据库健康检查与启动前置校验（不依赖 MySQL 驱动即可判断可用性）。

标签：[架构组] [P1]（连通性体检 + 中文修复建议）

为什么单独一层：
    1. `/health`、`/health/ready` 必须在「数据库挂了」时也能给出明确原因
       （密码错误 / 库不存在 / 服务没起 / 驱动缺失），而不是抛 500；
    2. 这些检查走 TCP + 驱动异常分类，不经过 ORM 引擎，避免连接池等待拖慢探针；
    3. 启动日志、scripts/init_db.py、README 的排障提示共用同一套诊断信息。

返回结构：
    {
      "target": "127.0.0.1:3306/ai_talent_platform",
      "status": "up" | "down" | "disabled",
      "reason": "ok" | "connection_refused" | "auth_failed" | "database_missing"
                | "driver_missing" | "timeout" | "unknown",
      "message": "中文可读说明（含修复建议）",
      "elapsed_ms": 3.2
    }
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, Literal

from app.config.settings import settings

logger = logging.getLogger(__name__)

StatusLiteral = Literal["up", "down", "disabled"]
ReasonLiteral = Literal[
    "ok",
    "connection_refused",
    "auth_failed",
    "database_missing",
    "driver_missing",
    "timeout",
    "unknown",
]

TCP_TIMEOUT = 2.0


def _result(
    target: str,
    status: StatusLiteral,
    reason: ReasonLiteral,
    message: str,
    elapsed_ms: float = 0.0,
) -> dict[str, Any]:
    return {
        "target": target,
        "status": status,
        "reason": reason,
        "message": message,
        "elapsed_ms": round(elapsed_ms, 1),
    }


async def _tcp_check(host: str, port: int) -> bool:
    """TCP 连通性探测（不依赖数据库驱动）。"""
    loop = asyncio.get_running_loop()

    def _connect() -> bool:
        try:
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT):
                return True
        except OSError:
            return False

    return await loop.run_in_executor(None, _connect)


def _classify_driver_error(exc: BaseException) -> tuple[ReasonLiteral, str]:
    """把驱动异常翻译成可读原因 + 修复建议。"""
    text = f"{type(exc).__name__}: {exc}".lower()

    if "modulenotfound" in text or "no module named" in text:
        return "driver_missing", "数据库驱动未安装，请执行 pip install -r requirements.txt"
    if "access denied" in text or "password authentication failed" in text or "authentication" in text:
        return "auth_failed", "数据库账号或密码错误，请检查 .env 中的账号配置"
    if "unknown database" in text or 'database "' in text and "does not exist" in text:
        return "database_missing", "目标数据库不存在，请先执行 python scripts/init_db.py"
    if "refused" in text or "can't connect" in text or "could not connect" in text:
        return "connection_refused", "数据库服务未启动或端口不通，请确认服务已运行"
    if "timeout" in text or "timed out" in text:
        return "timeout", "数据库连接超时，请检查网络、防火墙或连接池配置"
    return "unknown", "数据库连接失败，请查看服务端日志获取详细堆栈"


async def check_mysql() -> dict[str, Any]:
    """MySQL（业务主库）健康检查。"""
    target = f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    started = time.perf_counter()

    if not await _tcp_check(settings.DB_HOST, settings.DB_PORT):
        return _result(
            target,
            "down",
            "connection_refused",
            f"无法连接 MySQL {settings.DB_HOST}:{settings.DB_PORT}，"
            "请确认 MySQL 服务已启动、端口正确（.env 中 DB_HOST / DB_PORT）",
            (time.perf_counter() - started) * 1000,
        )

    try:
        import aiomysql  # noqa: F401  驱动缺失时在下方 except 中提示
    except ModuleNotFoundError:
        return _result(
            target,
            "down",
            "driver_missing",
            "未安装 aiomysql，请执行 pip install -r requirements.txt",
            (time.perf_counter() - started) * 1000,
        )

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(
            settings.DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 3}
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return _result(
            target, "up", "ok", "MySQL 连接正常", (time.perf_counter() - started) * 1000
        )
    except Exception as exc:  # noqa: BLE001
        reason, message = _classify_driver_error(exc)
        logger.warning("MySQL 健康检查失败：%s", message)
        return _result(target, "down", reason, message, (time.perf_counter() - started) * 1000)


async def check_pgsql() -> dict[str, Any]:
    """PgSQL（分析 / 时序库）健康检查。"""
    if not settings.PGSQL_ENABLED:
        return _result(
            f"{settings.PGSQL_HOST}:{settings.PGSQL_PORT}/{settings.PGSQL_DB}",
            "disabled",
            "ok",
            "PgSQL 未启用（PGSQL_ENABLED=False），分析类功能降级为仅统计 MySQL",
        )

    target = f"{settings.PGSQL_HOST}:{settings.PGSQL_PORT}/{settings.PGSQL_DB}"
    started = time.perf_counter()

    if not await _tcp_check(settings.PGSQL_HOST, settings.PGSQL_PORT):
        return _result(
            target,
            "down",
            "connection_refused",
            f"无法连接 PgSQL {settings.PGSQL_HOST}:{settings.PGSQL_PORT}，"
            "请确认 PgSQL 服务已启动（.env 中 PGSQL_HOST / PGSQL_PORT）",
            (time.perf_counter() - started) * 1000,
        )

    try:
        import asyncpg  # noqa: F401
    except ModuleNotFoundError:
        return _result(
            target,
            "down",
            "driver_missing",
            "未安装 asyncpg，请执行 pip install -r requirements.txt",
            (time.perf_counter() - started) * 1000,
        )

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(settings.PGSQL_URL, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return _result(
            target, "up", "ok", "PgSQL 连接正常", (time.perf_counter() - started) * 1000
        )
    except Exception as exc:  # noqa: BLE001
        reason, message = _classify_driver_error(exc)
        logger.warning("PgSQL 健康检查失败：%s", message)
        return _result(target, "down", reason, message, (time.perf_counter() - started) * 1000)


async def check_databases() -> dict[str, Any]:
    """MySQL + PgSQL 总览（供 /health/ready 与启动日志使用）。

    整体状态：
        up        MySQL 可用且 PgSQL 可用或未启用
        degraded  MySQL 可用但 PgSQL 不可用（业务接口可用，分析功能降级）
        down      MySQL 不可用（业务不可服务）
    """
    mysql = await check_mysql()
    pgsql = await check_pgsql()

    if mysql["status"] != "up":
        overall = "down"
    elif pgsql["status"] == "down":
        overall = "degraded"
    else:
        overall = "up"

    return {
        "status": overall,
        "mysql": mysql,
        "pgsql": pgsql,
        "redis": "enabled" if settings.REDIS_ENABLED else "disabled",
        "milvus": "enabled" if settings.MILVUS_ENABLED else "disabled",
    }
