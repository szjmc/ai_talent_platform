"""
模块：app/services/report_cache.py
职责：报告缓存层（本地 JSON 文件读写，不依赖 Redis / MySQL）。

标签：[Agent组] [P1]

============================ 实现思路与代码要点 ============================
定位：把「周期报告 / 继任方案」这类「算一次能复用」的结果落本地 JSON，
      避免领导每次点一下都去查库、调 LLM，浪费算力。

设计：
- 缓存落在项目根目录 storage/ 下，一个 key 一个 .json 文件；
- key 规则：
    period:<report_type>          周期报告（weekly / monthly / yearly）
    succession:<post_name>        继任方案（按岗位分开缓存）
- 每个缓存文件内容统一为：
    {
      "generated_at": "2026-11-06 09:50:00",   # 上次生成时间
      "payload": { ... 原始报告 dict ... }
    }

对外方法：
    get(key)            -> dict | None（无缓存返回 None）
    set(key, payload)   -> 写入缓存并返回 generated_at
    touch_time(key)     -> str | None（仅查上次更新时间，不读完整 payload）
    delete(key)         -> 清掉某条缓存（可选）

线程安全：写文件用「先写临时文件再原子替换」，避免并发写坏文件。
=========================================================================
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.config.settings import BASE_DIR

# 缓存目录：项目根目录下 storage/（如不存在则懒创建）
_CACHE_DIR = os.path.join(BASE_DIR, "storage")


def _safe_dir() -> str:
    """确保缓存目录存在，返回目录路径。"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


def _key_to_path(key: str) -> str:
    """把逻辑 key 转成磁盘路径（key 里的非法字符统一替换为下划线）。"""
    safe = "".join(c if c.isalnum() else "_" for c in key)
    return os.path.join(_CACHE_DIR, f"{safe}.json")


def _read_file(path: str) -> dict[str, Any] | None:
    """读缓存文件，返回完整 dict；文件不存在或损坏返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _json_default(obj: Any) -> Any:
    """json 序列化兜底：把数据库查出的 Decimal / datetime 等非标准类型转成可 JSON 的类型。

    传进：json.dump 遇到无法序列化的对象
    传出：可 JSON 化的 Python 原生值（float / str）
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # 其它未知类型：转字符串兜底，避免整个缓存写失败
    return str(obj)


def _write_file(path: str, data: dict[str, Any]) -> None:
    """原子写缓存文件：先写 .tmp 再替换，避免写一半。"""
    _safe_dir()  # 确保缓存目录存在
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=_json_default)
    os.replace(tmp, path)


def get(key: str) -> dict[str, Any] | None:
    """读缓存：返回完整 payload（不含外壳），无缓存返回 None。

    传进：key（如 "period:weekly"）
    传出：payload dict 或 None
    """
    data = _read_file(_key_to_path(key))
    if data is None:
        return None
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else None


def set(key: str, payload: dict[str, Any]) -> str:
    """写缓存：把 payload 包一层（含 generated_at）落盘，返回生成时间。

    传进：key + payload（报告 dict）
    传出：generated_at 字符串（"YYYY-MM-DD HH:MM:SS"）
    """
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {"generated_at": generated_at, "payload": payload}
    _write_file(_key_to_path(key), data)
    return generated_at


def touch_time(key: str) -> str | None:
    """只查某条缓存的「上次更新时间」，不读完整 payload。

    传进：key
    传出：generated_at 字符串或 None
    """
    data = _read_file(_key_to_path(key))
    if data is None:
        return None
    t = data.get("generated_at")
    return str(t) if t else None


def delete(key: str) -> None:
    """删除某条缓存（不存在则忽略）。"""
    try:
        os.remove(_key_to_path(key))
    except FileNotFoundError:
        pass
