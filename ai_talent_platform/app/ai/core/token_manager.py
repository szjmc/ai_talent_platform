"""
模块：app/ai/core/token_manager.py
职责：Token 消耗统计与配额控制。

标签：[架构组] [P1]（被 llm_manager 依赖）

============================ 实现思路与代码要点 ============================
1. 统计维度：用户 / 会话 / 任务类型 / 模型 / 日期；
2. 短期计数用 Redis（incr_with_ttl），长期明细落 conversation/audit 表；
3. 配额控制：按用户日限额，超限抛 core.exceptions.AIError；
4. 成本换算：tokens * 单价（MODEL_PRICE，单位：元 / 1K tokens），输出到 analytics。

存储降级：Redis 未启用时自动退化为进程内字典（重启清零），保证本地可跑。
=========================================================================
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta
from typing import Any

from app.config.settings import settings
from app.core.exceptions import AIError

logger = logging.getLogger(__name__)

# 单价（元 / 1K tokens），用于成本估算；未登记的按 0 计，不伪造数字
MODEL_PRICE: dict[str, float] = {
    "qwen-turbo": 0.002,
    "qwen-plus": 0.004,
    "qwen-max": 0.02,
    "gpt-4o-mini": 0.0105,
    "gpt-4o": 0.035,
    "text-embedding-v3": 0.0007,
}

# 每日 token 限额（可用环境变量覆盖）
DEFAULT_DAILY_LIMIT = int(os.getenv("LLM_DAILY_TOKEN_LIMIT", "200000"))


def _today() -> str:
    return date.today().isoformat()


def _seconds_until_midnight() -> int:
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()))


class TokenManager:
    """Token 统计与配额（Redis 优先，进程内兜底）。"""

    def __init__(self, daily_limit: int | None = None) -> None:
        self.daily_limit = int(daily_limit if daily_limit is not None else DEFAULT_DAILY_LIMIT)
        self._memory: dict[str, int] = {}
        self._details: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 写入
    async def record(
        self,
        user_id: int | None,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        purpose: str | None = None,
        conversation_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """记录一次调用消耗，返回统计条目（含成本估算）。"""
        total = int(prompt_tokens or 0) + int(completion_tokens or 0)
        entry = {
            "user_id": user_id,
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": total,
            "purpose": purpose,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
            "date": _today(),
            "cost": self.estimate_cost(model, prompt_tokens, completion_tokens),
        }

        key = self._key(user_id)
        redis_value: int | None = None
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import incr_with_ttl

                redis_value = await incr_with_ttl(key, _seconds_until_midnight())
            except Exception as exc:  # noqa: BLE001 - 缓存异常不阻塞主流程
                logger.warning("Redis token 计数失败，退化进程内统计：%s", exc)

        with self._lock:
            self._memory[key] = self._memory.get(key, 0) + total
            self._details.append(entry)
            if len(self._details) > 1000:  # 进程内明细只留最近 1000 条
                self._details = self._details[-1000:]
            used = redis_value if redis_value is not None else self._memory[key]

        entry["used_today"] = used
        logger.info(
            "token 消耗：user=%s model=%s total=%s purpose=%s 今日累计=%s",
            user_id,
            model,
            total,
            purpose,
            used,
        )
        return entry

    # ---------------------------------------------------------------- 查询
    async def usage_today(self, user_id: int | None) -> int:
        """查询用户今日已用 token 数。"""
        key = self._key(user_id)
        if settings.REDIS_ENABLED:
            try:
                from app.infrastructure.cache.redis import get_json

                value = await get_json(key)
                if value is not None:
                    return int(value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis 读取 token 用量失败：%s", exc)
        return int(self._memory.get(key, 0))

    async def check_quota(self, user_id: int | None, *, need: int = 0) -> None:
        """配额校验：超限抛 AIError（上层直接返回给用户）。"""
        used = await self.usage_today(user_id)
        if used + max(0, need) > self.daily_limit:
            raise AIError(
                f"今日 AI 用量已达上限（{used}/{self.daily_limit} tokens），请明天再试或联系管理员",
                data={"user_id": user_id, "used": used, "limit": self.daily_limit},
            )

    async def usage_summary(self, user_id: int | None) -> dict[str, Any]:
        """用户用量概览（含成本与限额）。"""
        used = await self.usage_today(user_id)
        records = [item for item in self._details if item.get("user_id") == user_id]
        by_model: dict[str, int] = {}
        cost = 0.0
        for item in records:
            by_model[item["model"]] = by_model.get(item["model"], 0) + item["total_tokens"]
            cost += float(item.get("cost") or 0)
        return {
            "user_id": user_id,
            "date": _today(),
            "used": used,
            "limit": self.daily_limit,
            "remaining": max(0, self.daily_limit - used),
            "by_model": by_model,
            "cost_estimate": round(cost, 4),
            "records_in_memory": len(records),
        }

    # ---------------------------------------------------------------- 工具
    def estimate_cost(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> float:
        """按 MODEL_PRICE 估算成本（元）；未登记模型按 0 计，不编造。"""
        price = MODEL_PRICE.get(model)
        if price is None:
            return 0.0
        return round((int(prompt_tokens or 0) + int(completion_tokens or 0)) / 1000 * price, 6)

    @staticmethod
    def _key(user_id: int | None) -> str:
        return f"quota:token:{user_id if user_id is not None else 'anonymous'}:{_today()}"


token_manager = TokenManager()


__all__ = ["MODEL_PRICE", "TokenManager", "token_manager"]
