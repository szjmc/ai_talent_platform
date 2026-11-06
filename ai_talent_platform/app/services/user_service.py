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
from app.core.permissions import (
    DataScopeEnum,
    check_data_scope,
    get_data_scope,
    visible_organization_ids,
)
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
from app.services.audit_service import publish_audit
from app.core.token_revocation import revoke_user_tokens

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
        current_user: User | None = None,
    ) -> PageResult[UserResponse]:
        """分页查询用户，返回已补充部门名称的响应对象。

        行级数据范围（current_user 提供时生效）：manager 只能查到
        本部门及下级部门的用户，hr/admin/super 不受限（见 visible_organization_ids）。
        """
        filters = {"organization_id": organization_id, "is_active": is_active}
        extra_where = await self._data_scope_conditions(current_user)
        result = await self.repo.paginate(
            self.db, page, filters=filters, keyword=keyword,
            extra_where=extra_where or None,
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

    async def get_detail(self, user_id: int, *, current_user: User | None = None) -> UserResponse:
        """用户详情（current_user 提供时做行级数据范围校验）。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        await self._assert_in_scope(user, current_user)
        org_name = None
        if user.organization_id:
            org = await self.org_repo.get_by_id(self.db, user.organization_id)
            org_name = org.name if org else None
        return self._to_response(user, org_name)

    async def _data_scope_conditions(self, current_user: User | None) -> list:
        """行级数据范围 -> SQLAlchemy 过滤条件（current_user 为空 / 超管时不加条件）。"""
        if current_user is None or current_user.is_superuser:
            return []
        org_ids = await self._visible_org_ids(current_user)
        if org_ids is None:
            return []
        if not org_ids:
            return [User.id == current_user.id]
        return [User.organization_id.in_(org_ids)]

    async def _visible_org_ids(self, current_user: User) -> set[int] | None:
        """当前用户可见部门集合（None=不限制；空集合=仅本人）。"""
        subtree: set[int] = set()
        scope = get_data_scope(current_user.role_codes)
        if scope == DataScopeEnum.DEPT_SUB and current_user.organization_id:
            org = await self.org_repo.get_by_id(self.db, current_user.organization_id)
            if org is not None and org.path:
                nodes = await self.org_repo.list_subtree(self.db, org.path + str(org.id) + "/")
                subtree = {n.id for n in nodes}
        return visible_organization_ids(
            current_user.role_codes, current_user.organization_id, subtree
        )

    async def _assert_in_scope(self, target: User, current_user: User | None) -> None:
        """详情场景：目标用户必须在当前用户的数据范围内，否则 403。"""
        if current_user is None or current_user.is_superuser:
            return
        org_ids = await self._visible_org_ids(current_user)
        if org_ids is None:
            return
        if target.id == current_user.id:
            return
        if org_ids and target.organization_id in org_ids:
            return
        # SELF / 无部门 / 目标不在可见部门内：复用统一单条校验（dept_in_scope=False）
        check_data_scope(
            current_user.role_codes,
            owner_id=target.id,
            current_user_id=current_user.id,
            dept_in_scope=False,
        )

    # ------------------------------------------------------------ 写入
    async def create_user(
        self, data: UserCreate, *, operator: User | None = None
    ) -> UserResponse:
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
        publish_audit(
            action="create", module="user", target_type="user", target_id=user.id,
            user_id=operator.id if operator else None,
            username=operator.username if operator else None,
            after_data={"username": user.username, "organization_id": user.organization_id},
        )
        logger.info("创建用户 id=%s username=%s", user.id, user.username)
        return self._to_response(user, None)

    async def update_user(
        self, user_id: int, data: UserUpdate, *, operator: User | None = None
    ) -> UserResponse:
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
        publish_audit(
            action="update", module="user", target_type="user", target_id=user.id,
            user_id=operator.id if operator else None,
            username=operator.username if operator else None,
            after_data={"fields": sorted(payload)},
        )
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

    async def reset_password(
        self, user_id: int, data: ResetPasswordRequest, *, operator: User | None = None
    ) -> None:
        """管理员重置用户密码（重置后目标用户全部旧令牌失效）。"""
        user = await self.repo.get_by_id_or_404(self.db, user_id, message="用户不存在")
        user.password_hash = hash_password(data.new_password)
        user.fail_count = 0
        user.locked_until = None
        await self.db.flush()
        await revoke_user_tokens(user.id)
        publish_audit(
            action="update", module="user", target_type="password", target_id=user.id,
            user_id=operator.id if operator else None,
            username=operator.username if operator else None,
            status="success",
        )
        logger.info("管理员重置密码 user_id=%s（已吊销其全部旧令牌）", user_id)

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

        deleted_username = user.username.split("#")[0]
        user.username = f"{user.username}#deleted#{user.id}"
        if user.mobile:
            user.mobile = None
        if user.email:
            user.email = None
        user.roles = []
        await self.repo.delete(self.db, user, soft=True)
        await revoke_user_tokens(user.id)
        publish_audit(
            action="delete", module="user", target_type="user", target_id=user.id,
            user_id=operator.id, username=operator.username,
            before_data={"username": deleted_username},
        )
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
