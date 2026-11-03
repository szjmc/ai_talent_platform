"""
模块：app/infrastructure/cache/redis.py
职责：Redis 客户端管理（可选依赖，未安装 redis 包时自动降级）。

实现思路：
1. 使用 redis.asyncio 连接池，单例管理；
2. init_redis() / get_redis() / close_redis() 三个入口，由 lifespan 调用；
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

logger = logging.getLogger(__name__)

_client: Any | None = None


async def init_redis() -> Any | None:
    """初始化 Redis 连接池（幂等）。"""
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_ENABLED:
        logger.info("Redis 未启用（REDIS_ENABLED=False），缓存能力降级为直连数据库")
        return None
    try:
        from redis.asyncio import Redis  # type: ignore[import-not-found]

        _client = Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
        await _client.ping()
        logger.info("Redis 连接成功：%s", settings.REDIS_URL)
        return _client
    except ImportError:
        logger.warning("未安装 redis 依赖（pip install redis），缓存功能不可用")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Redis 连接失败，缓存功能不可用")
        return None


def get_redis() -> Any | None:
    """获取 Redis 客户端（可能为 None）。"""
    return _client


async def close_redis() -> None:
    """关闭连接池。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Redis 连接已关闭")


# ---------------------------------------------------------------- 常用封装
async def get_json(key: str) -> Any | None:
    """读取并反序列化 JSON 缓存。"""
    if not _client:
        return None
    raw = await _client.get(key)
    return json.loads(raw) if raw else None


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """写入 JSON 缓存（默认 TTL 来自 settings）。"""
    if not _client:
        return
    await _client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl or settings.REDIS_DEFAULT_TTL)


async def delete_keys(pattern: str) -> int:
    """按通配符删除 key（例如组织树变更后清理 `org:*`）。"""
    if not _client:
        return 0
    deleted = 0
    async for key in _client.scan_iter(match=pattern):
        deleted += await _client.delete(key)
    return deleted


async def incr_with_ttl(key: str, ttl: int) -> int:
    """自增计数并设置过期时间（用于接口限流、短信验证码次数）。"""
    if not _client:
        return 0
    value = await _client.incr(key)
    if value == 1:
        await _client.expire(key, ttl)
    return int(value)
