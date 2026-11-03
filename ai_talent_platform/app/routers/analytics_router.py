"""
模块：app/routers/analytics_router.py
职责：数据分析接口层（数据分析）。

标签：[Vanna组] [P1]  依赖：analytics_service（问数 / 看板 / 导出）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/analytics", tags=["数据分析"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> AnalyticsService:
        return AnalyticsService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: AnalyticsService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    GET    /analytics/overview       首页概览指标
    GET    /analytics/distribution   学历/年龄/技能/部门分布
    GET    /analytics/trend          月度趋势
    POST   /analytics/nl2sql         自然语言问数（Vanna -> SQL -> 数据 + 图表配置）
    POST   /analytics/report         生成分析报告（导出 Word/PDF）
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
from app.services.analytics_service import AnalyticsService

router = APIRouter(
    prefix="/analytics",
    tags=["数据分析"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> AnalyticsService:
    """AnalyticsService 依赖。"""
    return AnalyticsService(db)


@router.get(
    "",
    summary="数据分析列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.ANALYTICS_READ))],
)
async def list_analytics(page: PageParams = Depends(), service: AnalyticsService = Depends(get_service)):
    """占位接口：AnalyticsService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="数据分析模块待实现")
