"""
模块：app/services/notification_service.py
职责：培训站内信（节点 4/5/6 推送的落地点：写库 / 查询 / 已读 + 事件订阅者）。

标签：[Dify组] [P1]（培训推送落地：站内信与事件订阅）

============================ 实现思路与代码要点 ============================
为什么需要这一层
----------------
事件总线在「无订阅者」时是静默丢弃的（event_bus.py 只打一条 debug 日志）。
此前培训模块发出 5 类事件，`app/` 下**一个订阅者都没有**，于是：
    「推送计划给员工」      → 只是发了个没人接的事件，`channels:["inapp"]` 没有实现
    「落后提醒 / HR 预警」  → 同上
    「复测回流测评 Agent」  → 同上，且测评模块没有复测入口
演示脚本自己订阅并打印，所以看起来是通的；答辩被追问就是硬伤。

本模块做两件事
--------------
1. **同事务落库**：Service 在业务事务内直接调 `record()` 写站内信。
   为什么不只靠事件订阅：`publish` 是 `asyncio.create_task` 异步派发，
   而业务事务此时还没 commit，订阅者开新 session 写带外键的站内信会等父行锁，
   轻则拖慢请求、重则死锁。同事务写既不会漏，也没有跨事务等待。
2. **注册事件订阅者**：`register_training_notification_subscribers()` 在应用启动时执行，
   消费这 5 类事件，用于「非本服务发起」的场景（比如测评 Agent 主动发
   `training.plan.adjust`、外部定时任务发滞后事件）。处理器先查计划是否存在，
   查不到（事务未提交）直接跳过，绝不阻塞主流程。

幂等
----
唯一键 `(plan_id, type, notify_date)`：同一计划同一天同类通知只落一条。
滞后扫描重跑、页面轮询、手工补扫都不会重复轰炸收件人。
=========================================================================
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents import training_logic as L
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.infrastructure.messaging.event_bus import event_bus
from app.models.training import TrainingNotification, TrainingPlan
from app.repositories.training_repository import TrainingRepository

logger = logging.getLogger(__name__)

# 事件类型 -> 默认标题模板（payload 没带 title 时兜底）
_TITLES: dict[str, str] = {
    L.EVENT_PLAN_CREATED: "新的培训计划已下发",
    L.EVENT_LAG_REMIND: "培训进度落后提醒",
    L.EVENT_LAG_ALERT: "培训进度严重滞后（HR 预警）",
    L.EVENT_RETEST_REQUIRED: "结课考核通过，待安排复测",
    L.EVENT_PLAN_ADJUST: "结课考核未通过，计划需调整",
}

# 事件类型 -> 默认级别
_LEVELS: dict[str, str] = {
    L.EVENT_PLAN_CREATED: "正常",
    L.EVENT_LAG_REMIND: "提醒",
    L.EVENT_LAG_ALERT: "HR预警",
    L.EVENT_RETEST_REQUIRED: "正常",
    L.EVENT_PLAN_ADJUST: "提醒",
}


def build_notification(event: dict[str, Any]) -> dict[str, Any] | None:
    """把一条事件整理成站内信字段（纯函数，便于单测）。

    返回 None 表示这条事件不该落站内信（缺少 plan_id，或类型不在培训几类里）。
    """
    event_type = str(event.get("type") or "").strip()
    plan_id = event.get("plan_id")
    if not event_type or plan_id in (None, "", 0):
        return None
    if event_type not in _TITLES:
        return None

    detail = str(event.get("detail") or "").strip()
    content = str(event.get("content") or "").strip() or detail
    if not content:
        content = _TITLES[event_type]

    return {
        "plan_id": int(plan_id),
        "talent_id": int(event["talent_id"]) if event.get("talent_id") else None,
        "type": event_type,
        "level": str(event.get("level") or _LEVELS.get(event_type, "正常")),
        "title": str(event.get("title") or _TITLES[event_type])[:128],
        "content": content[:512],
    }


class TrainingNotificationService:
    """培训站内信服务（写库 / 查询 / 已读）。"""

    def __init__(self, db: AsyncSession, repo: TrainingRepository | None = None) -> None:
        self.db = db
        self.repo = repo or TrainingRepository()

    async def record(self, event: dict[str, Any], *, notify_date: date | None = None) -> Any:
        """把一条事件落成站内信（幂等：同计划同类型当天只保留一条）。

        返回写入的记录；已存在或事件不合法时返回 None（调用方无需关心）。
        """
        payload = build_notification(event)
        if payload is None:
            return None
        day = notify_date or date.today()
        if await self.repo.notification.exists_today(
            self.db, payload["plan_id"], payload["type"], day
        ):
            logger.debug(
                "站内信已存在，跳过：plan=%s type=%s date=%s",
                payload["plan_id"],
                payload["type"],
                day,
            )
            return None
        return await self.repo.notification.create(
            self.db, **payload, notify_date=day, is_read=False
        )

    async def record_many(self, events: list[dict[str, Any]]) -> int:
        """批量落库，返回实际写入条数。"""
        written = 0
        for event in events:
            if await self.record(event) is not None:
                written += 1
        return written

    async def page(
        self,
        page: PageParams,
        *,
        talent_id: int | None = None,
        is_read: bool | None = None,
        event_type: str | None = None,
    ) -> PageResult:
        """站内信分页（按收件人 / 已读状态 / 类型筛选）。"""
        filters: dict[str, Any] = {"talent_id": talent_id, "is_read": is_read}
        if event_type:
            filters["type"] = event_type
        return await self.repo.notification.paginate(self.db, page, filters=filters)

    async def unread_count(self, talent_id: int) -> int:
        return await self.repo.notification.unread_count(self.db, talent_id)

    async def mark_read(
        self, notification_id: int, *, talent_id: int | None = None
    ) -> Any:
        """标记单条已读；传 talent_id 时校验收件人，防止越权读别人的信。"""
        row = await self.repo.notification.get_by_id(self.db, notification_id)
        if row is None or row.is_deleted:
            raise NotFoundError(f"站内信不存在（id={notification_id}）")
        if talent_id is not None and row.talent_id != talent_id:
            raise PermissionDeniedError("只能操作自己的站内信")
        if not row.is_read:
            row.is_read = True
            row.read_at = datetime.now()
            await self.db.flush()
        return row

    async def mark_all_read(self, *, talent_id: int | None = None) -> int:
        """把未读全部标记已读，返回影响条数。

        talent_id 为 None 时作用于全部站内信（仅管理者可达，见 Router 的
        _is_manager 校验）；语义与 GET /notifications 不传 talent_id 时
        「管理者看全部」保持一致，否则前端「全部已读」按钮对管理员必然报错。
        """
        conditions = [
            TrainingNotification.is_read.is_(False),
            TrainingNotification.is_deleted.is_(False),
        ]
        if talent_id is not None:
            conditions.append(TrainingNotification.talent_id == talent_id)
        stmt = (
            update(TrainingNotification)
            .where(*conditions)
            .values(is_read=True, read_at=datetime.now())
        )
        result = await self.db.execute(stmt)
        return int(result.rowcount or 0)


# ============================================================== 事件订阅者
_SUBSCRIBED = False


async def handle_training_event(payload: dict[str, Any] | None) -> None:
    """消费一条培训事件并落站内信。

    独立 session（脚本/后台场景没有请求级事务）；任何异常只告警，
    绝不能反向影响主业务——事件总线本来就允许「副作用失败不影响主流程」。

    注意：先确认计划已可见再写。业务事务提交前，父行对别的连接不可见，
    此时写带外键的站内信会等父行锁。查不到就跳过，不做无谓等待。
    """
    if not isinstance(payload, dict):
        return
    plan_id = payload.get("plan_id")
    if plan_id in (None, "", 0):
        return

    from app.infrastructure.database.session import session_scope

    try:
        async with session_scope() as db:
            plan = await db.get(TrainingPlan, int(plan_id))
            if plan is None or plan.is_deleted:
                logger.debug("站内信跳过：计划 %s 尚不可见（可能事务未提交）", plan_id)
                return
            service = TrainingNotificationService(db)
            await service.record(payload)
    except Exception as exc:  # noqa: BLE001 - 副作用失败不影响主流程
        logger.warning("培训站内信写入失败（plan=%s）：%s", plan_id, exc)


def register_training_notification_subscribers() -> int:
    """注册 5 类培训事件的订阅者（幂等，多次调用只注册一次）。

    返回实际注册的事件数，便于启动日志自证「事件不再是空的」。
    """
    global _SUBSCRIBED
    if _SUBSCRIBED:
        return 0
    registered = 0
    for event_name in L.TRAINING_EVENTS:
        event_bus.subscribe(event_name, handle_training_event)
        registered += 1
    _SUBSCRIBED = True
    logger.info("培训事件订阅完成：%s 类（站内信落库）", registered)
    return registered


def reset_subscribers_for_test() -> None:
    """测试用：解除注册状态（避免全局状态跨用例串味）。"""
    global _SUBSCRIBED
    for event_name in L.TRAINING_EVENTS:
        event_bus.unsubscribe(event_name, handle_training_event)
    _SUBSCRIBED = False


__all__ = [
    "TrainingNotificationService",
    "build_notification",
    "handle_training_event",
    "register_training_notification_subscribers",
    "reset_subscribers_for_test",
]
