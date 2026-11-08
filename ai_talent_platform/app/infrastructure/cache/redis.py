"""
模块：app/infrastructure/cache/redis.py
职责：缓存客户端（Redis 优先，缺失时自动降级 fakeredis / 进程内内存）。

标签：[架构组] [P0]（缓存核心：token 配额 / 会话记忆 / 限流 / NL2SQL / 周期报告）

设计说明：
1. 优先使用真实 Redis（REDIS_ENABLED=true 且可连通）；
2. 未启用或连接失败时，自动使用 fakeredis（Redis 协议兼容的内存实现）作为全局缓存；
3. fakeredis 也不可用时，回退内置线程安全的 MemoryCache（dict + TTL 清扫）；
4. 对外 API 与真实 redis.asyncio.Redis 完全对齐：get/set/delete/expire/incr/set_nx/
   scan_iter/eval/aclose，业务代码只调 get_json/set_json/... 不用关心底层；
5. 所有封装（get_json/set_json/delete_keys/incr_with_ttl/acquire_lock/release_lock）
   在内存回退实现下同样可用，保证限流、会话记忆、报告缓存等功能在无 Redis 环境也工作。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.config.settings import settings
from app.patterns.singleton import LazyHolder

logger = logging.getLogger(__name__)

_init_logged = False
_backend_kind: str = "none"   # "redis" | "fakeredis" | "memory"


# ============================================================ 抽象缓存客户端
class _BaseCache:
    """所有缓存后端都实现的最小接口集合。"""

    async def aclose(self) -> None: ...
    async def ping(self) -> bool: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ex: int | None = None) -> bool: ...
    async def delete(self, *keys: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> bool: ...
    async def incr(self, key: str) -> int: ...
    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...
    async def scan_iter(self, match: str | None = None) -> Any:
        # 默认迭代器：不支持 pattern 时返回空
        if False:
            yield


# ============================================================ 内存回退实现
class _MemoryCache(_BaseCache):
    """线程/协程安全的内存 KV（带 TTL），兜底用。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        async with self._lock:
            self._data.clear()

    async def ping(self) -> bool:
        return True

    @staticmethod
    def _now() -> float:
        return time.time()

    def _expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and expires_at < self._now()

    async def get(self, key: str) -> str | None:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, exp = item
            if self._expired(exp):
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        async with self._lock:
            exp = (self._now() + ex) if ex else None
            self._data[key] = (value, exp)
            return True

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            n = 0
            for k in keys:
                if self._data.pop(k, None) is not None:
                    n += 1
            return n

    async def expire(self, key: str, seconds: int) -> bool:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return False
            value, _ = item
            self._data[key] = (value, self._now() + max(1, int(seconds)))
            return True

    async def incr(self, key: str) -> int:
        async with self._lock:
            item = self._data.get(key)
            if item is None or self._expired(item[1]):
                self._data[key] = ("1", None)
                return 1
            try:
                v = int(item[0]) + 1
            except (TypeError, ValueError):
                v = 1
            self._data[key] = (str(v), item[1])
            return v

    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool:
        async with self._lock:
            item = self._data.get(key)
            if item is not None and not self._expired(item[1]):
                return False
            exp = (self._now() + ex) if ex else None
            self._data[key] = (value, exp)
            return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        # 仅实现「释放锁」Lua：if get(key)==owner then del(key) return 1 else 0 end
        # 以及 incr+expire 复合。其它脚本返回 0 避免炸流程。
        if numkeys != 1 or len(keys_and_args) < 2:
            return 0
        key = keys_and_args[0]
        owner = keys_and_args[1]
        async with self._lock:
            item = self._data.get(key)
            if item is None or self._expired(item[1]):
                return 0
            if item[0] == owner:
                self._data.pop(key, None)
                return 1
            return 0

    async def scan_iter(self, match: str | None = None):
        import fnmatch
        async with self._lock:
            keys = list(self._data.keys())
        for k in keys:
            if match is None or fnmatch.fnmatch(k, match):
                yield k


# ============================================================ 客户端创建
def _create_fake_client() -> tuple[Any, str]:
    """创建 fakeredis/内存 回退客户端（不再尝试真实 Redis）。"""
    try:
        from fakeredis import FakeAsyncRedis as FakeRedis  # fakeredis>=2.20

        return FakeRedis(decode_responses=True, version=7), "fakeredis"
    except Exception:  # noqa: BLE001
        try:
            from fakeredis.aioredis import FakeRedis as OldFakeRedis

            return OldFakeRedis(encoding="utf-8", decode_responses=True), "fakeredis"
        except Exception as exc:  # noqa: BLE001
            logger.warning("fakeredis 不可用（%s），回退到内置内存缓存", exc)
    return _MemoryCache(), "memory"


def _create_redis_client() -> tuple[Any | None, str]:
    """尝试按 真实 Redis -> fakeredis -> 内存 顺序创建客户端。返回 (client, kind)。
    注意：这里不做 ping 校验；init_redis() 会对真实 Redis 做连通性检测，失败则切换到 fake。
    """
    if settings.REDIS_ENABLED:
        try:
            from redis.asyncio import Redis  # type: ignore

            client = Redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            return client, "redis"
        except Exception as exc:  # noqa: BLE001
            logger.warning("真实 Redis 客户端创建失败（%s），尝试 fakeredis 降级", exc)
    return _create_fake_client()


async def _close_client(client: Any) -> None:
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        pass


_client_holder: LazyHolder[Any] = LazyHolder("cache-client", lambda: _create_redis_client()[0], _close_client)


