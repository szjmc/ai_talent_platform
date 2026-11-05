"""
模块：app/repositories/audit_repository.py
职责：审计日志数据访问层。

标签：[架构组] [P1]（审计入库）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，已声明：
    model = AuditLog
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    default_order = "-id"

- 只增不改：不实现 update/delete 方法（create_log 是唯一写入口）；
- 写审计失败不能影响主业务（Service 中 try/except 吞掉异常并记日志）；
- 敏感字段入库前脱敏（Service.log 统一处理）。
方法清单（按顺序实现，勾选表示已完成）：
    - [x] create_log(db, data)                           写审计（唯一写入口）
    - [x] list_with_filters(db, page_params, filters)    审计列表（按用户/模块/动作）
    - [x] count_by_action(db, date_range)                动作分布统计
    - [x] get_by_trace_id(db, trace_id)                  按链路 ID 追踪一次请求

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
========================================================================
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository):
    """审计日志仓库（只增不改）。"""

    model = AuditLog
    filterable_fields: set[str] = {
        "user_id",
        "username",
        "action",
        "module",
        "target_type",
        "target_id",
        "status",
        "trace_id",
        "request_path",
    }
    searchable_fields: set[str] = {"username", "request_path", "error_message"}
    sortable_fields: set[str] = {"id", "created_at", "action"}
    default_order = "-id"

    async def create_log(self, db: AsyncSession, data: dict[str, Any]) -> AuditLog:
        """写入一条审计日志（唯一写入口；调用方负责脱敏与异常保护）。"""
        payload = {k: v for k, v in data.items() if self._has_column(k)}
        log = AuditLog(**payload)
        db.add(log)
        await db.flush()
        return log

    async def list_with_filters(
        self,
        db: AsyncSession,
        page_params: PageParams,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
    ) -> PageResult[AuditLog]:
        """审计列表（按用户 / 模块 / 动作 / 状态过滤，分页）。"""
        return await self.paginate(
            db, page_params, filters=filters, keyword=keyword
        )

    async def count_by_action(
        self,
        db: AsyncSession,
        *,
        start: Any | None = None,
        end: Any | None = None,
    ) -> list[dict[str, Any]]:
        """动作分布统计（SQL 聚合；start/end 为 created_at 边界）。"""
        stmt = select(AuditLog.action, func.count(AuditLog.id)).group_by(AuditLog.action)
        conditions = []
        if start is not None:
            conditions.append(AuditLog.created_at >= start)
        if end is not None:
            conditions.append(AuditLog.created_at <= end)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(func.count(AuditLog.id).desc())
        rows = (await db.execute(stmt)).all()
        return [{"action": action, "count": count} for action, count in rows]

    async def get_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[AuditLog]:
        """按链路 ID 追踪一次请求的全部审计记录。"""
        stmt = (
            select(AuditLog)
            .where(AuditLog.trace_id == trace_id)
            .order_by(AuditLog.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())
