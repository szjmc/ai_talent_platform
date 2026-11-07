"""
模块：app/routers/position_router.py
职责：岗位管理接口层（本阶段实现「JD 解析 / 岗位画像确认 / 版本查询」）。

标签：[Agent组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/positions", tags=["岗位管理"], responses=COMMON_ERROR_RESPONSES)
    def get_service(db: AsyncSession = Depends(get_db)) -> XxxService: ...

本阶段只实现匹配链路依赖的画像接口（岗位 CRUD 仍由岗位负责人推进）：
    POST /positions/{id}/jd/parse       JD 智能解析（草稿，不落库）
    POST /positions/{id}/profile/confirm 确认画像并生成新版本（生效动作）
    GET  /positions/{id}/profile        查询最新 / 指定版本画像

权限：
    解析与确认：position:manage（HR / 管理员）
    查询：position:read

注意：
- 解析结果不落库，必须显式确认后才生效（未确认画像不得参与匹配）；
- 接口不返回 500 给前端猜：Dify 不可用会降级为规则解析，并在 channel 中如实标注。

影响范围：仅新增接口；router.py 中把 positions 从占位路由切换为本路由。
=========================================================================
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.models.user import User
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.position import (
    JdParseRequest,
    JdParseResponse,
    JobProfileConfirmRequest,
    JobProfileView,
    PositionCreate,
    PositionResponse,
    PositionStatusUpdate,
    PositionUpdate,
)
from app.services.jd_parser import summarize_missing
from app.services.position_profile_service import PositionProfileService
from app.services.position_service import PositionService

router = APIRouter(
    prefix="/positions",
    tags=["岗位管理"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> PositionProfileService:
    """岗位画像服务依赖。"""
    return PositionProfileService(db)


# *kwq* 新增：JD 智能解析（草稿）。
# 说明：解析结果不写库 —— 避免「解析即生效」绕过 HR 确认。
@router.post(
    "/{position_id}/jd/parse",
    summary="JD 智能解析（生成岗位画像草稿）",
    response_model=SuccessResponse[JdParseResponse],
)
async def parse_jd(
    position_id: int,
    payload: JdParseRequest,
    service: PositionProfileService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """把 JD 原文解析为结构化岗位画像（Dify 优先，规则兜底）。"""
    profile, channel = await service.parse(position_id, payload.jd_text, payload.job_title)
    data = JdParseResponse(
        channel=channel,  # type: ignore[arg-type]
        profile=profile,
        message=summarize_missing(profile),
    )
    return success(data=data, message=data.message)


# *kwq* 新增：确认画像并生成新版本。
# 说明：这是唯一让画像「生效」的入口，写操作需要 position:manage 权限。
@router.post(
    "/{position_id}/profile/confirm",
    summary="确认岗位画像（生成新版本，生效后可用于匹配）",
    response_model=SuccessResponse[JobProfileView],
)
async def confirm_profile(
    position_id: int,
    payload: JobProfileConfirmRequest,
    service: PositionProfileService = Depends(get_service),
    current_user: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """确认岗位画像（HR 可先修订再确认）。"""
    view = await service.confirm(position_id, payload, user_id=current_user.id)
    return success(data=view, message=f"岗位画像 v{view.version} 已确认")


# *kwq* 新增：查询画像（默认最新版本）。
@router.get(
    "/{position_id}/profile",
    summary="查询岗位画像（含版本与确认状态）",
    response_model=SuccessResponse[JobProfileView],
)
async def get_profile(
    position_id: int,
    version: int | None = Query(None, ge=1, description="画像版本号；不传取最新"),
    service: PositionProfileService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.POSITION_READ)),
):
    """查询岗位画像。"""
    view = await service.get_profile(position_id, version)
    return success(data=view, message="OK")


# ============================================================================
# [本轮接口对齐] 岗位 CRUD（替换原 501 占位）
# 背景：/api/v1/positions 曾只挂 placeholder_router，前端岗位页拿不到列表，
#      新建/编辑/上下架全部不可用。以下端点与前端 PositionsPage 一一对应。
# 注意：这些端点必须声明在 /{position_id}/... 之前或之后都行（FastAPI 按注册顺序匹配，
#      静态路径 /positions 与前缀不冲突），但 /{position_id} 详情要放在最后，避免吞掉 /stats。
# ============================================================================


def get_position_service(db: AsyncSession = Depends(get_db)) -> PositionService:
    """岗位 CRUD 服务依赖。"""
    return PositionService(db)


@router.get(
    "",
    summary="岗位列表（分页）",
    response_model=SuccessResponse[PageResult[PositionResponse]],
)
async def list_positions(
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="岗位名称 / 编码模糊搜索"),
    organization_id: int | None = Query(None, gt=0, description="按部门筛选"),
    level: str | None = Query(None, pattern="^(junior|middle|senior|expert)$", description="岗位层级"),
    status: str | None = Query(None, pattern="^(open|paused|closed)$", description="岗位状态"),
    service: PositionService = Depends(get_position_service),
    _: User = Depends(require_permission(PermissionEnum.POSITION_READ)),
):
    """分页查询岗位（含部门名称与最新画像版本，列表页一次查询补齐）。"""
    result = await service.page_list(
        page, keyword=keyword, organization_id=organization_id, level=level, status=status
    )
    return success(
        data=build_page_result(result.items, result.total, page), message="查询成功"
    )


@router.post(
    "",
    summary="新建岗位",
    response_model=SuccessResponse[PositionResponse],
)
async def create_position(
    payload: PositionCreate,
    service: PositionService = Depends(get_position_service),
    current_user: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """新建岗位；不传 code 时自动生成 POS-{id}。"""
    return success(
        data=await service.create(payload, user_id=current_user.id), message="岗位已创建"
    )


@router.get(
    "/{position_id}",
    summary="岗位详情",
    response_model=SuccessResponse[PositionResponse],
)
async def get_position(
    position_id: int,
    service: PositionService = Depends(get_position_service),
    _: User = Depends(require_permission(PermissionEnum.POSITION_READ)),
):
    """岗位详情（含部门名称与最新画像版本）。"""
    return success(data=await service.get(position_id), message="OK")


@router.put(
    "/{position_id}",
    summary="更新岗位",
    response_model=SuccessResponse[PositionResponse],
)
async def update_position(
    position_id: int,
    payload: PositionUpdate,
    service: PositionService = Depends(get_position_service),
    current_user: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """更新岗位（只更新传入字段；状态变更按 open ⇄ paused / closed 单向流转校验）。"""
    return success(
        data=await service.update(position_id, payload, user_id=current_user.id),
        message="岗位已更新",
    )


@router.patch(
    "/{position_id}/status",
    summary="岗位上下架",
    response_model=SuccessResponse[PositionResponse],
)
async def set_position_status(
    position_id: int,
    payload: PositionStatusUpdate,
    service: PositionService = Depends(get_position_service),
    current_user: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """一键切换岗位状态（open / paused / closed）。"""
    return success(
        data=await service.set_status(position_id, payload, user_id=current_user.id),
        message="岗位状态已更新",
    )


@router.delete(
    "/{position_id}",
    summary="删除岗位",
    response_model=SuccessResponse[dict],
)
async def delete_position(
    position_id: int,
    service: PositionService = Depends(get_position_service),
    current_user: User = Depends(require_permission(PermissionEnum.POSITION_MANAGE)),
):
    """逻辑删除岗位（历史匹配记录保留可追溯）。"""
    await service.delete(position_id, user_id=current_user.id)
    return success(data={"id": position_id, "deleted": True}, message="岗位已删除")
