"""
模块：app/core/lifespan.py
职责：应用生命周期管理（启动 / 关闭钩子）。

实现思路：
1. 使用 asynccontextmanager 定义 lifespan，替代已废弃的 on_event；
2. 启动阶段：
     日志初始化 -> 目录准备 -> 数据库连通性检测 -> （开发环境）自动建表
     -> 可选：初始化 Redis / Milvus 客户端（当前关闭） -> 打印启动信息
3. 关闭阶段：
     关闭 Redis / Milvus（若启用） -> 释放数据库连接池 -> 打印关闭日志
4. 任一必需资源初始化失败（如 DEBUG=False 且数据库不可用）时，直接抛异常终止启动，
   避免「服务起来了但根本不能用」的假可用；
   开发环境（DEBUG=True）下数据库不可用时仅告警，允许先访问 Swagger 排查。
5. 生产环境不自动建表：表结构变更一律走 Alembic。

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
from app.infrastructure.database.session import check_connection, close_db, init_db

logger = logging.getLogger(__name__)


async def _startup() -> None:
    """启动流程。"""
    logger.info("=" * 64)
    logger.info("%s v%s 启动中（env=%s）", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)
    settings.ensure_dirs()

    # 1) 数据库连通性
    connected = await check_connection()
    if not connected:
        message = "数据库连接失败，请检查 DB_* 配置"
        if settings.is_prod:
            logger.error(message)
            raise RuntimeError(message)
        logger.warning("%s（开发模式下继续启动，/health 会返回 down）", message)

    # 2) 开发环境自动建表
    if connected and not settings.is_prod:
        try:
            await init_db()
        except Exception:  # noqa: BLE001
            logger.exception("自动建表失败（不影响服务启动，可手动执行 scripts/init_db.py）")

    # 3) 可选基础设施（默认关闭，需要时打开）
    if settings.REDIS_ENABLED:
        try:
            from app.infrastructure.cache.redis import init_redis

            await init_redis()
        except Exception:  # noqa: BLE001
            logger.exception("Redis 初始化失败")

    logger.info(
        "接口文档：http://127.0.0.1:%s%s | 健康检查：http://127.0.0.1:%s/health",
        settings.PORT, settings.DOCS_URL, settings.PORT,
    )
    logger.info("=" * 64)


async def _shutdown() -> None:
    """关闭流程。"""
    logger.info("%s 正在关闭……", settings.APP_NAME)

    if settings.REDIS_ENABLED:
        try:
            from app.infrastructure.cache.redis import close_redis

            await close_redis()
        except Exception:  # noqa: BLE001
            logger.exception("Redis 关闭失败")

    await close_db()
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
