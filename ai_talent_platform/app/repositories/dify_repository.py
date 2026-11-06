"""
模块：app/repositories/dify_repository.py
职责：Dify 工作流配置数据访问层（继承 BaseRepository 获得通用 CRUD / 分页 / 软删除）。

标签：[Dify组] [P1]
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dify import WorkflowInfo
from app.repositories.base import BaseRepository


class WorkflowInfoRepository(BaseRepository[WorkflowInfo]):
    model = WorkflowInfo
    filterable_fields = {"workflow_name", "is_deleted", "created_by"}
    searchable_fields = {"workflow_name", "wf_explanation"}
    sortable_fields = {"id", "workflow_name", "created_at", "updated_at"}
    default_order = "-id"

    async def name_exists(
        self,
        db: AsyncSession,
        name: str,
        *,
        exclude_id: int | None = None,
        include_deleted: bool = False,
    ) -> bool:
        """名称唯一性校验（排除自身，默认只看未删除记录）。"""
        stmt = select(func.count()).select_from(WorkflowInfo).where(
            WorkflowInfo.workflow_name == name
        )
        if not include_deleted:
            stmt = stmt.where(WorkflowInfo.is_deleted.is_(False))
        if exclude_id is not None:
            stmt = stmt.where(WorkflowInfo.id != exclude_id)
        return int((await db.execute(stmt)).scalar() or 0) > 0
