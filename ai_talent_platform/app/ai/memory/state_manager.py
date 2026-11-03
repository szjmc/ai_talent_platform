"""
模块：app/ai/memory/state_manager.py
职责：LangGraph 工作流状态持久化。

标签：[架构组] [P2]（被 Agent 组 LangGraph 流程依赖）

============================ 实现思路与代码要点 ============================
1. 目的：工作流（简历解析、匹配跑批）可能跨请求/跨进程，需要断点续跑；
2. 存储：Redis（短期，TTL 24h），未启用 Redis 时退化为进程内字典；
3. API：save(thread_id, state) / load(thread_id) / delete(thread_id) / exists(thread_id)；
4. 与 LangGraph checkpointer 对接，保持一致的状态结构（dict，JSON 可序列化）。

注意：长期状态建议落库（workflow_states 表），当前模型层未定义该表，故只做短期态。
=========================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.config.settings import settings

logger = logging.getLogger(__name__)

_MEMORY_STATES: dict[str, dict[str, Any]] = {}


def to_jsonable(value: Any) -> Any:
    """把状态里的非 JSON 类型转成可序列化结构（datetime -> isoformat）。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value))
    return value


class StateManager:
    """工作流状态存取（Redis 优先，进程内兜底）。"""

    def __init__(self, prefix: str = "wf:state", ttl: int = 86400) -> None:
        self.prefix = prefix
        self.ttl = ttl

    def _key(self, thread_id: str) -> str:
        return f"{self.prefix}:{thread_id}"

    # ---------------------------------------------------------------- API
    async def save(self, thread_id: str, state: dict[str, Any], ttl: int | None = None) -> dict[str, Any]:
        """保存状态（整体覆盖，返回已序列化的状态）。"""
        payload = to_jsonable(state or {})
        _MEMORY_STATES[self._key(thread_id)] = payload
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import set_json

                await set_json(self._key(thread_id), payload, ttl or self.ttl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("工作流状态写入 Redis 失败，已存进程内：%s", exc)
        return payload

    async def load(self, thread_id: str) -> dict[str, Any] | None:
        """读取状态；不存在返回 None。"""
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import get_json

                value = await get_json(self._key(thread_id))
                if value is not None:
                    return dict(value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("工作流状态读取 Redis 失败：%s", exc)
        return _MEMORY_STATES.get(self._key(thread_id))

    async def update(self, thread_id: str, **changes: Any) -> dict[str, Any]:
        """局部更新（读 -> 合并 -> 写）。"""
        current = await self.load(thread_id) or {}
        current.update(to_jsonable(changes))
        return await self.save(thread_id, current)

    async def delete(self, thread_id: str) -> bool:
        """删除状态。"""
        _MEMORY_STATES.pop(self._key(thread_id), None)
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import delete_keys

                await delete_keys(self._key(thread_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("工作流状态删除失败：%s", exc)
        return True

    async def exists(self, thread_id: str) -> bool:
        return await self.load(thread_id) is not None

    def keys(self) -> list[str]:
        """进程内状态的 key 列表（排障用，Redis 模式不完整）。"""
        return sorted(_MEMORY_STATES)

    @staticmethod
    def dumps(state: dict[str, Any]) -> str:
        return json.dumps(to_jsonable(state), ensure_ascii=False)


state_manager = StateManager()


__all__ = ["StateManager", "state_manager", "to_jsonable"]
