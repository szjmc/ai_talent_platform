"""
模块：app/services/report_cache.py
职责：报告缓存层（Redis 为主、本地 JSON 文件为辅，双写）。

标签：[Agent组] [P0]

============================ 实现思路与代码要点 ============================
定位：把「周期报告 / 继任方案」这类「算一次能复用」的结果缓存起来，
      避免领导每次点一下都去查库、调 LLM，浪费算力；同时让多 worker
      / 多实例部署时通过 Redis 共享缓存，避免实例 A 生成完实例 B 还要
      再算一遍。

设计：
- **一级缓存 Redis**：key 统一加前缀 `report:`，TTL 默认 24 小时；
  真实 Redis 可用时走真实 Redis，否则自动降级 fakeredis/内存（和全局缓存一致）。
- **二级缓存 本地文件**：落地 storage/*.json 作为持久备份（Redis 被清空
  时仍可恢复上一份结果），用临时文件 + 原子 rename 防止并发写坏。
- key 规则：
    period_v2:<report_type>       周期报告（weekly / monthly / yearly）
    succession:<post_name>        继任方案（按岗位分开缓存）
- 缓存内容统一为：
    {
      "generated_at": "2026-11-06 09:50:00",
      "payload": { ... 原始报告 dict ... }
    }

对外方法（全部 async，便于跨实例/Redis 读写）：
    async get(key)              -> dict | None
    async set(key, payload, ttl)-> str（generated_at）
    async touch_time(key)       -> str | None
    async delete(key)           -> None
    invalidate(report_type)     -> 协程：删除周期报告缓存（Redis+文件）
=========================================================================
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.config.settings import BASE_DIR

logger = logging.getLogger(__name__)

# 缓存目录：项目根目录下 storage/（如不存在则懒创建）
_CACHE_DIR = os.path.join(BASE_DIR, "storage")

# Redis key 前缀 & 默认 TTL（周期报告 24 小时；继任方案 12 小时）
_REDIS_PREFIX = "report"
_PERIOD_TTL = 24 * 3600
_SUCC_TTL = 12 * 3600


def _safe_dir() -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


def _key_to_path(key: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in key)
    return os.path.join(_CACHE_DIR, f"{safe}.json")


def _redis_key(key: str) -> str:
    return f"{_REDIS_PREFIX}:{key}"


def _ttl_for(key: str) -> int:
    if key.startswith("succession:"):
        return _SUCC_TTL
    return _PERIOD_TTL


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


# ------------------------------------------------------------ 本地文件操作
def _read_file(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_file(path: str, data: dict[str, Any]) -> None:
    _safe_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=_json_default)
    os.replace(tmp, path)


def _delete_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# ------------------------------------------------------------ 对外 async API
async def get(key: str) -> dict[str, Any] | None:
    """读取缓存：优先 Redis；未命中回退本地文件并回填 Redis。"""
    from app.infrastructure.cache import get_json, set_json

    # 1) 先查 Redis
    try:
        cached = await get_json(_redis_key(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告缓存 Redis 读取失败（%s）：%s", key, exc)
        cached = None

    if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
        return cached["payload"]

    # 2) Redis 未命中，回退文件
    data = _read_file(_key_to_path(key))
    if data is None:
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return None

    # 3) 回填 Redis（不 await 结果，不阻塞返回）
    try:
        asyncio.create_task(set_json(_redis_key(key), data, ttl=_ttl_for(key)))
    except RuntimeError:
        # 没有运行中的事件循环时忽略
        pass
    return payload


async def set(key: str, payload: dict[str, Any], ttl: int | None = None) -> str:
    """写入缓存（Redis + 本地文件双写），返回 generated_at。"""
    from app.infrastructure.cache import set_json as _redis_set

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {"generated_at": generated_at, "payload": payload}

    # 1) 本地文件（持久备份）
    try:
        _write_file(_key_to_path(key), data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告缓存写入文件失败（%s）：%s", key, exc)

    # 2) Redis（跨实例共享）
    try:
        await _redis_set(_redis_key(key), data, ttl=ttl or _ttl_for(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告缓存写入 Redis 失败（%s）：%s", key, exc)

    return generated_at


async def touch_time(key: str) -> str | None:
    """只查某条缓存的「上次更新时间」，优先 Redis。"""
    from app.infrastructure.cache import get_json

    try:
        cached = await get_json(_redis_key(key))
        if isinstance(cached, dict) and cached.get("generated_at"):
            return str(cached["generated_at"])
    except Exception:  # noqa: BLE001
        pass
    data = _read_file(_key_to_path(key))
    if data is None:
        return None
    t = data.get("generated_at")
    return str(t) if t else None


async def delete(key: str) -> None:
    """删除某条缓存（Redis + 文件）。"""
    from app.infrastructure.cache import delete_keys as _redis_del

    _delete_file(_key_to_path(key))
    try:
        await _redis_del(_redis_key(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告缓存 Redis 删除失败（%s）：%s", key, exc)


async def invalidate_report(report_type: str | None = None) -> int:
    """失效周期报告缓存。report_type=None 时清空所有 period_v2:*；否则清单项。

    返回删除的 key 数量（仅 Redis；文件始终同步删除）。
    """
    from app.infrastructure.cache import delete_keys as _redis_del

    if report_type:
        key = f"period_v2:{report_type}"
        await delete(key)
        return 1

    # 清空所有 period_v2:* 与 succession:*
    # 文件：清 storage 下相关文件
    _safe_dir()
    removed_files = 0
    for fn in os.listdir(_CACHE_DIR):
        if fn.startswith("period_v2_") or fn.startswith("succession_"):
            try:
                os.remove(os.path.join(_CACHE_DIR, fn))
                removed_files += 1
            except OSError:
                pass

    n = 0
    for pat in (f"{_REDIS_PREFIX}:period_v2:*", f"{_REDIS_PREFIX}:succession:*"):
        try:
            n += await _redis_del(pat)
        except Exception as exc:  # noqa: BLE001
            logger.warning("报告缓存 Redis 批量删除失败（%s）：%s", pat, exc)
    return n
