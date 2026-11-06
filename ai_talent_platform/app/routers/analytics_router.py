"""
模块：app/routers/analytics_router.py
职责：数据分析接口层：问数、报告生成、报告列表、报告下载。

标签：[Vanna组] [P1]  依赖：analytics_service（问数 / 报告 / 下载）

============================ 实现思路与代码要点 ============================
接口清单（完整地址都带 /api/v1 前缀，由应用总路由添加）：
    GET  /analytics/distribution               学历/年龄/技能/部门分布
    GET  /analytics/assessment/summary         测评得分统计
    GET  /analytics/trend                      月度人才存量及同比/环比
    POST /analytics/compare                    显式快照对比
    POST /analytics/nl2sql                     问数（analytics:read）
    POST /analytics/report                     生成报告（analytics:export）
    GET  /analytics/reports                    报告列表（analytics:read）
    GET  /analytics/reports/{report_id}/download  下载报告（analytics:read + 归属校验）
要点：
- Router 只做参数与权限，不拼接 SQL、不渲染报告；
- 生成报告要 analytics:export（会把数据写进文件），查看/下载只要 analytics:read；
- 下载返回 FileResponse，filename 交给 Starlette 生成 filename*（支持中文名）。
=========================================================================
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.core.dependencies import require_permission
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.models.user import User
from app.schemas.analytics import (
    NL2SQLRequest, NL2SQLResponse, ReportItem, ReportRequest,
)
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.schemas.analytics_statistics import (
    AnalyticsFilters, AssessmentSummaryResponse, ComparisonRequest, ComparisonResponse, DistributionQuery,
    DistributionResponse, TrendQuery, TrendResponse,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["数据分析"], responses=COMMON_ERROR_RESPONSES)

# 报告格式 -> 下载时的 Content-Type
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def get_service() -> AnalyticsService:
    """由 FastAPI 注入业务服务；登录认证读取用户，分析查询另走 PgSQL 只读连接。"""
    return AnalyticsService()


@router.get("/distribution", summary="学历/年龄/技能/部门分布",
            response_model=ApiResponse[DistributionResponse])
async def distribution(
    data: Annotated[DistributionQuery, Query()],
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    """同类筛选OR、跨字段AND；不支持的跨数据源组合明确报错，不静默忽略。"""
    return success(data=await service.distribution(data.type, data, user))


@router.get("/assessment/summary", summary="测评得分统计",
            response_model=ApiResponse[AssessmentSummaryResponse])
async def assessment_summary(
    data: Annotated[AnalyticsFilters, Query()],
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    return success(data=await service.assessment_summary(data, user))


@router.get("/trend", summary="月度人才存量趋势", response_model=ApiResponse[TrendResponse])
async def trend(
    data: Annotated[TrendQuery, Query()],
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    """需要显式月份区间；不把存量差冒充新增/流失，缺期返回null。"""
    return success(data=await service.trend(data, user))


@router.post("/compare", summary="两个明确快照的结构对比", response_model=ApiResponse[ComparisonResponse])
async def compare(
    data: ComparisonRequest,
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    return success(data=await service.compare(data, user))


@router.post("/nl2sql", summary="自然语言问数（Vanna）",
             response_model=ApiResponse[NL2SQLResponse])
async def nl2sql_ask(
    data: NL2SQLRequest,
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    """执行一次自然语言问数。

    data：已由 NL2SQLRequest 校验的问题、行数上限、是否解读、图表类型与外观。
    user：认证及 analytics:read 校验后的用户，不取请求体中的角色。
    返回：带 code/message/data/trace_id 的统一响应；业务异常交给全局处理器。
    """
    # Router 不拼接 SQL，也不接收客户端指定的用户范围。
    result = await service.nl2sql_ask(
        data.question, user, max_rows=data.max_rows,
        with_insight=data.with_insight, chart_type=data.chart_type,
        chart_options=data.chart_options,
    )
    return success(data=result)


@router.post("/report", summary="生成数据分析报告（PDF / Word）",
             response_model=ApiResponse[ReportItem])
async def create_report(
    data: ReportRequest,
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_EXPORT)),
    service: AnalyticsService = Depends(get_service),
):
    """生成一份报告并返回下载地址。

    data：query_id复用页面结果，或question重新查询；再指定格式/标题/是否含图表。
    user：需要analytics:export；快照模式还复核本人身份与当前all范围权限。
    只有question模式会重新问数并可能消耗模型调用。
    返回：报告元数据，其中 url 可直接用于下载。
    """
    item = await service.create_report(data, user)
    return success(data=item)


@router.get("/reports", summary="报告列表",
            response_model=ApiResponse[PageResult[ReportItem]])
async def list_reports(
    page: PageParams = Depends(),
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    """分页查看报告：管理员看全部，其他角色只看自己生成的。"""
    return success(data=await service.list_reports(page, user))


@router.get("/reports/{report_id}/download", summary="下载报告文件")
async def download_report(
    report_id: str,
    user: User = Depends(require_permission(PermissionEnum.ANALYTICS_READ)),
    service: AnalyticsService = Depends(get_service),
):
    """下载报告：校验归属后返回文件流（中文文件名由 Starlette 编码）。

    report_id：32 位十六进制报告 ID；非法或不存在的 ID 统一按 404 / 403 处理。
    """
    path, filename = await service.get_report_file(report_id, user)
    media_type = _MEDIA_TYPES.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=filename)
