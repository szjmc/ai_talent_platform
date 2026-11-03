"""
模块：app/infrastructure/cache/__init__.py
职责：缓存基础设施出口。
"""

from app.infrastructure.cache.redis import (
    close_redis,
    delete_keys,
    get_json,
    get_redis,
    incr_with_ttl,
    init_redis,
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
]
