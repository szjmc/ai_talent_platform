"""
模块：app/infrastructure/cache/__init__.py
职责：缓存基础设施出口。

标签：[架构组] [P1]（缓存出口）
"""

from app.infrastructure.cache.redis import (
    acquire_lock,
    cache_get_or_set,
    cache_kind,
    clear_namespace,
    close_redis,
    delete_keys,
    get_json,
    get_redis,
    incr_with_ttl,
    init_redis,
    release_lock,
    set_json,
)

__all__ = [
    "init_redis",
    "get_redis",
    "close_redis",
    "get_json",
    "set_json",
    "delete_keys",
    "incr_with_ttl",
    "acquire_lock",
    "release_lock",
    "cache_get_or_set",
    "clear_namespace",
    "cache_kind",
]
