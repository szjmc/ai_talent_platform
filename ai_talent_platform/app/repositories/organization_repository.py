"""
模块：app/repositories/organization_repository.py
职责：组织架构数据访问层。

标签：[架构组] [P0]（组织树查询，数据权限范围依赖）

实现思路：
- 继承 BaseRepository 获得通用 CRUD；
- 树形相关查询优先使用 path 前缀匹配（`path LIKE '/1/3/%'`），避免递归 SQL；
- 部门人数统计用 SQL 聚合，不在 Python 中循环；
- 不写业务判断（父子关系合法性、能否删除等在 OrganizationService）。

已实现方法：
    get_by_code            按编码查
    list_children          直接子部门
    list_subtree           子树（含自身，按 path 前缀）
    list_all_active        全部启用部门（用于构建整棵树）
    exists_sibling_name    同级重名校验
    count_users            部门在职人数
    count_children         子部门数量
    update_subtree_path    移动部门时批量重写子孙 path / level
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User
from app.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository[Organization]):
    """组织架构仓库。"""

    model = Organization
    filterable_fields = {"parent_id", "code", "is_active"}
    searchable_fields = {"name", "code"}
    sortable_fields = {"id", "sort", "created_at", "name"}
    default_order = "sort"

    async def get_by_code(self, db: AsyncSession, code: str) -> Organization | None:
        """按部门编码查询。"""
        stmt = select(Organization).where(
            Organization.code == code, Organization.is_deleted.is_(False)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_children(
        self, db: AsyncSession, parent_id: int | None
    ) -> list[Organization]:
        """直接子部门（parent_id 为 None 时取根部门）。"""
        stmt = select(Organization).where(Organization.is_deleted.is_(False))
        stmt = stmt.where(
            Organization.parent_id.is_(None)
            if parent_id is None
            else Organization.parent_id == parent_id
        )
        stmt = stmt.order_by(Organization.sort.asc(), Organization.id.asc())
        return list((await db.execute(stmt)).scalars().all())

    async def list_subtree(self, db: AsyncSession, path: str) -> list[Organization]:
        """按完整自身前缀取子孙部门（不含自身）。

        organizations.path 只含祖先：部门 3 在根 1 下时 path=/1/，
        其子孙前缀必须是 /1/3/。传入 /1/ 会误含兄弟部门，调用方负责拼接自身 ID。
        """
        stmt = (
            select(Organization)
            .where(
                Organization.is_deleted.is_(False),
                Organization.path.like(f"{path}%"),
            )
            .order_by(Organization.level.asc(), Organization.sort.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_all_active(self, db: AsyncSession) -> list[Organization]:
        """全部启用部门（构建组织树时一次性取出，避免 N+1 查询）。"""
        stmt = (
            select(Organization)
            .where(Organization.is_deleted.is_(False), Organization.is_active.is_(True))
            .order_by(Organization.level.asc(), Organization.sort.asc(), Organization.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def exists_sibling_name(
        self,
        db: AsyncSession,
        parent_id: int | None,
        name: str,
        exclude_id: int | None = None,
    ) -> bool:
        """同一父级下部门名称是否重复。"""
        stmt = select(func.count()).select_from(Organization).where(
            Organization.name == name, Organization.is_deleted.is_(False)
        )
        stmt = stmt.where(
            Organization.parent_id.is_(None) if parent_id is None else Organization.parent_id == parent_id
        )
        if exclude_id is not None:
            stmt = stmt.where(Organization.id != exclude_id)
        return int((await db.execute(stmt)).scalar() or 0) > 0

    async def count_users(self, db: AsyncSession, organization_id: int, *, include_sub: bool = False) -> int:
        """部门在职人数；include_sub=True 时统计整棵子树。"""
        org_ids = [organization_id]
        if include_sub:
            org = await self.get_by_id(db, organization_id)
            if org is None:
                return 0
            subtree = await self.list_subtree(db, org.path + str(org.id) + "/")
            org_ids = [organization_id] + [o.id for o in subtree]
        stmt = select(func.count()).select_from(User).where(
            User.organization_id.in_(org_ids),
            User.is_deleted.is_(False),
            User.is_active.is_(True),
        )
        return int((await db.execute(stmt)).scalar() or 0)

    async def count_children(self, db: AsyncSession, organization_id: int) -> int:
        """直接子部门数量（删除前校验）。"""
        stmt = select(func.count()).select_from(Organization).where(
            Organization.parent_id == organization_id, Organization.is_deleted.is_(False)
        )
        return int((await db.execute(stmt)).scalar() or 0)

    async def update_subtree_path(
        self,
        db: AsyncSession,
        old_prefix: str,
        new_prefix: str,
        level_delta: int,
    ) -> int:
        """移动部门后批量重写子孙的 path 与 level。

        :param old_prefix: 原 path（形如 /1/3/）
        :param new_prefix: 新 path（形如 /2/5/）
        :param level_delta: 层级变化量（可正可负）
        """
        subtree = await self.list_subtree(db, old_prefix)
        affected = 0
        for org in subtree:
            if org.path.startswith(old_prefix):
                org.path = new_prefix + org.path[len(old_prefix):]
                org.level = max(1, org.level + level_delta)
                affected += 1
        await db.flush()
        return affected
