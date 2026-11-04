"""
模块：app/routers/organization_router.py
职责：组织架构接口（部门树、列表、详情、创建、更新、移动、删除）。

标签：[架构组] [P1]（组织架构管理）

实现思路：
- /tree 返回整棵树（前端直接渲染），/ 返回扁平分页列表（表格用）；
- 移动部门单独接口，避免更新接口里混入复杂的父子关系校验；
- 写操作需要 organization:manage 权限，读操作 organization:read。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.organization import (
    OrgCreate,
    OrgMoveRequest,
    OrgResponse,
    OrgTreeNode,
    OrgUpdate,
)
from app.services.organization_service import OrganizationService

router = APIRouter(
    prefix="/organizations", tags=["组织架构"], responses=COMMON_ERROR_RESPONSES
)


def get_org_service(db: AsyncSession = Depends(get_db)) -> OrganizationService:
    return OrganizationService(db)


@router.get(
    "/tree",
    summary="组织架构树",
    response_model=SuccessResponse[list[OrgTreeNode]],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_READ))],
)
async def get_org_tree(service: OrganizationService = Depends(get_org_service)):
    """返回完整部门树（已按 sort 排序）。"""
    return success(data=await service.get_tree())


@router.get(
    "",
    summary="部门列表（分页）",
    response_model=SuccessResponse[PageResult[OrgResponse]],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_READ))],
)
async def list_orgs(
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="部门名称/编码模糊搜索"),
    service: OrganizationService = Depends(get_org_service),
):
    result = await service.page_list(page, keyword=keyword)
    return success(data=build_page_result(result.items, result.total, page))


@router.get(
    "/{org_id}",
    summary="部门详情",
    response_model=SuccessResponse[OrgResponse],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_READ))],
)
async def get_org(org_id: int, service: OrganizationService = Depends(get_org_service)):
    return success(data=await service.get_detail(org_id))


@router.post(
    "",
    summary="创建部门",
    response_model=SuccessResponse[OrgResponse],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_MANAGE))],
)
async def create_org(data: OrgCreate, service: OrganizationService = Depends(get_org_service)):
    return success(data=await service.create_org(data), message="创建成功")


@router.put(
    "/{org_id}",
    summary="更新部门",
    response_model=SuccessResponse[OrgResponse],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_MANAGE))],
)
async def update_org(
    org_id: int, data: OrgUpdate, service: OrganizationService = Depends(get_org_service)
):
    return success(data=await service.update_org(org_id, data), message="更新成功")


@router.patch(
    "/{org_id}/move",
    summary="移动部门",
    response_model=SuccessResponse[OrgResponse],
    dependencies=[Depends(require_permission(PermissionEnum.ORG_MANAGE))],
)
async def move_org(
    org_id: int, data: OrgMoveRequest, service: OrganizationService = Depends(get_org_service)
):
    """把部门移动到新的上级（自动重算 level/path 并级联修正子孙）。"""
    return success(data=await service.move_org(org_id, data), message="移动成功")


@router.delete(
    "/{org_id}",
    summary="删除部门",
    dependencies=[Depends(require_permission(PermissionEnum.ORG_MANAGE))],
)
async def delete_org(org_id: int, service: OrganizationService = Depends(get_org_service)):
    await service.delete_org(org_id)
    return success(message="删除成功")
