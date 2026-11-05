"""
模块：app/infrastructure/cache/redis.py
职责：Redis 客户端管理（可选依赖，未安装 redis 包时自动降级）。

标签：[架构组] [P1]（可降级：token 配额 / 会话记忆 / 限流）

实现思路：
1. 使用 redis.asyncio 连接池，经 patterns.singleton.LazyHolder 单例管理，
   与 MySQL / PgSQL 引擎共用同一套「创建一次、统一释放」语义；
2. init_redis() / get_redis() / close_redis() 三个入口，由 lifespan 调用；
   连接失败时立即清空持有器（不会把「连不上的客户端」留在单例里反复被使用）；
3. 未安装 redis 包或 REDIS_ENABLED=False 时，get_redis() 返回 None，
   业务侧配合 cache_utils 的「无缓存直接查库」策略，保证功能不受影响；
4. 常用封装：get_json / set_json（自动 json 序列化）、delete_keys（支持通配符）、
   acquire_lock（分布式锁，SET NX PX）、incr_with_ttl（限流计数）。

使用示例（Service）：
    cache = get_redis()
    if cache:
        data = await cache.get(f"talent:{tid}")
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config.settings import settings
from app.patterns.singleton import LazyHolder

logger = logging.getLogger(__name__)

_init_logged = False


def _create_client() -> Any | None:
    """创建 Redis 客户端（未启用 / 未安装依赖时返回 None；不在此处 ping）。"""
    if not settings.REDIS_ENABLED:
        return None
    try:
        from redis.asyncio import Redis  # type: ignore[import-not-found]

        return Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    except ImportError:
        logger.warning("未安装 redis 依赖（pip install redis），缓存功能不可用")
        return None


async def _close_client(client: Any) -> None:
    await client.aclose()


_client_holder: LazyHolder[Any] = LazyHolder("redis-client", _create_client, _close_client)


async def init_redis() -> Any | None:
    """初始化 Redis 连接池（幂等）；ping 失败时清空单例并返回 None。"""
    global _init_logged
    if not settings.REDIS_ENABLED:
        if not _init_logged:
            logger.info("Redis 未启用（REDIS_ENABLED=False），缓存能力降级为直连数据库")
            _init_logged = True
        return None
    client = _client_holder.get()
    if client is None:
        return None
    try:
        await client.ping()
    except Exception:  # noqa: BLE001 - 失败必须丢弃客户端，避免坏单例被反复复用
        logger.exception("Redis 连接失败，缓存功能不可用")
        await _client_holder.aclose()
        return None
    logger.info("Redis 连接成功：%s", settings.REDIS_URL)
    return client


def get_redis() -> Any | None:
    """获取 Redis 客户端（可能为 None；未显式 init 时按配置惰性创建）。"""
    return _client_holder.get()


async def close_redis() -> None:
    """关闭连接池并清空单例。"""
    had_client = _client_holder.peek() is not None
    await _client_holder.aclose()
    if had_client:
        logger.info("Redis 连接已关闭")


# ---------------------------------------------------------------- 常用封装
async def get_json(key: str) -> Any | None:
    """读取并反序列化 JSON 缓存。"""
    client = _client_holder.get()
    if not client:
        return None
    raw = await client.get(key)
    return json.loads(raw) if raw else None


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """写入 JSON 缓存（默认 TTL 来自 settings）。"""
    client = _client_holder.get()
    if not client:
        return
    await client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl or settings.REDIS_DEFAULT_TTL)


async def delete_keys(pattern: str) -> int:
    """按通配符删除 key（例如组织树变更后清理 `org:*`）。"""
    client = _client_holder.get()
    if not client:
        return 0
    deleted = 0
    async for key in client.scan_iter(match=pattern):
        deleted += await client.delete(key)
    return deleted


async def incr_with_ttl(key: str, ttl: int) -> int:
    """自增计数并设置过期时间（用于接口限流、短信验证码次数）。

    未连接 Redis 时返回 0（调用方据此回退进程内实现，见 RateLimitMiddleware）。
    """
    client = _client_holder.get()
    if not client:
        return 0
    value = await client.incr(key)
    if value == 1:
        await client.expire(key, ttl)
    return int(value)
