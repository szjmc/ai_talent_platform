"""
模块：app/repositories/user_repository.py
职责：用户与角色的数据访问（公共层已实现，供 auth / user 模块复用）。

标签：[业务组] [P0]（认证 / 用户管理数据访问）

设计说明：
- 继承 BaseRepository，直接获得 create/get_by_id/paginate/update/soft_delete 等能力；
- 这里只补充「用户特有」的查询与写入：按用户名、按手机号、角色绑定、登录状态更新；
- 不写业务判断（例如「密码是否正确」在 AuthService 中做）。

对外方法清单：
    get_by_username(db, username)              按登录名查
    get_by_mobile(db, mobile)                  按手机号查
    get_with_roles(db, user_id)                查询用户（含角色，lazy=selectin 已自动）
    exists_username(db, username)              登录名是否已存在
    exists_mobile(db, mobile)                  手机号是否已存在
    get_roles_by_codes(db, codes)              按编码批量取角色
    set_roles(db, user, role_codes)            重置用户角色
    update_login_success(db, user, ip)         登录成功：更新最后登录信息并清零失败次数
    update_login_fail(db, user, max_fail, lock_minutes)  登录失败：累计并可能锁定
    list_by_organization(db, org_id)           部门下用户
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.role import Role
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """用户仓库。"""

    model = User
    filterable_fields = {"organization_id", "is_active", "is_superuser", "username", "mobile"}
    searchable_fields = {"username", "real_name", "mobile", "email"}
    sortable_fields = {"id", "created_at", "updated_at", "last_login_at", "username"}
    default_order = "-id"

    # ------------------------------------------------------------ 查询
    async def get_by_username(self, db: AsyncSession, username: str) -> User | None:
        """按登录名查询（含角色）。"""
        stmt = (
            select(User)
            .where(User.username == username, User.is_deleted.is_(False))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_mobile(self, db: AsyncSession, mobile: str) -> User | None:
        stmt = select(User).where(User.mobile == mobile, User.is_deleted.is_(False)).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def exists_username(self, db: AsyncSession, username: str) -> bool:
        """登录名是否已被占用（含已逻辑删除用户，避免唯一键冲突）。"""
        stmt = select(func.count()).select_from(User).where(User.username == username)
        return int((await db.execute(stmt)).scalar() or 0) > 0

    async def exists_mobile(self, db: AsyncSession, mobile: str) -> bool:
        stmt = select(func.count()).select_from(User).where(User.mobile == mobile)
        return int((await db.execute(stmt)).scalar() or 0) > 0

    async def list_by_organization(self, db: AsyncSession, organization_id: int) -> list[User]:
        stmt = select(User).where(
            User.organization_id == organization_id,
            User.is_deleted.is_(False),
            User.is_active.is_(True),
        )
        return list((await db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ 角色
    async def get_roles_by_codes(self, db: AsyncSession, codes: list[str]) -> list[Role]:
        """按编码批量获取角色；不存在的编码忽略（由 Service 决定是否报错）。"""
        if not codes:
            return []
        stmt = select(Role).where(Role.code.in_(codes), Role.is_active.is_(True))
        return list((await db.execute(stmt)).scalars().all())

    async def set_roles(self, db: AsyncSession, user: User, role_codes: list[str]) -> User:
        """重置用户角色（先清空再绑定）。

        注意：关系属性 lazy="selectin"，在异步会话中直接赋值会触发隐式懒加载
        （MissingGreenlet）。因此先显式 refresh 加载 roles 集合，再整体替换。
        """
        roles = await self.get_roles_by_codes(db, role_codes)
        missing = set(role_codes) - {r.code for r in roles}
        if missing:
            raise NotFoundError(f"角色不存在：{sorted(missing)}")

        if user.id is not None:
            await db.refresh(user, attribute_names=["roles"])
        user.roles = roles
        await db.flush()
        return user

    # ------------------------------------------------------------ 登录状态
    async def update_login_success(self, db: AsyncSession, user: User, ip: str | None) -> User:
        user.last_login_at = datetime.now()
        user.last_login_ip = ip
        user.fail_count = 0
        user.locked_until = None
        await db.flush()
        return user

    async def update_login_fail(
        self,
        db: AsyncSession,
        user: User,
        *,
        max_fail: int = 5,
        lock_minutes: int = 15,
    ) -> User:
        """累计失败次数，达到阈值后锁定账号。"""
        user.fail_count = (user.fail_count or 0) + 1
        if user.fail_count >= max_fail:
            user.locked_until = datetime.now() + timedelta(minutes=lock_minutes)
            user.fail_count = 0
        await db.flush()
        return user
