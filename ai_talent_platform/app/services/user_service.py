"""
模块：app/services/user_service.py
职责：用户管理业务（管理员视角的 CRUD、状态、角色）。

标签：[架构组] [P1]

实现思路：
- 所有方法接收 db（Service 由 Router 通过依赖注入构造），返回 ORM 对象或 PageResult；
- 唯一性校验、权限校验（不能禁用自己 / 超管）、逻辑删除时的用户名改写都在这里；
- 需要「部门名称」等跨表信息时，调用对应 Repository 批量查询（避免 N+1）。

安全约定：任何返回值都不包含 password_hash（Schema 层已保证）。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    DuplicateError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.core.security import hash_password
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    AssignRolesRequest,
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
    UserStatusUpdate,
    UserUpdate,
)

logger = logging.getLogger(__name__)


class UserService:
    """用户管理服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = UserRepository()
        self.org_repo = OrganizationRepository()

    # ------------------------------------------------------------ 查询
    async def page_list(
        self,
        page: PageParams,
        *,
        keyword: str | None = None,
        organization_id: int | None = None,
        is_active: bool | None = None,
    ) -> PageResult[UserResponse]:
        """分页查询用户，返回已补充部门名称的响应对象。"""
        filters = {"organization_id": organization_id, "is_active": is_active}
        result = await self.repo.paginate(
            self.db, page, filters=filters, keyword=keyword
        )

        # 批量取部门名称，避免逐条查询
        org_ids = {u.organization_id for u in result.items if u.organization_id}
        org_names: dict[int, str] = {}
        if org_ids:
            orgs = await self.org_repo.list(
                self.db,
                filters={"id": list(org_ids)},
                include_deleted=True,
            )
            org_names = {o.id: o.name for o in orgs}

        result.items = [
            self._to_response(u, org_names.get(u.organization_id or 0)) for u in result.items
        ]
        return result

    async def get_detail(self, user_id: int) -> UserResponse:
        """用户详情。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        org_name = None
        if user.organization_id:
            org = await self.org_repo.get_by_id(self.db, user.organization_id)
            org_name = org.name if org else None
        return self._to_response(user, org_name)

    # ------------------------------------------------------------ 写入
    async def create_user(self, data: UserCreate) -> UserResponse:
        """创建用户。"""
        if await self.repo.exists_username(self.db, data.username):
            raise DuplicateError("登录名已被占用")
        if data.mobile and await self.repo.exists_mobile(self.db, data.mobile):
            raise DuplicateError("手机号已被注册")
        if data.organization_id and not await self.org_repo.get_by_id(self.db, data.organization_id):
            raise NotFoundError("所属部门不存在")

        user = User(
            username=data.username,
            password_hash=hash_password(data.password),
            real_name=data.real_name,
            mobile=data.mobile or None,
            email=data.email or None,
            organization_id=data.organization_id,
            position_title=data.position_title,
            is_active=data.is_active,
        )
        self.db.add(user)
        await self.db.flush()
        await self.repo.set_roles(self.db, user, data.role_codes or ["employee"])
        logger.info("创建用户 id=%s username=%s", user.id, user.username)
        return self._to_response(user, None)

    async def update_user(self, user_id: int, data: UserUpdate) -> UserResponse:
        """更新用户基础信息。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        payload = data.model_dump(exclude_unset=True)

        if payload.get("mobile") and payload["mobile"] != user.mobile:
            if await self.repo.exists_mobile(self.db, payload["mobile"]):
                raise DuplicateError("手机号已被其他账号使用")
        if payload.get("organization_id") and not await self.org_repo.get_by_id(
            self.db, payload["organization_id"]
        ):
            raise NotFoundError("所属部门不存在")

        await self.repo.update(self.db, user, payload)
        logger.info("更新用户 id=%s fields=%s", user_id, list(payload))
        return self._to_response(user, None)

    async def set_status(
        self, user_id: int, data: UserStatusUpdate, *, operator: User
    ) -> UserResponse:
        """启用 / 禁用用户。"""
        if user_id == operator.id:
            raise BusinessRuleError("不能修改自己的启用状态")
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        if user.is_superuser:
            raise PermissionDeniedError("不能禁用超级管理员")
        await self.repo.update(self.db, user, {"is_active": data.is_active})
        logger.info("用户状态变更 id=%s is_active=%s by=%s", user_id, data.is_active, operator.id)
        return self._to_response(user, None)

    async def reset_password(self, user_id: int, data: ResetPasswordRequest) -> None:
        """管理员重置用户密码。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        user.password_hash = hash_password(data.new_password)
        user.fail_count = 0
        user.locked_until = None
        await self.db.flush()
        logger.info("管理员重置密码 user_id=%s", user_id)

    async def assign_roles(self, user_id: int, data: AssignRolesRequest) -> UserResponse:
        """分配角色（覆盖式）。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        await self.repo.set_roles(self.db, user, data.role_codes)
        logger.info("角色分配 user_id=%s roles=%s", user_id, data.role_codes)
        return self._to_response(user, None)

    async def delete_user(self, user_id: int, *, operator: User) -> None:
        """逻辑删除用户。

        说明：为释放唯一键占用，删除后把 username 改写为 `xxx#deleted#<id>`。
        """
        if user_id == operator.id:
            raise BusinessRuleError("不能删除自己")
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        if user.is_superuser:
            raise PermissionDeniedError("不能删除超级管理员")

        user.username = f"{user.username}#deleted#{user.id}"
        if user.mobile:
            user.mobile = None
        if user.email:
            user.email = None
        user.roles = []
        await self.repo.delete(self.db, user, soft=True)
        logger.info("删除用户 user_id=%s by=%s", user_id, operator.id)

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _to_response(user: User, organization_name: str | None) -> UserResponse:
        """ORM -> 响应 Schema（补充派生字段）。"""
        return UserResponse(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            mobile=user.mobile,
            email=user.email,
            avatar=user.avatar,
            organization_id=user.organization_id,
            organization_name=organization_name,
            position_title=user.position_title,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            roles=[{"id": r.id, "code": r.code, "name": r.name} for r in user.roles],
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )
