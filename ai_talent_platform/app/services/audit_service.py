"""
模块：app/services/audit_service.py
职责：操作审计业务服务层（统一写入口 + EventBus 订阅落库）。

标签：[架构组] [P1]（全局操作与 AI 审计，订阅本组 EventBus 异步入库）

============================ 实现思路与代码要点 ============================
Service 的定位：把「校验 + 仓库调用 + 外部能力 + 事务」组织成一次完整业务。

审计链路（已接通）：
    业务点 publish_audit(...) / event_bus.publish(EVENT_*)
        -> 本模块 _handle_audit_log / _handle_ai_call（lifespan 启动时注册）
        -> 自己开 session_scope()（后台任务不能复用请求级会话）
        -> AuditService.log(...) 脱敏 + try/except 保护 -> AuditRepository.create_log

硬性约束：
- Service 不处理 HTTP（不接收 Request、不返回 JSONResponse）；
- Service 不写复杂 SQL（查询交给 Repository）；
- 写审计失败不影响主业务（异常吞掉并记 error 日志）；
- 审计查询需要权限：仅 admin/super_admin（由 Router 的 require_permission 把关）。
方法清单（按顺序实现，勾选表示已完成）：
    - [x] log(...)：统一写审计入口（带脱敏与 try/except 保护）
    - [x] 查询：按用户/模块/动作/时间段过滤分页（list_with_filters 委托仓库）
    - [x] 统计：各动作次数（count_by_action 委托仓库，SQL 聚合）
    - [x] AI 调用审计：EVENT_AI_CALL_FINISHED -> action=ai_call（model/token/耗时入 after_data）
========================================================================
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import trace_id_ctx
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.audit import AuditLog
from app.infrastructure.database.session import session_scope
from app.infrastructure.messaging.event_bus import (
    EVENT_AI_CALL_FINISHED,
    EVENT_AUDIT_LOG,
    event_bus,
)
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit_repository import AuditRepository

logger = logging.getLogger(__name__)

# publish_audit 透传给 log() 的合法字段（多余字段丢弃，避免把未知 key 写进模型）
_LOG_FIELDS = {
    "action",
    "module",
    "user_id",
    "username",
    "target_type",
    "target_id",
    "request_method",
    "request_path",
    "request_ip",
    "user_agent",
    "trace_id",
    "before_data",
    "after_data",
    "status",
    "error_message",
}

# 入库前需脱敏的字段名（大小写不敏感，值为字符串时打码）
_SENSITIVE_KEYS = re.compile(
    r"(?i)^(password|passwd|old_password|new_password|password_hash|token|"
    r"access_token|refresh_token|secret|api_key|id_card)$"
)


def _mask_value(value: Any) -> Any:
    """字符串值打码，其余类型递归处理容器。"""
    if isinstance(value, str):
        return "***"
    if isinstance(value, dict):
        return {k: _mask_audit_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_audit_value(None, item) for item in value]
    return value


def _mask_audit_value(key: str | None, value: Any) -> Any:
    if key and _SENSITIVE_KEYS.match(str(key)) and value is not None:
        return _mask_value(value)
    if isinstance(value, (dict, list, tuple)):
        return _mask_value(value)
    return value


def mask_audit_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """审计数据脱敏（password / token / secret / id_card 等字段值替换为 ***）。"""
    if not data:
        return data
    return {k: _mask_audit_value(k, v) for k, v in data.items()}


def publish_audit(**fields: Any) -> None:
    """发布审计事件（fire-and-forget）：落库由订阅者完成，失败绝不影响主流程。

    trace_id 在发布时刻捕获（asyncio.create_task 会复制当前上下文，双保险）。
    """
    fields.setdefault("trace_id", trace_id_ctx.get() or "-")
    event_bus.publish(EVENT_AUDIT_LOG, fields)


class AuditService:
    """操作审计业务服务。"""

    def __init__(self, db: AsyncSession, repo: AuditRepository | None = None) -> None:
        self.db = db
        self.repo = repo or AuditRepository()

    async def log(
        self,
        *,
        action: str,
        module: str,
        user_id: int | None = None,
        username: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        request_method: str | None = None,
        request_path: str | None = None,
        request_ip: str | None = None,
        user_agent: str | None = None,
        trace_id: str | None = None,
        before_data: dict[str, Any] | None = None,
        after_data: dict[str, Any] | None = None,
        status: str = "success",
        error_message: str | None = None,
        user: User | None = None,
    ) -> AuditLog | None:
        """统一写审计入口（敏感字段脱敏；任何失败只记日志，不抛出）。"""
        try:
            if user is not None:
                user_id = user_id if user_id is not None else user.id
                username = username or user.username
            payload: dict[str, Any] = {
                "action": str(action)[:16],
                "module": module,
                "user_id": user_id,
                "username": username,
                "target_type": target_type,
                "target_id": target_id,
                "request_method": request_method,
                "request_path": request_path,
                "request_ip": request_ip,
                "user_agent": user_agent,
                "trace_id": trace_id,
                "before_data": mask_audit_data(before_data),
                "after_data": mask_audit_data(after_data),
                "status": status,
                "error_message": str(error_message)[:512] if error_message else None,
            }
            return await self.repo.create_log(self.db, payload)
        except Exception:  # noqa: BLE001 - 写审计失败绝不影响主业务
            logger.exception("审计写入失败（已忽略） action=%s module=%s", action, module)
            return None

    async def list_with_filters(
        self,
        page_params: PageParams,
        *,
        user_id: int | None = None,
        module: str | None = None,
        action: str | None = None,
        status: str | None = None,
        trace_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        keyword: str | None = None,
    ) -> PageResult[AuditLog]:
        """审计分页查询（调用方需具备 admin 权限）。

        trace_id / start_at / end_at 直接下推到 SQL：
        早期版本只在已分页的结果上再做一次内存过滤，导致「翻到第 2 页才命中」
        的日志永远查不到，且 total 与 items 对不上。
        """
        filters = {
            "user_id": user_id,
            "module": module,
            "action": action,
            "status": status,
            "trace_id": trace_id,
        }
        extra_where = []
        if start_at is not None:
            extra_where.append(AuditLog.created_at >= start_at)
        if end_at is not None:
            extra_where.append(AuditLog.created_at <= end_at)
        return await self.repo.paginate(
            self.db,
            page_params,
            filters=filters,
            keyword=keyword,
            extra_where=extra_where or None,
        )

    async def count_by_action(
        self, *, start: Any | None = None, end: Any | None = None
    ) -> list[dict[str, Any]]:
        """动作分布统计。"""
        return await self.repo.count_by_action(self.db, start=start, end=end)


# ------------------------------------------------------------------ EventBus 接线
_handlers_registered = False


async def _handle_audit_log(payload: Any) -> None:
    """EVENT_AUDIT_LOG 订阅者：payload 即 log() 的关键字参数字典。"""
    if not isinstance(payload, dict):
        logger.warning("审计事件 payload 非法，忽略：%r", payload)
        return
    fields = {k: v for k, v in payload.items() if k in _LOG_FIELDS}
    if not fields.get("action"):
        logger.warning("审计事件缺少 action，忽略：%r", payload)
        return
    async with session_scope() as db:
        await AuditService(db).log(**fields)


async def _handle_ai_call_finished(payload: Any) -> None:
    """EVENT_AI_CALL_FINISHED 订阅者：AI 调用落为 action=ai_call 审计。"""
    if not isinstance(payload, dict):
        return
    metrics = {
        k: payload.get(k)
        for k in ("model", "provider", "prompt_tokens", "completion_tokens", "elapsed_ms", "agent")
        if payload.get(k) is not None
    }
    fields = {
        "action": "ai_call",
        "module": payload.get("module") or "ai",
        "user_id": payload.get("user_id"),
        "username": payload.get("username"),
        "target_type": payload.get("target_type"),
        "target_id": payload.get("target_id"),
        "trace_id": payload.get("trace_id") or trace_id_ctx.get() or "-",
        "after_data": metrics,
        "status": payload.get("status", "success"),
        "error_message": payload.get("error_message"),
    }
    async with session_scope() as db:
        await AuditService(db).log(**fields)


def register_audit_handlers() -> None:
    """注册审计相关事件订阅（幂等，可重复调用；lifespan 启动时调用一次）。"""
    global _handlers_registered
    if _handlers_registered:
        return
    event_bus.subscribe(EVENT_AUDIT_LOG, _handle_audit_log)
    event_bus.subscribe(EVENT_AI_CALL_FINISHED, _handle_ai_call_finished)
    _handlers_registered = True
    logger.info("审计事件订阅已注册：%s, %s", EVENT_AUDIT_LOG, EVENT_AI_CALL_FINISHED)


def unregister_audit_handlers() -> None:
    """移除审计订阅（测试收尾用）。"""
    global _handlers_registered
    event_bus.unsubscribe(EVENT_AUDIT_LOG, _handle_audit_log)
    event_bus.unsubscribe(EVENT_AI_CALL_FINISHED, _handle_ai_call_finished)
    _handlers_registered = False
