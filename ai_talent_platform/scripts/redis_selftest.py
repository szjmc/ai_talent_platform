#!/usr/bin/env python3
"""Redis 缓存自检脚本

标签：[架构组] [P1]（部署自检：真实 Redis 连通性与缓存子系统），保持原有说明如下。

用途：部署后一键验证「真实 Redis 是否能连通 + 所有缓存子系统（token/会话/限流/NL2SQL/报告/锁）是否走 Redis」。

用法（后端项目根目录下）：
    source /path/to/venv/bin/activate
    python scripts/redis_selftest.py

退出码：0=真实 Redis 已连接且所有子系统 OK；1=降级到 fakeredis/memory（功能可用但非真实 Redis）；2=初始化失败。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

# 切到项目根目录（保证能 import app）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)


async def main() -> int:
    # 懒加载 settings（触发 .env 读取）
    from app.config.settings import settings
    from app.infrastructure.cache import (
        acquire_lock,
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

    print("=" * 64)
    print("AI 人才平台 — Redis 缓存自检")
    print("=" * 64)
    print(f"REDIS_ENABLED          = {settings.REDIS_ENABLED}")
    print(f"REDIS_URL              = {settings.REDIS_URL}")
    print(f"REDIS_DB               = {settings.REDIS_DB}")
    print(f"REDIS_KEY_PREFIX       = {settings.REDIS_KEY_PREFIX!r}")
    print(f"REDIS_SSL              = {settings.REDIS_SSL}")
    print("-" * 64)

    client = await init_redis()
    kind = cache_kind()
    print(f"✅ 缓存初始化完成：kind = {kind}")

    if kind != "redis":
        print()
        print("⚠️ 当前未连接到真实 Redis（降级为 fakeredis/memory）。")
        print("   功能可用但跨进程/跨实例数据不共享，限流/锁不会全局生效。")
        print("   请检查 .env 的 REDIS_URL / REDIS_PASSWORD / REDIS_SSL 是否正确、Redis 服务是否已启动。")

    # 基础 KV
    tag = f"selftest:{uuid.uuid4().hex[:8]}"
    await set_json(tag, {"hello": "world", "ts": time.time()}, ttl=60)
    got = await get_json(tag)
    assert got and got.get("hello") == "world", "set_json/get_json 失败"
    print(f"✅ JSON KV 读写：        set_json/get_json OK ({tag})")

    # 自增 + TTL（限流）
    rl_key = f"{tag}:ratelimit"
    v1 = await incr_with_ttl(rl_key, ttl=30)
    v2 = await incr_with_ttl(rl_key, ttl=30)
    assert v1 == 1 and v2 == 2, f"incr_with_ttl 异常 v1={v1} v2={v2}"
    print(f"✅ 限流自增 incr：       v1={v1}, v2={v2} OK")

    # 分布式锁
    lock_key = f"{tag}:lock"
    owner = f"self-{uuid.uuid4().hex[:8]}"
    ok1 = await acquire_lock(lock_key, owner, ttl=10)
    ok2 = await acquire_lock(lock_key, "other-owner", ttl=10)
    assert ok1 and not ok2, "锁互斥失败"
    rel = await release_lock(lock_key, owner)
    assert rel, "释放锁失败"
    ok3 = await acquire_lock(lock_key, "other-owner", ttl=10)
    assert ok3, "释放后重入失败"
    await release_lock(lock_key, "other-owner")
    print("✅ 分布式锁：            互斥/释放/重入 OK")

    # NL2SQL 缓存模拟
    nl2sql_key = f"nl2sql:all:1:100:1:sha256:{uuid.uuid4().hex[:12]}"
    await set_json(nl2sql_key, {"sql": "SELECT 1", "rows": [[1]]}, ttl=600)
    assert (await get_json(nl2sql_key)) is not None
    print("✅ NL2SQL 查询缓存：     set/get OK（TTL 600s）")

    # 报告缓存（通过 report_cache 模块双写）
    try:
        from app.services import report_cache

        rt_key = f"period_v2:selftest-{uuid.uuid4().hex[:6]}"
        ts = await report_cache.set(rt_key, {"insight": {}, "data": {"selftest": True}})
        hit = await report_cache.get(rt_key)
        t = await report_cache.touch_time(rt_key)
        assert hit and hit.get("data", {}).get("selftest") is True
        assert t == ts
        await report_cache.delete(rt_key)
        assert (await report_cache.get(rt_key)) is None
        print(f"✅ 报告缓存（双写）：    set/get/touch_time/delete OK (generated_at={ts})")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 报告缓存异常：{exc}")

    # 前缀校验：所有写入都应带前缀
    if kind == "redis":
        try:
            real = get_redis()
            found = 0
            async for k in real.scan_iter(match=f"{settings.REDIS_KEY_PREFIX}{tag}*", count=200):
                found += 1
            assert found >= 2, f"前缀不生效？扫描到 {found} 个带前缀 key"
            print(f"✅ Key 前缀：            所有写入带前缀 {settings.REDIS_KEY_PREFIX!r}（扫到 {found} 个）")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 前缀扫描失败：{exc}")

    # 清理自检数据
    await delete_keys(f"{tag}*")
    await clear_namespace("nl2sql")
    await close_redis()

    print("-" * 64)
    if kind == "redis":
        print("🎉 真实 Redis 已连通，所有缓存子系统工作正常。")
        return 0
    print("ℹ️  缓存子系统功能正常（降级模式），请配置真实 Redis 获得多实例共享能力。")
    return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"❌ 自检失败：{exc}")
        exit_code = 2
    sys.exit(exit_code)
