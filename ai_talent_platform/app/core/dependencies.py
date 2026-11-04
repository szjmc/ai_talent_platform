"""
模块：app/core/dependencies.py
职责：全局依赖注入（数据库会话、当前用户、权限校验、分页等）。

标签：[架构组] [P0]（DB 会话 / 当前用户 / 权限依赖注入）

实现思路：
1. 所有「横切关注点」在这里以 Depends 形式提供给 Router，Router 不再自己解析 Token；
2. 主要依赖：
     get_db                  -> 请求级 AsyncSession（来自 infrastructure/database/session）
     get_current_user        -> 解析 Bearer Token、加载用户、校验状态（禁用 / 锁定）
     get_current_active_user -> 同上（语义化别名，业务接口默认使用）
     get_current_user_optional -> 允许匿名访问的接口使用（返回 None）
     get_current_roles       -> 当前用户角色编码列表
     require_permission(p)   -> 权限依赖工厂（支持单个 / 多个权限、any/all 策略）
     require_roles(*roles)   -> 角色依赖工厂
     get_page_params         -> 分页参数
     get_trace_id            -> 请求 trace_id
3. Token 通过 HTTPBearer 获取，auto_error=False 以便自定义 401 提示；
4. 用户缓存：开启 Redis 后可在此加缓存（ttl 60s），降低每请求查库开销。

使用示例：
    @router.get("/users")
    async def list_users(
        db: AsyncSession = Depends(get_db),
        _: User = Depends(require_permission(PermissionEnum.USER_READ)),
        page: PageParams = Depends(get_page_params),
    ): ...
"""

from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import trace_id_ctx
from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    AuthError,
    PermissionDeniedError,
)
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum, has_all_permissions, has_any_permission
from app.core.security import decode_token
from app.infrastructure.database.session import get_db
from app.models.user import User

# auto_error=False：没有 Token 时不抛框架异常，交给下面统一抛 AuthError
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """解析 Token 并返回当前登录用户。"""
    if credentials is None or not credentials.credentials:
        raise AuthError("请先登录")

    # 1) 校验 JWT（签名 + 过期 + 类型）
    payload = decode_token(credentials.credentials)
    user_id = int(payload["sub"])

    # 2) 查库加载用户（含角色）
    from app.repositories.user_repository import UserRepository  # 避免循环导入

    repo = UserRepository()
    user = await repo.get_by_id(db, user_id)
    if user is None:
        raise AuthError("用户不存在或已被删除")

    # 3) 状态校验
    if user.is_deleted:
        raise AuthError("用户不存在或已被删除")
    if not user.is_active:
        raise AccountDisabledError()
    if user.is_locked:
        raise AccountLockedError()

    return user


# 语义化别名：业务接口推荐使用
get_current_active_user = get_current_user


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """允许匿名的接口使用：无 Token 返回 None，有 Token 则校验。"""
    if credentials is None or not credentials.credentials:
        return None
    return await get_current_user(credentials, db)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_roles(user: CurrentUser) -> list[str]:
    """当前用户的角色编码列表。"""
    return user.role_codes


async def require_superuser(user: CurrentUser) -> User:
    """要求超级管理员。"""
    if not user.is_superuser and "super_admin" not in user.role_codes:
        raise PermissionDeniedError("仅超级管理员可执行该操作")
    return user


def require_permission(
    *permissions: str | PermissionEnum,
    require_all: bool = False,
) -> Callable[..., User]:
    """权限依赖工厂。

    用法：
        Depends(require_permission(PermissionEnum.TALENT_READ))
        Depends(require_permission("talent:read", "talent:update", require_all=True))
    """
    if not permissions:
        raise ValueError("require_permission 至少需要一个权限码")

    async def _checker(user: CurrentUser) -> User:
        roles = user.role_codes
        if user.is_superuser:
            return user
        ok = (
            has_all_permissions(roles, list(permissions))
            if require_all
            else has_any_permission(roles, list(permissions))
        )
        if not ok:
            raise PermissionDeniedError(
                "缺少权限：" + ",".join(str(p) for p in permissions)
            )
        return user

    return _checker


def require_roles(*roles: str) -> Callable[..., User]:
    """角色依赖工厂：当前用户具备任一角色即可。"""

    async def _checker(user: CurrentUser) -> User:
        if user.is_superuser:
            return user
        if not set(user.role_codes) & set(roles):
            raise PermissionDeniedError("当前角色无权访问该资源")
        return user

    return _checker


# ---------------------------------------------------------------- 其它通用依赖
def get_page_params() -> PageParams:
    """分页参数依赖（返回 PageParams，自动读取 page/page_size/order_by/order）。"""
    return PageParams()  # type: ignore[call-arg]


def get_trace_id() -> str:
    """当前请求的 trace_id（用于需要回传给前端的场景）。"""
    return trace_id_ctx.get()


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（兼容反向代理）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


__all__ = [
    "get_db",
    "get_current_user",
    "get_current_active_user",
    "get_current_user_optional",
    "get_current_roles",
    "require_superuser",
    "require_permission",
    "require_roles",
    "get_page_params",
    "get_trace_id",
    "get_client_ip",
    "CurrentUser",
    "OptionalUser",
    "DbSession",
    "PageParams",
]
