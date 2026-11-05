"""
模块：app/services/auth_service.py
职责：认证业务（注册、登录、刷新令牌、修改密码）。

标签：[架构组] [P0]（注册 / 登录 / 刷新 / 改密，安全红线）

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
- 角色变更后旧 token 中的 roles 会过期，属于可接受行为（重新登录即刷新）；
- 令牌失效闭环：logout 吊销当前 access（及携带的 refresh）jti；
  change_password 触发用户级吊销（全端下线）；refresh / get_current_user 校验吊销状态；
- 登录 / 登出 / 改密 / 注册均 publish_audit 审计事件（EventBus 异步落库）。
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
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.token_revocation import is_token_revoked, revoke_jti, revoke_user_tokens
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.repositories.base import unique_write_guard
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginUserInfo, RegisterRequest
from app.services.audit_service import publish_audit

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
        # SAVEPOINT 保护：并发注册撞唯一键时抛 DuplicateError 而不是 500，
        # 且不回滚掉请求级事务里的其它工作
        async with unique_write_guard(self.db, "User 数据已存在或违反唯一约束"):
            self.db.add(user)
            await self.db.flush()  # 拿到 id 后绑定角色

        await self.user_repo.set_roles(self.db, user, [RoleEnum.EMPLOYEE.value])
        publish_audit(
            action="create",
            module="auth",
            target_type="user",
            target_id=user.id,
            user_id=user.id,
            username=user.username,
            status="success",
        )
        logger.info("用户注册成功 id=%s username=%s", user.id, user.username)
        return user

    # ------------------------------------------------------------ 登录
    async def login(self, data: LoginRequest, *, client_ip: str | None = None) -> dict:
        """账号密码登录，返回令牌与用户信息。"""
        user = await self.user_repo.get_by_username(self.db, data.username)

        # 统一错误提示，避免暴露「用户是否存在」
        if user is None:
            publish_audit(
                action="login", module="auth", username=data.username,
                request_ip=client_ip, status="fail", error_message="用户不存在或密码错误",
            )
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
            publish_audit(
                action="login", module="auth", user_id=user.id, username=user.username,
                request_ip=client_ip, status="fail", error_message="密码错误",
            )
            logger.warning("登录失败 username=%s ip=%s", data.username, client_ip)
            raise CredentialsError()

        await self.user_repo.update_login_success(self.db, user, client_ip)

        # remember_me：签发有效期更长的 access_token（同一签发路径，必带 jti）
        expires_minutes = (
            settings.JWT_REFRESH_EXPIRE_MINUTES if data.remember_me else settings.JWT_EXPIRE_MINUTES
        )
        access_token = self._issue_access_token(user, expires_minutes=expires_minutes)
        refresh_token = create_refresh_token(user.id)

        publish_audit(
            action="login", module="auth", user_id=user.id, username=user.username,
            request_ip=client_ip, status="success",
        )
        logger.info("登录成功 id=%s username=%s ip=%s", user.id, user.username, client_ip)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": expires_minutes * 60,
            "user": self.build_login_user_info(user),
        }

    def _issue_access_token(self, user: User, *, expires_minutes: int | None = None) -> str:
        """签发 access_token（统一走 create_access_token：默认 / remember_me 都带 jti）。"""
        return create_access_token(
            user.id,
            username=user.username,
            roles=user.role_codes,
            expires_minutes=expires_minutes,
        )

    # ------------------------------------------------------------ 令牌刷新
    async def refresh(self, refresh_token: str) -> dict:
        """用 refresh_token 换新的 access_token（refresh_token 本身不轮转）。

        吊销校验：登出时吊销的 refresh jti、改密触发的用户级下线都会在此拒绝。
        """
        payload = decode_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
        if await is_token_revoked(payload):
            raise AuthError("登录状态已失效，请重新登录")
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

    # ------------------------------------------------------------ 登出
    async def logout(
        self,
        user: User,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """登出：吊销当前 access_token（携带 refresh_token 时一并吊销），旧令牌立即失效。"""
        revoked = 0
        for token, expected in (
            (access_token, "access"),
            (refresh_token, TOKEN_TYPE_REFRESH),
        ):
            if not token:
                continue
            try:
                payload = decode_token(token, expected_type=expected)
            except AuthError:
                continue  # 令牌本身已无效，无需吊销
            await revoke_jti(payload.get("jti"), payload.get("exp"))
            revoked += 1
        publish_audit(
            action="logout", module="auth", user_id=user.id, username=user.username,
            status="success",
        )
        logger.info("登出 user_id=%s 吊销令牌数=%s", user.id, revoked)

    # ------------------------------------------------------------ 修改密码
    async def change_password(self, user: User, data: ChangePasswordRequest) -> None:
        """修改当前用户密码（成功后全端下线：用户级吊销存量令牌）。"""
        if data.new_password != data.confirm_password:
            raise ParamError("两次输入的新密码不一致")
        if not verify_password(data.old_password, user.password_hash):
            raise BusinessRuleError("原密码不正确")
        if data.old_password == data.new_password:
            raise BusinessRuleError("新密码不能与原密码相同")

        user.password_hash = hash_password(data.new_password)
        await self.db.flush()
        # 改密 = 强制所有旧令牌失效（iat 早于此刻的 access/refresh 全部拒绝）
        await revoke_user_tokens(user.id)
        publish_audit(
            action="update", module="auth", target_type="password", target_id=user.id,
            user_id=user.id, username=user.username, status="success",
        )
        logger.info("修改密码成功 user_id=%s（已吊销全部旧令牌）", user.id)

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
