"""
模块：app/routers/auth_router.py
职责：认证接口（注册、登录、刷新令牌、退出、当前用户、修改密码）。

标签：[架构组] [P0]（注册 / 登录 / 刷新 / 当前用户，全平台入口）

实现思路：
- Router 只做三件事：取参数 -> 调 Service -> 包统一响应；
- 所有返回值为 ApiResponse，data 由 Schema 序列化（不直接暴露 ORM 对象）；
- 需要登录的接口使用 `CurrentUser` 依赖；
- 公开接口（注册/登录/刷新）在文档中标注 security=[]，避免 Swagger 误带 Token。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_client_ip, get_db
from app.core.response import success
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginUserInfo,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["认证"], responses=COMMON_ERROR_RESPONSES)


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    """认证服务依赖。"""
    return AuthService(db)


@router.post(
    "/register",
    summary="用户注册",
    response_model=SuccessResponse[UserResponse],
    openapi_extra={"security": []},
)
async def register(
    data: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
):
    """注册新账号，默认绑定 employee 角色。"""
    user = await service.register(data)
    return success(data=UserResponse.model_validate(user), message="注册成功")


@router.post(
    "/login",
    summary="用户登录",
    description="返回 access_token（用于 Authorization）、refresh_token 及当前用户信息。",
    openapi_extra={"security": []},
)
async def login(
    data: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
):
    """账号密码登录。"""
    result = await service.login(data, client_ip=get_client_ip(request))
    payload = TokenResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        token_type=result["token_type"],
        expires_in=result["expires_in"],
    )
    return success(
        data={**payload.model_dump(), "user": result["user"].model_dump()},
        message="登录成功",
    )


@router.post(
    "/refresh",
    summary="刷新访问令牌",
    response_model=SuccessResponse[TokenResponse],
    openapi_extra={"security": []},
)
async def refresh_token(
    data: RefreshTokenRequest,
    service: AuthService = Depends(get_auth_service),
):
    """使用 refresh_token 换取新的 access_token。"""
    result = await service.refresh(data.refresh_token)
    return success(data=TokenResponse(**result), message="刷新成功")


@router.get("/me", summary="获取当前登录用户", response_model=SuccessResponse[LoginUserInfo])
async def get_me(user: CurrentUser, service: AuthService = Depends(get_auth_service)):
    """返回当前用户的角色与权限码，前端据此渲染菜单与按钮。"""
    info = await service.get_user_info(user)
    return success(data=info)


@router.post("/change-password", summary="修改密码")
async def change_password(
    data: ChangePasswordRequest,
    user: CurrentUser,
    service: AuthService = Depends(get_auth_service),
):
    """修改当前用户密码。"""
    await service.change_password(user, data)
    return success(message="密码修改成功")


@router.post("/logout", summary="退出登录")
async def logout(user: CurrentUser):
    """退出登录。

    JWT 是无状态令牌，服务端不做强制失效；前端丢弃 token 即可。
    如需立即失效（强制下线），可在此基础上引入 Redis 黑名单（记录 jti + exp）。
    """
    return success(message="已退出登录")
