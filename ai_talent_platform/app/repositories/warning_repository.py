"""
模块：app/repositories/warning_repository.py
职责：储备预警记录的数据访问（只读写，不写业务规则）。

标签：[Agent组] [P1]（储备预警仓储）
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reserve_warning import ReserveWarning
from app.repositories.base import BaseRepository


class WarningRepository(BaseRepository[ReserveWarning]):
    """ReserveWarning 仓库。"""

    # 公共CRUD必须知道模型；筛选白名单保证岗位/类型/解决状态真正参与SQL。
    model = ReserveWarning
    filterable_fields = {"warning_type", "position_id", "resolved", "severity"}
    sortable_fields = {"id", "created_at", "updated_at"}
    default_order = "-created_at"

    async def get_unresolved(
        self, db: AsyncSession, *, warning_type: str, position_id: int
    ) -> ReserveWarning | None:
        """取同类型同岗位的未解决预警（扫描幂等去重的依据）。"""
        stmt = select(ReserveWarning).where(
            ReserveWarning.warning_type == warning_type,
            ReserveWarning.position_id == position_id,
            ReserveWarning.resolved.is_(False),
            ReserveWarning.is_deleted.is_(False),
        )
        return (await db.execute(stmt)).scalars().first()

    async def list_unresolved_by_positions(
        self, db: AsyncSession, position_ids: list[int]
    ) -> list[ReserveWarning]:
        """取这些岗位的全部未解决预警（用于扫描后自动闭环）。"""
        if not position_ids:
            return []
        stmt = select(ReserveWarning).where(
            ReserveWarning.position_id.in_(position_ids),
            ReserveWarning.resolved.is_(False),
            ReserveWarning.is_deleted.is_(False),
        )
        return list((await db.execute(stmt)).scalars().all())

    async def resolve(
        self,
        db: AsyncSession,
        warning: ReserveWarning,
        *,
        by_user_id: int | None = None,
    ) -> ReserveWarning:
        """标记解决（自动闭环 by_user_id=None；人工解决传用户 ID）。"""
        warning.resolved = True
        warning.resolved_at = datetime.now()
        warning.resolved_by = by_user_id
        await db.flush()
        return warning


__all__ = ["WarningRepository"]
