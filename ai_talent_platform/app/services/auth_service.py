"""
模块：app/services/auth_service.py
职责：认证业务（注册、登录、刷新令牌、修改密码）。

标签：[业务组] [P0]（注册 / 登录 / 刷新 / 改密，安全红线）

实现思路：
1. 注册：唯一性校验（用户名 / 手机号）-> 密码哈希 -> 创建用户 -> 绑定默认 employee 角色；
2. 登录：
   a. 查用户（用户名登录）；
   b. 校验 逻辑删除 / 禁用 / 锁定；
   c. verify_password，失败则累计失败次数（达阈值锁定）；
   d. 成功则清零失败次数、记录登录时间/IP；
   e. 签发 access_token + refresh_token（携带 roles，减少鉴权时的查库次数）；
3. 刷新：校验 refresh_token -> 重新加载用户 -> 签发新 access_token；
4. 修改密码：校验旧密码 -> 强度校验 -> 更新哈希。

安全约定：
- 密码错误统一抛 CredentialsError（不区分用户不存在 / 密码错误，防止用户名枚举）；
- 任何异常信息都不会泄露 password_hash；
- 角色变更后旧 token 中的 roles 会过期，属于可接受行为（重新登录即刷新）。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    AuthError,
    BusinessRuleError,
    CredentialsError,
    DuplicateError,
    ParamError,
)
from app.core.permissions import ROLE_PERMISSIONS, RoleEnum, get_permissions_by_roles
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginUserInfo, RegisterRequest

logger = logging.getLogger(__name__)


class AuthService:
    """认证服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.user_repo = UserRepository()
        self.org_repo = OrganizationRepository()

    # ------------------------------------------------------------ 注册
    async def register(self, data: RegisterRequest) -> User:
        """注册新用户（默认 employee 角色）。"""
        if await self.user_repo.exists_username(self.db, data.username):
            raise DuplicateError("登录名已被占用")
        if data.mobile and await self.user_repo.exists_mobile(self.db, data.mobile):
            raise DuplicateError("手机号已被注册")

        user = User(
            username=data.username,
            password_hash=hash_password(data.password),
            real_name=data.real_name,
            mobile=data.mobile or None,
            email=data.email or None,
            is_active=True,
            is_superuser=False,
        )
        self.db.add(user)
        await self.db.flush()  # 拿到 id 后绑定角色

        await self.user_repo.set_roles(self.db, user, [RoleEnum.EMPLOYEE.value])
        logger.info("用户注册成功 id=%s username=%s", user.id, user.username)
        return user

    # ------------------------------------------------------------ 登录
    async def login(self, data: LoginRequest, *, client_ip: str | None = None) -> dict:
        """账号密码登录，返回令牌与用户信息。"""
        user = await self.user_repo.get_by_username(self.db, data.username)

        # 统一错误提示，避免暴露「用户是否存在」
        if user is None:
            raise CredentialsError()
        if not user.is_active:
            raise AccountDisabledError()
        if user.is_locked:
            raise AccountLockedError(
                f"登录失败次数过多，账号已锁定，请于 {user.locked_until:%Y-%m-%d %H:%M} 后重试"
            )

        if not verify_password(data.password, user.password_hash):
            await self.user_repo.update_login_fail(
                self.db,
                user,
                max_fail=settings.LOGIN_MAX_FAIL_TIMES,
                lock_minutes=settings.LOGIN_LOCK_MINUTES,
            )
            logger.warning("登录失败 username=%s ip=%s", data.username, client_ip)
            raise CredentialsError()

        await self.user_repo.update_login_success(self.db, user, client_ip)

        # remember_me：签发有效期更长的 access_token
        expires_minutes = (
            settings.JWT_REFRESH_EXPIRE_MINUTES if data.remember_me else settings.JWT_EXPIRE_MINUTES
        )
        access_token = self._issue_access_token(user, expires_minutes=expires_minutes)
        refresh_token = create_refresh_token(user.id)

        logger.info("登录成功 id=%s username=%s ip=%s", user.id, user.username, client_ip)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": expires_minutes * 60,
            "user": self.build_login_user_info(user),
        }

    def _issue_access_token(self, user: User, *, expires_minutes: int | None = None) -> str:
        """签发 access_token（默认使用 settings 中的有效期）。"""
        if expires_minutes is None or expires_minutes == settings.JWT_EXPIRE_MINUTES:
            return create_access_token(
                user.id, username=user.username, roles=user.role_codes
            )
        # 自定义有效期（remember_me 场景）
        from datetime import datetime, timedelta, timezone

        import jwt

        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user.id),
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
            "username": user.username,
            "roles": user.role_codes,
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    # ------------------------------------------------------------ 令牌刷新
    async def refresh(self, refresh_token: str) -> dict:
        """用 refresh_token 换新的 access_token（refresh_token 本身不轮转）。"""
        from app.core.security import decode_token, TOKEN_TYPE_REFRESH

        payload = decode_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
        user = await self.user_repo.get_by_id(self.db, int(payload["sub"]))
        if user is None or user.is_deleted:
            raise AuthError("用户不存在或已被删除")
        if not user.is_active:
            raise AccountDisabledError()

        return {
            "access_token": self._issue_access_token(user),
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_EXPIRE_MINUTES * 60,
        }

    # ------------------------------------------------------------ 修改密码
    async def change_password(self, user: User, data: ChangePasswordRequest) -> None:
        """修改当前用户密码。"""
        if data.new_password != data.confirm_password:
            raise ParamError("两次输入的新密码不一致")
        if not verify_password(data.old_password, user.password_hash):
            raise BusinessRuleError("原密码不正确")
        if data.old_password == data.new_password:
            raise BusinessRuleError("新密码不能与原密码相同")

        user.password_hash = hash_password(data.new_password)
        await self.db.flush()
        logger.info("修改密码成功 user_id=%s", user.id)

    # ------------------------------------------------------------ 当前用户信息
    def build_login_user_info(self, user: User) -> LoginUserInfo:
        """组装前端需要的用户信息（角色 + 权限码）。"""
        roles = user.role_codes
        permissions: list[str] = sorted(get_permissions_by_roles(roles))
        if user.is_superuser:
            permissions = ["*"]
        elif not permissions:
            permissions = sorted(ROLE_PERMISSIONS.get(RoleEnum.EMPLOYEE.value, set()))

        return LoginUserInfo(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            avatar=user.avatar,
            organization_id=user.organization_id,
            position_title=user.position_title,
            is_superuser=user.is_superuser,
            roles=roles,
            permissions=permissions,
        )

    async def get_user_info(self, user: User) -> LoginUserInfo:
        """获取当前登录用户信息（含部门名称）。"""
        info = self.build_login_user_info(user)
        if user.organization_id:
            org = await self.org_repo.get_by_id(self.db, user.organization_id)
            if org is not None:
                info.organization_name = org.name
        return info
