"""
模块：app/routers/user_router.py
职责：用户管理接口（列表、详情、创建、更新、状态、角色、重置密码、删除）。

标签：[架构组] [P1]（用户管理）

实现思路：
- 所有接口都需要登录 + 对应权限（require_permission）；
- 分页接口使用 get_page_params 依赖，返回统一分页结构 PageResult；
- 删除使用逻辑删除（Service 内部处理用户名改写）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.schemas.user import (
    AssignRolesRequest,
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
    UserStatusUpdate,
    UserUpdate,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["用户管理"], responses=COMMON_ERROR_RESPONSES)


def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)


@router.get(
    "",
    summary="用户列表（分页）",
    response_model=ApiResponse[PageResult[UserResponse]],
    dependencies=[Depends(require_permission(PermissionEnum.USER_READ))],
)
async def list_users(
    user: CurrentUser,
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="用户名/姓名/手机号/邮箱模糊搜索"),
    organization_id: int | None = Query(None, gt=0, description="按部门筛选"),
    is_active: bool | None = Query(None, description="按启用状态筛选"),
    service: UserService = Depends(get_user_service),
):
    """分页查询用户（按当前用户数据范围过滤：manager 仅本部门及下级）。"""
    result = await service.page_list(
        page, keyword=keyword, organization_id=organization_id, is_active=is_active,
        current_user=user,
    )
    return success(
        data=build_page_result(result.items, result.total, page), message="查询成功"
    )


@router.get(
    "/{user_id}",
    summary="用户详情",
    response_model=ApiResponse[UserResponse],
    dependencies=[Depends(require_permission(PermissionEnum.USER_READ))],
)
async def get_user(
    user_id: int, user: CurrentUser, service: UserService = Depends(get_user_service)
):
    """用户详情（数据范围外的用户返回 403）。"""
    return success(data=await service.get_detail(user_id, current_user=user))


@router.post(
    "",
    summary="创建用户",
    response_model=ApiResponse[UserResponse],
    dependencies=[Depends(require_permission(PermissionEnum.USER_CREATE))],
)
async def create_user(
    data: UserCreate, operator: CurrentUser, service: UserService = Depends(get_user_service)
):
    return success(data=await service.create_user(data, operator=operator), message="创建成功")


@router.put(
    "/{user_id}",
    summary="更新用户",
    response_model=ApiResponse[UserResponse],
    dependencies=[Depends(require_permission(PermissionEnum.USER_UPDATE))],
)
async def update_user(
    user_id: int,
    data: UserUpdate,
    operator: CurrentUser,
    service: UserService = Depends(get_user_service),
):
    return success(data=await service.update_user(user_id, data, operator=operator), message="更新成功")


@router.patch(
    "/{user_id}/status",
    summary="启用/禁用用户",
    response_model=ApiResponse[UserResponse],
    dependencies=[Depends(require_permission(PermissionEnum.USER_UPDATE))],
)
async def set_user_status(
    user_id: int,
    data: UserStatusUpdate,
    operator: CurrentUser,
    service: UserService = Depends(get_user_service),
):
    return success(
        data=await service.set_status(user_id, data, operator=operator), message="状态已更新"
    )


@router.put(
    "/{user_id}/roles",
    summary="分配角色",
    response_model=ApiResponse[UserResponse],
    dependencies=[Depends(require_permission(PermissionEnum.USER_UPDATE))],
)
async def assign_roles(
    user_id: int, data: AssignRolesRequest, service: UserService = Depends(get_user_service)
):
    return success(data=await service.assign_roles(user_id, data), message="角色已更新")


@router.post(
    "/{user_id}/reset-password",
    summary="重置密码（管理员）",
    dependencies=[Depends(require_permission(PermissionEnum.USER_UPDATE))],
)
async def reset_password(
    user_id: int,
    data: ResetPasswordRequest,
    operator: CurrentUser,
    service: UserService = Depends(get_user_service),
):
    await service.reset_password(user_id, data, operator=operator)
    return success(message="密码已重置")


@router.delete(
    "/{user_id}",
    summary="删除用户",
    dependencies=[Depends(require_permission(PermissionEnum.USER_DELETE))],
)
async def delete_user(
    user_id: int, operator: CurrentUser, service: UserService = Depends(get_user_service)
):
    await service.delete_user(user_id, operator=operator)
    return success(message="删除成功")
