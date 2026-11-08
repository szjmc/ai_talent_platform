"""
模块：app/core/lifespan.py
职责：应用生命周期管理（启动 / 关闭钩子）。

标签：[架构组] [P0]（启动 / 关闭资源编排：四库连通性与降级）

实现思路：
1. 使用 asynccontextmanager 定义 lifespan，替代已废弃的 on_event；
2. 启动阶段：
     日志初始化 -> 目录准备 -> MySQL 连通性检测 -> （dev/test 且允许时）自动建表
     -> PgSQL 分析库连通性检测（可选依赖，失败仅告警）
     -> 审计事件订阅（EventBus -> AuditService，幂等注册）
     -> 可选：初始化 Redis / Milvus 客户端（当前关闭） -> 打印启动信息
3. 关闭阶段：
     等待事件总线任务收尾（drain，保证审计落库） -> 关闭 Redis / Milvus（若启用）
     -> 释放 MySQL / PgSQL 连接池 -> 打印关闭日志
4. MySQL 是硬依赖：生产环境（APP_ENV=prod）连接失败直接抛异常终止启动，
   避免「服务起来了但根本不能用」的假可用；开发环境仅告警，便于先排查配置；
5. PgSQL 是分析类硬依赖：默认启用，连接失败时告警并降级（统计只读 MySQL），
   不阻塞业务接口启动 —— 若要求强一致，可把 PGSQL_ENABLED 设为必需项并在此改为抛错；
6. 生产环境绝不自动建表：settings.db_auto_create_tables 在 APP_ENV=prod 时恒为 False
   （表结构变更一律走 Alembic），dev/test 可用 DB_AUTO_CREATE_TABLES 关闭，
   多实例并发启动由 init_db 的 MySQL 命名锁（GET_LOCK）串行化。

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

    # 1.5) 连接池预热：远程 MySQL 建连（TCP+鉴权）实测可达 5~19 秒，启动时并发
    #      建立常驻连接，避免重启后首批请求现场建连导致 20s+ 超时（列表加载失败）。
    if mysql_ok:
        try:
            from app.infrastructure.database.session import warm_up_pool

            await warm_up_pool()
        except Exception:  # noqa: BLE001 - 预热失败不阻塞启动
            logger.exception("MySQL 连接池预热失败（首个请求将现场建连）")

    # 2) 自动建表：仅 dev/test 且 DB_AUTO_CREATE_TABLES=true（prod 恒为 False，走 Alembic）
    if mysql_ok and settings.db_auto_create_tables:
        try:
            await init_db()
        except Exception:  # noqa: BLE001
            logger.exception("自动建表失败（不影响服务启动，可手动执行 scripts/init_db.py）")
    elif mysql_ok:
        logger.info(
            "已跳过启动自动建表（APP_ENV=%s / DB_AUTO_CREATE_TABLES=%s），表结构变更请走 Alembic",
            settings.APP_ENV,
            settings.DB_AUTO_CREATE_TABLES,
        )

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

    # 4) 业务事件订阅者（进程内事件总线的消费端）
    #    没有订阅者时 publish 是静默丢弃的，业务承诺（推送 / 预警 / 复测回流 / 审计）
    #    就只剩一句空话。注册在启动阶段，保证「进程起来 = 有人在听」。
    #    两段各自 try：审计挂不上不该拖垮培训推送，反之亦然。
    try:
        from app.services.audit_service import register_audit_handlers

        register_audit_handlers()
    except Exception:  # noqa: BLE001
        logger.exception("审计事件订阅注册失败（主流程继续，审计可能缺失）")
    try:
        from app.services.notification_service import (
            register_training_notification_subscribers,
        )

        register_training_notification_subscribers()
    except Exception:  # noqa: BLE001 - 订阅失败不应阻塞服务启动
        logger.exception("培训站内信订阅注册失败（不影响服务启动，推送将只发事件不落库）")

    # 5) 缓存基础设施：Redis 优先，不可用时自动降级 fakeredis/内存缓存，永不返回 None
    try:
        from app.infrastructure.cache.redis import init_redis, cache_kind

        await init_redis()
        logger.info("缓存后端就绪：kind=%s", cache_kind())
    except Exception:  # noqa: BLE001
        logger.exception("缓存初始化失败（将使用极简内存兜底）")

    # 6) 进程内定时任务（分析库增量同步；放在 Redis 之后，确保分布式锁可用）
    try:
        from app.infrastructure.scheduler import start_scheduler

        start_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("定时任务启动失败（不影响接口服务）")

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

    # 1) 先停定时任务，避免关闭过程中又触发新一轮同步
    try:
        from app.infrastructure.scheduler import shutdown_scheduler

        shutdown_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("停止调度器失败（忽略）")

    # 2) 先等待事件总线（审计等副作用）完成，此时数据库连接仍可用
    try:
        from app.infrastructure.messaging.event_bus import event_bus

        await event_bus.drain(timeout=5.0)
    except Exception:  # noqa: BLE001
        logger.exception("事件总线收尾失败（忽略）")

    # 3) 关闭缓存（Redis/fakeredis/内存统一入口）
    try:
        from app.infrastructure.cache.redis import close_redis

        await close_redis()
    except Exception:  # noqa: BLE001
        logger.exception("缓存关闭失败")

    # 4) 释放连接池（同时重置 MySQL 与 PgSQL 引擎单例）
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
