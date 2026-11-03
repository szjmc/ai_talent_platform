"""
模块：app/routers/talent_router.py
职责：人才档案接口层（人才档案）。

标签：[业务组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/talents", tags=["人才档案"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> TalentService:
        return TalentService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: TalentService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /talents                  创建人才档案（talent:create）
    GET    /talents                  分页列表（keyword/education/org/skill）
    GET    /talents/{id}             人才详情（含画像摘要）
    GET    /talents/{id}/portrait    人才画像详情
    PUT    /talents/{id}             更新人才
    DELETE /talents/{id}             删除人才（逻辑删除）
    POST   /talents/search/semantic  自然语言语义检索（向量召回）
    POST   /talents/{id}/portrait/refresh  触发画像刷新（异步任务）
    POST   /talents/merge            合并重复档案
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
from app.services.talent_service import TalentService

router = APIRouter(
    prefix="/talents",
    tags=["人才档案"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> TalentService:
    """TalentService 依赖。"""
    return TalentService(db)


@router.get(
    "",
    summary="人才档案列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def list_talents(page: PageParams = Depends(), service: TalentService = Depends(get_service)):
    """占位接口：TalentService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="人才档案模块待实现")
