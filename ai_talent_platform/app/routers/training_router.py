"""
模块：app/routers/training_router.py
职责：培训管理接口层（培训管理）。

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/trainings", tags=["培训管理"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> TrainingService:
        return TrainingService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: TrainingService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /trainings/courses        创建课程（training:manage）
    GET    /trainings/courses        课程列表
    POST   /trainings/plans          创建培训计划（可由 AI 生成）
    GET    /trainings/plans          计划列表（含完成率）
    POST   /trainings/records        更新学习进度
    POST   /trainings/recommend      根据能力缺口推荐课程
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
from app.services.training_service import TrainingService

router = APIRouter(
    prefix="/trainings",
    tags=["培训管理"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> TrainingService:
    """TrainingService 依赖。"""
    return TrainingService(db)


@router.get(
    "",
    summary="培训管理列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def list_trainings(page: PageParams = Depends(), service: TrainingService = Depends(get_service)):
    """占位接口：TrainingService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="培训管理模块待实现")
