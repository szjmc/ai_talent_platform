"""
模块：app/routers/training_router.py
职责：培训管理接口层（培训管理）。

标签：[Dify组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/trainings", tags=["培训管理"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> TrainingService:
        return TrainingService(db)

    @router.get("", summary="...", response_model=ApiResponse[PageResult[XxxResponse]],
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
from app.core.response import ApiResponse, PageResult, success
from app.routers._stub import not_implemented_response
from app.schemas.common import COMMON_ERROR_RESPONSES
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
)
async def list_trainings():
    """占位接口：与 routers/_stub.py 同一 501 契约（未登录也能看到实现位置）。

    主责组实现后替换为真实分页查询，并挂回权限依赖：
        dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))]
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return not_implemented_response("培训管理", "Dify组", "课程、培训计划、学习进度（见 routers/training_router.py）")