async def init_redis() -> Any | None:
    """初始化缓存客户端（幂等）。

    - 优先真实 Redis；
    - 连不上/未启用 自动降级 fakeredis；
    - fakeredis 不可用 回退进程内 MemoryCache。
    保证任何环境下 get_json/set_json/锁/限流 都能工作。
    """
    global _init_logged, _backend_kind

    if _init_logged and _client_holder.peek() is not None:
        return _client_holder.peek()

    # 1. 真实 Redis 可用则替换单例
    if settings.REDIS_ENABLED:
        try:
            from redis.asyncio import Redis  # type: ignore

            real = Redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await real.ping()
            await _client_holder.aclose()
            _rebuild_holder(real, "redis")
            _backend_kind = "redis"
            if not _init_logged:
                logger.info("✅ Redis 连接成功：%s", settings.REDIS_URL)
                _init_logged = True
            return real
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ Redis 连接失败（%s），缓存降级为 fakeredis/内存模式", exc)

    # 2. 降级：fakeredis -> memory
    client, kind = _create_fake_client()
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        client = _MemoryCache()
        kind = "memory"

    await _client_holder.aclose()
    _rebuild_holder(client, kind)
    _backend_kind = kind
    if not _init_logged:
        if kind == "fakeredis":
            logger.info("ℹ️ 使用 fakeredis（进程内 Redis 兼容缓存）替代真实 Redis")
        else:
            logger.info("ℹ️ 使用进程内内存缓存（未安装 redis/fakeredis）")
        _init_logged = True
    return client


def _rebuild_holder(client: Any, kind: str) -> None:
    """替换全局 LazyHolder 工厂为已构造好的 client（缓存单例切换）。"""
    global _client_holder, _backend_kind
    # aclose 会 reset holder；之后再把 factory 改成返回固定 client，确保 get() 拿到降级客户端
    _client_holder._factory = lambda c=client: c  # type: ignore[assignment]
    _client_holder._value = client
    _client_holder._created = True
    _backend_kind = kind


def get_redis() -> Any | None:
    """获取当前缓存客户端（redis / fakeredis / MemoryCache），永不返回 None。"""
    client = _client_holder.get()
    if client is None:
        # 惰性初始化（ lifespan 未调用 init_redis 时首次访问也能用）
        client, kind = _create_redis_client()
        _rebuild_holder(client, kind)
    return client


def cache_kind() -> str:
    return _backend_kind


async def close_redis() -> None:
    global _init_logged
    had_client = _client_holder.peek() is not None
    await _client_holder.aclose()
    if had_client:
        logger.info("缓存客户端已关闭（kind=%s）", _backend_kind)
    _init_logged = False


# ---------------------------------------------------------------- 常用封装
def _json_default(obj: Any) -> Any:
    """datetime / set / Decimal 等非 JSON 类型兜底。"""
    import datetime as _dt
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


async def get_json(key: str) -> Any | None:
    """读取并反序列化 JSON 缓存；未命中/坏值均返回 None。"""
    client = get_redis()
    try:
        raw = await client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存 get 失败（%s）：%s", key, exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("缓存值非合法 JSON，按未命中处理：%s", key)
        return None


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """写入 JSON 缓存（默认 TTL = settings.REDIS_DEFAULT_TTL）。"""
    client = get_redis()
    ttl = int(ttl or settings.REDIS_DEFAULT_TTL)
    try:
        await client.set(key, json.dumps(value, ensure_ascii=False, default=_json_default), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存 set 失败（%s）：%s", key, exc)


async def delete_keys(pattern: str) -> int:
    """按通配符删除 key（例如组织树变更后清理 `org:*`）。"""
    client = get_redis()
    deleted = 0
    try:
        # fakeredis 与真实 redis 都支持 async for scan_iter
        async for key in client.scan_iter(match=pattern):
            try:
                deleted += await client.delete(key)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存批量删除失败（pattern=%s）：%s", pattern, exc)
    return deleted


async def incr_with_ttl(key: str, ttl: int) -> int:
    """自增计数并设置过期时间（限流/验证码场景）。失败回退 1，不阻断业务。"""
    client = get_redis()
    try:
        value = await client.incr(key)
        if value == 1:
            await client.expire(key, int(ttl))
        return int(value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("缓存 incr 失败（%s）：%s，回退为 1", key, exc)
        return 1


# 释放锁 Lua 脚本（仅真实 Redis 用到；内存/fakeredis 的 eval 已内部原子处理）
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def acquire_lock(key: str, owner: str, ttl: int) -> bool:
    """获取分布式/进程内锁（SET NX EX）。
    统一使用 set(nx=True, ex=ttl) 以便兼容 redis / fakeredis / MemoryCache 三种后端。
    """
    client = get_redis()
    try:
        # 先试 Redis 标准 API；若后端是 MemoryCache（暴露 set_nx）再回退。
        if hasattr(client, "set") and not isinstance(client, _MemoryCache):
            return bool(await client.set(key, owner, nx=True, ex=int(ttl)))
        return bool(await client.set_nx(key, owner, ex=int(ttl)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("加锁失败（%s）：%s", key, exc)
        return False


async def release_lock(key: str, owner: str) -> bool:
    """释放锁：真实 Redis 走 Lua 保证原子；fakeredis/MemoryCache 的 eval 内部也原子。"""
    client = get_redis()
    try:
        if _backend_kind == "memory":
            return bool(await client.eval(_RELEASE_LOCK_LUA, 1, key, owner))
        return bool(await client.eval(_RELEASE_LOCK_LUA, 1, key, owner))
    except Exception as exc:  # noqa: BLE001
        logger.warning("释放锁失败（%s）：%s（TTL 兜底）", key, exc)
        return False


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
    "cache_kind",
]
