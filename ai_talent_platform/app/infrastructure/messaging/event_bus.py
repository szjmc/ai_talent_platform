"""
模块：app/infrastructure/messaging/event_bus.py
职责：进程内事件总线（解耦「业务主流程」与「副作用」）。

标签：[架构组] [P2]

============================ 实现思路与代码要点 ============================
使用场景：
- 人才创建成功 -> 触发画像生成、向量化、审计
- 简历解析完成 -> 触发人才档案更新、通知
- 测评提交 -> 触发 AI 研判、培训推荐

接口设计：
    subscribe(event_name, handler)          注册处理器
    publish(event_name, payload)            发布事件（异步、非阻塞）
    publish_background(event_name, payload) 在当前请求结束后执行（返回 BackgroundTask 协程）

实现要点：
- 处理器异常必须被捕获并记录，绝不能影响主流程；
- 单进程内实现（asyncio.create_task）；多实例场景后续替换为 Redis Stream / Kafka；
- 事件名统一常量：EVENT_TALENT_CREATED / EVENT_RESUME_PARSED / ...
=========================================================================
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 事件常量
EVENT_TALENT_CREATED = "talent.created"
EVENT_TALENT_UPDATED = "talent.updated"
EVENT_TALENT_DELETED = "talent.deleted"
EVENT_RESUME_UPLOADED = "resume.uploaded"
EVENT_RESUME_PARSED = "resume.parsed"
EVENT_ASSESSMENT_SUBMITTED = "assessment.submitted"
EVENT_ASSESSMENT_SCORED = "assessment.scored"
EVENT_MATCHING_FINISHED = "matching.finished"
EVENT_TRAINING_PLAN_CREATED = "training.plan_created"
EVENT_KNOWLEDGE_INDEXED = "knowledge.indexed"
EVENT_AI_CALL_FINISHED = "ai.call_finished"
EVENT_AUDIT_LOG = "audit.log"

ALL_EVENTS: tuple[str, ...] = (
    EVENT_TALENT_CREATED,
    EVENT_TALENT_UPDATED,
    EVENT_TALENT_DELETED,
    EVENT_RESUME_UPLOADED,
    EVENT_RESUME_PARSED,
    EVENT_ASSESSMENT_SUBMITTED,
    EVENT_ASSESSMENT_SCORED,
    EVENT_MATCHING_FINISHED,
    EVENT_TRAINING_PLAN_CREATED,
    EVENT_KNOWLEDGE_INDEXED,
    EVENT_AI_CALL_FINISHED,
    EVENT_AUDIT_LOG,
)

Handler = Callable[..., Union[None, Awaitable[None]]]


class EventBus:
    """进程内事件总线（发布订阅 + 后台任务）。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._stats: dict[str, int] = {"published": 0, "handled": 0, "failed": 0}

    # ---------------------------------------------------------------- 订阅
    def subscribe(self, event_name: str, handler: Handler) -> None:
        """注册事件处理器（同事件可注册多个，按注册顺序执行）。"""
        self._handlers.setdefault(event_name, []).append(handler)
        logger.debug("事件订阅：%s -> %s", event_name, getattr(handler, "__qualname__", handler))

    def unsubscribe(self, event_name: str, handler: Handler) -> bool:
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)
            return True
        return False

    def clear(self, event_name: str | None = None) -> None:
        if event_name:
            self._handlers.pop(event_name, None)
        else:
            self._handlers.clear()

    def handlers(self, event_name: str) -> list[Handler]:
        return list(self._handlers.get(event_name, []))

    # ---------------------------------------------------------------- 发布
    def publish(self, event_name: str, payload: Any = None) -> asyncio.Task | None:
        """发布事件：异步非阻塞执行全部处理器（返回 Task，便于测试等待）。"""
        handlers = list(self._handlers.get(event_name, []))
        self._stats["published"] += 1
        if not handlers:
            logger.debug("事件 %s 无订阅者，忽略", event_name)
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 无事件循环（同步脚本）
            logger.warning("当前无事件循环，事件 %s 未派发", event_name)
            return None
        task = loop.create_task(self._dispatch(event_name, handlers, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def publish_await(self, event_name: str, payload: Any = None) -> None:
        """同步等待所有处理器执行完（测试 / 需要强一致副作用的场景）。"""
        handlers = list(self._handlers.get(event_name, []))
        self._stats["published"] += 1
        await self._dispatch(event_name, handlers, payload)

    async def publish_background(self, event_name: str, payload: Any = None) -> None:
        """请求结束后执行：await event_bus.publish_background(...) 即可。"""
        handlers = list(self._handlers.get(event_name, []))
        self._stats["published"] += 1
        await self._dispatch(event_name, handlers, payload)

    async def drain(self, timeout: float = 5.0) -> None:
        """等待已派发的任务完成（优雅停机 / 测试收尾）。"""
        if not self._tasks:
            return
        await asyncio.wait(list(self._tasks), timeout=timeout)

    # ---------------------------------------------------------------- 内部
    async def _dispatch(self, event_name: str, handlers: list[Handler], payload: Any) -> None:
        for handler in handlers:
            try:
                result = handler(payload)
                if hasattr(result, "__await__"):
                    await result
                self._stats["handled"] += 1
            except Exception as exc:  # noqa: BLE001 - 处理器异常绝不冒泡到主流程
                self._stats["failed"] += 1
                logger.exception("事件 %s 的处理器 %s 执行失败：%s", event_name, handler, exc)

    # ---------------------------------------------------------------- 运维
    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "events": sorted(self._handlers),
            "handlers": {name: len(items) for name, items in self._handlers.items()},
            "pending_tasks": len(self._tasks),
        }


event_bus = EventBus()


def subscribe(event_name: str) -> Callable[[Handler], Handler]:
    """装饰器式订阅：@subscribe(EVENT_TALENT_CREATED)。"""

    def decorator(handler: Handler) -> Handler:
        event_bus.subscribe(event_name, handler)
        return handler

    return decorator


__all__ = [
    "ALL_EVENTS",
    "EventBus",
    "event_bus",
    "subscribe",
]
