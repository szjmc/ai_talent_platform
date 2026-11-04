"""
模块：app/routers/position_router.py
职责：岗位管理接口层（岗位管理）。

标签：[Agent组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/positions", tags=["岗位管理"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> PositionService:
        return PositionService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: PositionService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /positions                创建岗位（position:manage）
    GET    /positions                分页列表（keyword/org_id/level/status）
    GET    /positions/{id}           岗位详情（含结构化 requirements）
    PUT    /positions/{id}           更新岗位
    PATCH  /positions/{id}/status    上架 / 关闭岗位
    DELETE /positions/{id}           删除岗位（逻辑删除）
    POST   /positions/{id}/parse     上传岗位说明书 -> AI 解析 requirements
实现要点：
- Router 不写 SQL、不写业务规则；参数校验交给 Schema，业务校验交给 Service；
- 查询列表统一分页（PageParams + build_page_result），返回 PageResult；
- 每个写接口都要挂权限依赖：dependencies=[Depends(require_permission(...))]；
- 上传/下载类接口使用 UploadFile / FileResponse，并对 MIME 与大小做校验；
- 长耗时任务（AI 解析、向量化、批处理）接口立即返回任务号，不要同步等待。
=========================================================================
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.services.position_service import PositionService

router = APIRouter(
    prefix="/positions",
    tags=["岗位管理"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> PositionService:
    """PositionService 依赖。"""
    return PositionService(db)


@router.get(
    "",
    summary="岗位管理列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.POSITION_READ))],
)
async def list_positions(page: PageParams = Depends(), service: PositionService = Depends(get_service)):
    """占位接口：PositionService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="岗位管理模块待实现")
