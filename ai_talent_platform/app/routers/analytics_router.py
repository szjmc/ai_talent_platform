"""
模块：app/routers/analytics_router.py
职责：数据分析接口层（概览 / 分布 / 趋势 / NL2SQL 问数 / 报告导出）。

标签：[Vanna组] [P1]  依赖：analytics_service（问数 / 看板 / 导出）

接口清单（权限见 docs/api_document.md 十六节）：

    GET    /analytics/overview       首页概览指标（analytics:read）
    GET    /analytics/distribution   学历/年龄/技能/部门分布（analytics:read）
    GET    /analytics/trend          月度趋势 talent_new/turnover/match（analytics:read）
    POST   /analytics/nl2sql         自然语言问数（Vanna -> 校验 -> 执行，analytics:read）
    POST   /analytics/report         生成分析报告，返回任务号（analytics:export）
    GET    /analytics/reports        报告任务列表与状态（analytics:read）

实现要点：
- Router 不写 SQL、不写业务规则；参数校验交给 Schema，业务校验交给 Service；
- 行级数据范围：current_user -> scope 字符串（service.scope_for_user），
  Service/Adapter 内部按角色决定是否注入部门过滤（非管理员 fail-closed）；
- 报告为异步任务：POST 立即返回 task_id，GET /reports 查询状态与文件地址。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, success
from app.schemas.analytics import (
    DashboardOverview,
    DistributionResponse,
    NL2SQLRequest,
    NL2SQLResponse,
    ReportRequest,
    ReportTask,
    TrendResponse,
)
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.services.analytics_service import AnalyticsService, scope_for_user

router = APIRouter(
    prefix="/analytics",
    tags=["数据分析"],
    responses=COMMON_ERROR_RESPONSES,
)

_READ = [Depends(require_permission(PermissionEnum.ANALYTICS_READ))]
_EXPORT = [Depends(require_permission(PermissionEnum.ANALYTICS_EXPORT))]


def get_service(db: AsyncSession = Depends(get_db)) -> AnalyticsService:
    """AnalyticsService 依赖（MySQL 会话用于业务小聚合与降级口径）。"""
    return AnalyticsService(db)


@router.get(
    "/overview",
    summary="首页概览指标",
    response_model=ApiResponse[DashboardOverview],
    dependencies=_READ,
)
async def overview(
    user: CurrentUser, service: AnalyticsService = Depends(get_service)
) -> ApiResponse[DashboardOverview]:
    """驾驶舱概览：人才总数 / 在招岗位 / 匹配率 / 培训完成率 / 平均测评分 / 月度趋势。"""
    data = await service.overview(scope_for_user(user))
    return success(data=data)


@router.get(
    "/distribution",
    summary="多维分布统计",
    response_model=ApiResponse[DistributionResponse],
    dependencies=_READ,
)
async def distribution(
    user: CurrentUser,
    type: str = "education",  # noqa: A002 - FastAPI 查询参数名与文档一致
    service: AnalyticsService = Depends(get_service),
) -> ApiResponse[DistributionResponse]:
    """分布统计：type=education/age/skill/org。"""
    data = await service.distribution(type, scope_for_user(user))
    return success(data=data)


@router.get(
    "/trend",
    summary="月度趋势",
    response_model=ApiResponse[TrendResponse],
    dependencies=_READ,
)
async def trend(
    user: CurrentUser,
    metric: str = "talent_new",
    granularity: str = "month",
    service: AnalyticsService = Depends(get_service),
) -> ApiResponse[TrendResponse]:
    """趋势分析：metric=talent_new/turnover/match，granularity=month。"""
    data = await service.trend(metric, scope_for_user(user), granularity=granularity)
    return success(data=data)


@router.post(
    "/nl2sql",
    summary="自然语言问数",
    response_model=ApiResponse[NL2SQLResponse],
    dependencies=_READ,
)
async def nl2sql(
    payload: NL2SQLRequest,
    user: CurrentUser,
    service: AnalyticsService = Depends(get_service),
) -> ApiResponse[NL2SQLResponse]:
    """Vanna -> sql_validator 校验 -> 行级数据范围 -> PgSQL 只读执行 -> 图表配置。"""
    data = await service.nl2sql(payload, scope_for_user(user))
    return success(data=data, message="问数成功")


@router.post(
    "/report",
    summary="生成分析报告",
    response_model=ApiResponse[ReportTask],
    dependencies=_EXPORT,
)
async def create_report(
    payload: ReportRequest,
    user: CurrentUser,
    service: AnalyticsService = Depends(get_service),
) -> ApiResponse[ReportTask]:
    """异步生成报告（docx；缺依赖降级 md），立即返回任务号，用 GET /reports 查状态。"""
    task = await service.generate_report(payload, scope_for_user(user))
    return success(data=task, message="报告任务已创建")


@router.get(
    "/reports",
    summary="报告任务列表",
    response_model=ApiResponse[list[ReportTask]],
    dependencies=_READ,
)
async def list_reports(service: AnalyticsService = Depends(get_service)) -> ApiResponse[list[ReportTask]]:
    """报告任务列表与状态（新任务在前）。"""
    return success(data=service.reports())
