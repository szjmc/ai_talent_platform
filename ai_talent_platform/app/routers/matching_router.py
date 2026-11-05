"""
模块：app/routers/matching_router.py
职责：人岗匹配接口层（人岗匹配）。

标签：[Agent组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/matchings", tags=["人岗匹配"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> MatchingService:
        return MatchingService(db)

    @router.get("", summary="...", response_model=ApiResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: MatchingService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /matchings/run            执行匹配（position_id + top_k，返回 batch_id）
    GET    /matchings/positions/{id} 某岗位最新匹配结果
    GET    /matchings/talents/{id}   某人才历史匹配记录
    POST   /matchings/explain        匹配解释（AI 生成维度说明）
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
from app.services.matching_service import MatchingService

router = APIRouter(
    prefix="/matchings",
    tags=["人岗匹配"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> MatchingService:
    """MatchingService 依赖。"""
    return MatchingService(db)


@router.get(
    "",
    summary="人岗匹配列表（占位）",
)
async def list_matchings():
    """占位接口：与 routers/_stub.py 同一 501 契约（未登录也能看到实现位置）。

    主责组实现后替换为真实分页查询，并挂回权限依赖：
        dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))]
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return not_implemented_response("人岗匹配", "Agent组", "执行匹配、匹配结果、匹配解释（见 routers/matching_router.py）")
