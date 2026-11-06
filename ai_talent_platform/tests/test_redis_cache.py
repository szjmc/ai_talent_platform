"""模块：tests/test_redis_cache.py
职责：Redis 基础设施（app/infrastructure/cache/redis.py）的离线单元测试。

标签：[架构组] [P1]

覆盖点：
1. 降级路径：未连接 Redis 时所有封装不抛异常、返回安全默认值；
2. JSON 封装：set_json/get_json 往返（含中文）、TTL 落库、坏 JSON 读取返回 None；
3. delete_keys 通配符批量删除；
4. incr_with_ttl：首配过期、计数递增（限流窗口语义）；
5. 分布式锁：SET NX 互斥、owner 不匹配不能释放（Lua 原子比对）、释放后可重抢；
6. init_redis：ping 失败必须清空单例（不留坏客户端被反复复用）；
7. close_redis 幂等。

全部用 fakeredis 模拟传输，不依赖真实 Redis。
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from app.config import settings
from app.infrastructure.cache import redis as cache


class _HolderStub:
    """适配 LazyHolder 的测试替身（与 test_analytics_snapshot 同一写法）。"""

    def __init__(self, client):
        self._client = client
        self.closed = False

    def get(self):
        return self._client

    def peek(self):
        return self._client

    async def aclose(self):
        self._client = None
        self.closed = True


@pytest.fixture
def fake_redis():
    """decode_responses=True 与真实客户端配置（redis.py 第 47 行）保持一致。"""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _inject(monkeypatch, client):
    holder = _HolderStub(client)
    monkeypatch.setattr(cache, "_client_holder", holder)
    return holder


# ---------------------------------------------------------------- 降级路径
async def test_no_client_degrades_silently(monkeypatch):
    _inject(monkeypatch, None)
    assert await cache.get_json("k") is None
    await cache.set_json("k", {"a": 1})          # 不抛异常
    assert await cache.delete_keys("k*") == 0
    assert await cache.incr_with_ttl("k", 60) == 0
    assert await cache.acquire_lock("k", "owner", 60) is False
    assert await cache.release_lock("k", "owner") is False


# ---------------------------------------------------------------- JSON 封装
async def test_json_roundtrip_with_chinese_and_ttl(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    payload = {"name": "张三", "skills": ["Java", "Python"], "n": 3}
    await cache.set_json("talent:1", payload, ttl=120)

    assert await cache.get_json("talent:1") == payload
    ttl = await fake_redis.ttl("talent:1")
    assert 60 < ttl <= 120                       # TTL 真实生效


async def test_default_ttl_applied_when_none(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    await cache.set_json("k", 1)                 # 未传 ttl → REDIS_DEFAULT_TTL
    ttl = await fake_redis.ttl("k")
    assert 0 < ttl <= settings.REDIS_DEFAULT_TTL


async def test_corrupt_json_returns_none(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    await fake_redis.set("bad", "{not-json")
    assert await cache.get_json("bad") is None


async def test_set_json_serializes_datetime_like_values(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    # default=str 兜底：非原生可序列化对象走字符串化，不抛 TypeError
    from datetime import datetime
    await cache.set_json("dt", {"t": datetime(2026, 1, 1)})
    assert json.loads(await fake_redis.get("dt"))["t"].startswith("2026-01-01")


# ---------------------------------------------------------------- delete_keys
async def test_delete_keys_wildcard(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    for i in range(5):
        await cache.set_json(f"org:{i}", i)
    await cache.set_json("user:1", 1)

    deleted = await cache.delete_keys("org:*")
    assert deleted == 5
    assert await cache.get_json("user:1") == 1   # 不误删其他前缀


# ---------------------------------------------------------------- incr_with_ttl
async def test_incr_with_ttl_rate_limit_semantics(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    assert await cache.incr_with_ttl("rl:ip1", 60) == 1   # 首次：设过期
    assert await cache.incr_with_ttl("rl:ip1", 60) == 2   # 递增：不重置过期
    first_ttl = await fake_redis.ttl("rl:ip1")
    assert 0 < first_ttl <= 60
    assert await cache.incr_with_ttl("rl:ip2", 60) == 1   # 不同 key 独立计数


# ---------------------------------------------------------------- 分布式锁
async def test_lock_is_mutually_exclusive(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    assert await cache.acquire_lock("lock:sync", "A", 60) is True
    assert await cache.acquire_lock("lock:sync", "B", 60) is False   # 已被 A 持有


async def test_release_lock_requires_matching_owner(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    await cache.acquire_lock("lock:sync", "A", 60)

    assert await cache.release_lock("lock:sync", "B") is False       # 他人不能释放
    assert await cache.release_lock("lock:sync", "A") is True        # 本人可释放
    assert await cache.acquire_lock("lock:sync", "B", 60) is True    # 释放后可重抢


async def test_lock_expires_by_ttl(monkeypatch, fake_redis):
    _inject(monkeypatch, fake_redis)
    await cache.acquire_lock("lock:ttl", "A", 1)
    ttl = await fake_redis.ttl("lock:ttl")
    assert 0 < ttl <= 1                       # 防死锁的 TTL 真实落库


# ---------------------------------------------------------------- 生命周期
class _BrokenClient:
    """ping 必炸的客户端，模拟 Redis 宕机。"""

    async def ping(self):
        raise ConnectionError("redis down")


async def test_init_redis_ping_failure_clears_singleton(monkeypatch):
    holder = _HolderStub(_BrokenClient())
    monkeypatch.setattr(cache, "_client_holder", holder)
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)

    assert await cache.init_redis() is None
    assert holder.peek() is None              # 坏客户端被丢弃，不会反复复用
    assert holder.closed is True


async def test_init_redis_disabled_returns_none_without_client(monkeypatch):
    holder = _HolderStub(None)
    monkeypatch.setattr(cache, "_client_holder", holder)
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)

    assert await cache.init_redis() is None
    assert holder.peek() is None


async def test_close_redis_is_idempotent(monkeypatch, fake_redis):
    holder = _inject(monkeypatch, fake_redis)
    await cache.close_redis()
    await cache.close_redis()                 # 第二次无客户端也不抛异常
    assert holder.peek() is None
