"""
模块：app/infrastructure/scheduler.py
职责：进程内轻量调度器（APScheduler），承载分析库增量同步等定时任务。

标签：[架构组] [P1]（定时任务基础设施，被 [Vanna组] 分析库同步依赖）

实现思路：
1. 单例 AsyncIOScheduler，由 lifespan 启停；ANALYTICS_SYNC_ENABLED=false 时完全不启动，
   避免开发 / 测试环境反复触发全量同步；
2. 任务互斥三层防线：
     ① APScheduler max_instances=1 + coalesce=True（同进程内不重叠、错过的合并为一次）
     ② Redis 分布式锁（lock:sync:analytics）——多 worker / 多实例只有一个能跑
     ③ Redis 不可用时的进程内 asyncio.Lock 兜底（单实例语义）
3. 失败重试：指数退避（5s、10s、20s…，上限 60s），次数由 ANALYTICS_SYNC_RETRY 控制；
   重试耗尽只记录日志，不抛出（定时任务抛错无人接，没必要污染调度器状态）；
4. 触发方式：每日 ANALYTICS_SYNC_CRON_HOUR:ANALYTICS_SYNC_CRON_MINUTE（默认 02:00）；
   也可通过 systemd timer / cron 直接跑 scripts/sync_analytics.py（方案并行不冲突，
   因为两侧都走同一把 Redis 锁）。

使用（应用内自动）：settings.ANALYTICS_SYNC_ENABLED=true 即可，无需改代码。
使用（手工触发一次）：await run_analytics_sync_job()
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from app.config.settings import settings

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:sync:analytics"
JOB_ID = "analytics_sync"

_scheduler: Any | None = None
_process_lock = asyncio.Lock()
_last_result: dict[str, Any] | None = None


def _load_sync_all() -> Callable[..., Any]:
    """延迟导入 ETL 入口（避免启动期加载脚本；scripts 为命名空间包）。"""
    try:
        from scripts.sync_analytics import sync_all
    except ModuleNotFoundError:  # 以安装包方式运行且项目根不在 sys.path 时兜底
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.sync_analytics import sync_all
    return sync_all


async def _sync_with_retry(attempts: int) -> dict[str, int]:
    """带指数退避的同步执行。"""
    sync_all = _load_sync_all()
    delay = 5.0
    for attempt in range(1, attempts + 1):
        try:
            return await sync_all()
        except Exception:
            if attempt >= attempts:
                raise
            logger.warning(
                "分析库同步第 %s/%s 次失败，%.0fs 后重试", attempt, attempts, delay, exc_info=True
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
    return {}  # pragma: no cover - 循环必然 return 或 raise


async def _acquire_sync_lock(owner: str) -> tuple[bool, bool]:
    """获取同步锁，返回 (是否拿到, 是否用了 Redis)。

    Redis 可用时以分布式锁为准；不可用时退化为进程内锁（单实例语义）。
    """
    from app.infrastructure.cache.redis import acquire_lock, get_redis

    if get_redis() is not None:
        return await acquire_lock(LOCK_KEY, owner, settings.ANALYTICS_SYNC_LOCK_TTL), True
    if _process_lock.locked():
        return False, False
    await _process_lock.acquire()
    return True, False


async def _release_sync_lock(owner: str, used_redis: bool) -> None:
    if used_redis:
        from app.infrastructure.cache.redis import release_lock

        await release_lock(LOCK_KEY, owner)
    elif _process_lock.locked():
        _process_lock.release()


async def run_analytics_sync_job() -> dict[str, int] | None:
    """执行一次分析库同步（定时任务与手工触发共用入口）。

    返回各表写入行数；抢不到锁（已有实例在跑）时返回 None。
    """
    global _last_result

    if not settings.PGSQL_ENABLED:
        logger.info("PgSQL 未启用，跳过分析库同步")
        return None

    owner = uuid.uuid4().hex
    acquired, used_redis = await _acquire_sync_lock(owner)
    if not acquired:
        logger.info("分析库同步已在进行中（%s），本轮跳过", "Redis 锁" if used_redis else "进程内锁")
        return None

    try:
        counts = await _sync_with_retry(max(1, settings.ANALYTICS_SYNC_RETRY))
        _last_result = dict(counts)
        logger.info("分析库定时同步完成：%s", counts)
        return counts
    except Exception:  # noqa: BLE001 - 定时任务不向上抛，留日志与 /health 观测
        logger.exception("分析库定时同步失败（已重试 %s 次）", settings.ANALYTICS_SYNC_RETRY)
        return None
    finally:
        await _release_sync_lock(owner, used_redis)


def get_sync_status() -> dict[str, Any]:
    """同步任务状态（供 /system/info 自检展示）。"""
    return {
        "enabled": settings.ANALYTICS_SYNC_ENABLED,
        "running": _scheduler is not None and bool(getattr(_scheduler, "running", False)),
        "cron": f"{settings.ANALYTICS_SYNC_CRON_HOUR:02d}:{settings.ANALYTICS_SYNC_CRON_MINUTE:02d}",
        "last_result": _last_result,
    }


def start_scheduler() -> Any | None:
    """启动调度器（幂等）。未启用或缺少 apscheduler 时返回 None。"""
    global _scheduler

    if not settings.ANALYTICS_SYNC_ENABLED:
        logger.info("分析库定时同步未启用（ANALYTICS_SYNC_ENABLED=false）")
        return None
    if _scheduler is not None and getattr(_scheduler, "running", False):
        return _scheduler

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("未安装 apscheduler（pip install apscheduler），定时同步不可用")
        return None

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_analytics_sync_job,
        trigger=CronTrigger(
            hour=settings.ANALYTICS_SYNC_CRON_HOUR,
            minute=settings.ANALYTICS_SYNC_CRON_MINUTE,
        ),
        id=JOB_ID,
        name="MySQL -> PgSQL 分析库增量同步",
        max_instances=1,          # 同一进程内不重叠
        coalesce=True,            # 错过的多次触发合并为一次
        misfire_grace_time=3600,  # 启动晚于计划点 1 小时内仍补跑
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "分析库定时同步已启动：每日 %02d:%02d（redis_lock=%s）",
        settings.ANALYTICS_SYNC_CRON_HOUR,
        settings.ANALYTICS_SYNC_CRON_MINUTE,
        "on",
    )
    return scheduler


def shutdown_scheduler() -> None:
    """停止调度器（不等待正在执行的任务，避免拖慢进程退出）。"""
    global _scheduler
    if _scheduler is None:
        return
    try:
        if getattr(_scheduler, "running", False):
            _scheduler.shutdown(wait=False)
            logger.info("分析库调度器已停止")
    except Exception:  # noqa: BLE001
        logger.exception("停止调度器失败（忽略）")
    finally:
        _scheduler = None


__all__ = [
    "start_scheduler",
    "shutdown_scheduler",
    "run_analytics_sync_job",
    "get_sync_status",
]
