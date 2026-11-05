"""人才档案接口：真实 MySQL 分页、CRUD、画像查询与统计。

标签：[RAG组] [P1]
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.schemas.talent import (
    TalentCreate,
    TalentDetailResponse,
    TalentPortraitResponse,
    TalentQuery,
    TalentResponse,
    TalentStatsResponse,
    TalentUpdate,
)
from app.services.talent_service import TalentService

router = APIRouter(prefix="/talents", tags=["人才档案"], responses=COMMON_ERROR_RESPONSES)


def get_service(db: AsyncSession = Depends(get_db)) -> TalentService:
    return TalentService(db)


@router.get(
    "", summary="人才档案分页列表", response_model=ApiResponse[PageResult[TalentResponse]],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def list_talents(
    user: CurrentUser,
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="姓名/手机/公司/职位/院校"),
    education: str | None = Query(None, max_length=20),
    work_years_min: int | None = Query(None, ge=0, le=60),
    work_years_max: int | None = Query(None, ge=0, le=60),
    organization_id: int | None = Query(None, gt=0),
    status: str | None = Query(None, pattern=r"^(active|left|reserve)$"),
    skill: str | None = Query(None, max_length=64, description="技能 JSON 数组包含筛选"),
    service: TalentService = Depends(get_service),
):
    query = TalentQuery(keyword=keyword, education=education, work_years_min=work_years_min, work_years_max=work_years_max, organization_id=organization_id, status=status, skill=skill)
    result = await service.page_list(page, query, current_user=user)
    return success(data=build_page_result(result.items, result.total, page), message="查询成功")


@router.get(
    "/stats", summary="人才统计", response_model=ApiResponse[TalentStatsResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def talent_stats(
    year: int | None = Query(None, ge=2000, le=2200),
    service: TalentService = Depends(get_service),
):
    return success(data=await service.get_stats(year=year or datetime.now().year))


@router.post(
    "", summary="创建人才档案", response_model=ApiResponse[TalentResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_CREATE))],
)
async def create_talent(data: TalentCreate, operator: CurrentUser, service: TalentService = Depends(get_service)):
    return success(data=await service.create_talent(data, operator=operator), message="创建成功")


@router.get(
    "/{talent_id}", summary="人才档案详情", response_model=ApiResponse[TalentDetailResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def get_talent(talent_id: int, user: CurrentUser, service: TalentService = Depends(get_service)):
    """人才详情（数据范围外返回 403）。"""
    return success(data=await service.get_detail(talent_id, current_user=user))


@router.put(
    "/{talent_id}", summary="更新人才档案", response_model=ApiResponse[TalentResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_UPDATE))],
)
async def update_talent(talent_id: int, data: TalentUpdate, operator: CurrentUser, service: TalentService = Depends(get_service)):
    return success(data=await service.update_talent(talent_id, data, operator=operator), message="更新成功")


@router.delete(
    "/{talent_id}", summary="逻辑删除人才档案", response_model=ApiResponse[None],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_DELETE))],
)
async def delete_talent(talent_id: int, operator: CurrentUser, service: TalentService = Depends(get_service)):
    await service.delete_talent(talent_id, operator=operator)
    return success(message="删除成功")


@router.get(
    "/{talent_id}/portrait", summary="人才最新画像", response_model=ApiResponse[TalentPortraitResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def get_talent_portrait(talent_id: int, service: TalentService = Depends(get_service)):
    return success(data=await service.get_portrait(talent_id))
