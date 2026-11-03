"""
模块：app/core/lifespan.py
职责：应用生命周期管理（启动 / 关闭钩子）。

实现思路：
1. 使用 asynccontextmanager 定义 lifespan，替代已废弃的 on_event；
2. 启动阶段：
     日志初始化 -> 目录准备 -> MySQL 连通性检测 -> （开发环境）自动建表
     -> PgSQL 分析库连通性检测（可选依赖，失败仅告警）
     -> 可选：初始化 Redis / Milvus 客户端（当前关闭） -> 打印启动信息
3. 关闭阶段：
     关闭 Redis / Milvus（若启用） -> 释放 MySQL / PgSQL 连接池 -> 打印关闭日志
4. MySQL 是硬依赖：生产环境（DEBUG=False）连接失败直接抛异常终止启动，
   避免「服务起来了但根本不能用」的假可用；开发环境仅告警，便于先排查配置；
5. PgSQL 是分析类硬依赖：默认启用，连接失败时告警并降级（统计只读 MySQL），
   不阻塞业务接口启动 —— 若要求强一致，可把 PGSQL_ENABLED 设为必需项并在此改为抛错；
6. 生产环境不自动建表：表结构变更一律走 Alembic。

扩展位置（其他同学在此接入自己的基础设施）：
    - Redis：`from app.infrastructure.cache.redis import init_redis, close_redis`
    - Milvus：`from app.infrastructure.vector.milvus import init_milvus, close_milvus`
    - 事件总线 / 定时任务：同理
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.logging_config import setup_logging
from app.config.settings import settings
from app.infrastructure.database.health import check_mysql, check_pgsql
from app.infrastructure.database.session import close_db, init_db

logger = logging.getLogger(__name__)


async def _startup() -> None:
    """启动流程。"""
    logger.info("=" * 68)
    logger.info("%s v%s 启动中（env=%s）", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)
    settings.ensure_dirs()
    logger.info("数据库配置：%s", settings.database_summary())

    # 1) MySQL（业务主库，硬依赖）
    mysql_report = await check_mysql()
    mysql_ok = mysql_report["status"] == "up"
    if not mysql_ok:
        message = (
            f"MySQL 连接失败（{mysql_report['target']}）：{mysql_report['message']}"
        )
        if settings.is_prod:
            logger.error(message)
            raise RuntimeError(message)
        logger.warning("%s（开发模式下继续启动，/health/ready 会返回 503 并显示原因）", message)
    else:
        logger.info("MySQL 连接成功 %s", mysql_report["target"])

    # 2) 开发环境自动建表
    if mysql_ok and not settings.is_prod:
        try:
            await init_db()
        except Exception:  # noqa: BLE001
            logger.exception("自动建表失败（不影响服务启动，可手动执行 scripts/init_db.py）")

    # 3) PgSQL（分析 / 时序库）
    pgsql_report = await check_pgsql()
    if pgsql_report["status"] == "disabled":
        logger.info("PgSQL 未启用（PGSQL_ENABLED=False），分析类功能降级为仅统计 MySQL")
    elif pgsql_report["status"] == "up":
        logger.info("PgSQL 分析库连接成功 %s", pgsql_report["target"])
    else:
        logger.warning(
            "PgSQL 连接失败（%s）：%s；分析类功能将降级为仅统计 MySQL",
            pgsql_report["target"],
            pgsql_report["message"],
        )

    # 4) 可选基础设施（默认关闭，需要时打开）
    if settings.REDIS_ENABLED:
        try:
            from app.infrastructure.cache.redis import init_redis

            await init_redis()
        except Exception:  # noqa: BLE001
            logger.exception("Redis 初始化失败")

    logger.info(
        "接口文档：http://127.0.0.1:%s%s | 健康检查：http://127.0.0.1:%s/health/ready",
        settings.PORT,
        settings.DOCS_URL,
        settings.PORT,
    )
    logger.info("=" * 68)


async def _shutdown() -> None:
    """关闭流程。"""
    logger.info("%s 正在关闭……", settings.APP_NAME)

    if settings.REDIS_ENABLED:
        try:
            from app.infrastructure.cache.redis import close_redis

            await close_redis()
        except Exception:  # noqa: BLE001
            logger.exception("Redis 关闭失败")

    await close_db()  # 同时释放 MySQL 与 PgSQL 连接池
    logger.info("%s 已安全关闭", settings.APP_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期入口。"""
    setup_logging()
    await _startup()
    try:
        yield
    finally:
        await _shutdown()
