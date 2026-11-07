"""
模块：app/routers/audit_router.py
职责：审计日志查询接口（/audit）：列表 / 详情 / 统计。

标签：[架构组] [P1]（审计模块的真实实现，替换原 501 占位路由）

背景（本轮修复的「接口分错组」问题）：
    AuditRepository / AuditService 早已实现并接入了事件总线，
    但 /api/v1/audit 一直是 placeholder_router 生成的 501 占位，
    前端 AuditPage 只能显示「后端占位 / 待实现」——能力已具备却没有对外入口。

契约（与 docs/api_document.md §十九 一致）：
    GET /audit/logs        分页查询（user_id / module / action / status / 时间区间 / trace_id / keyword）
    GET /audit/logs/{id}   单条详情（含 before_data / after_data）
    GET /audit/stats       动作与模块分布、失败率

权限：全部要求 audit:read（默认只有 admin 及以上持有）。
审计日志只增不改：本路由**没有写接口**，也不提供删除。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.core.exceptions import NotFoundError
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogResponse, AuditStatsResponse
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.services.audit_service import AuditService

router = APIRouter(
    prefix="/audit",
    tags=["审计日志"],
    responses=COMMON_ERROR_RESPONSES,
    dependencies=[Depends(require_permission(PermissionEnum.AUDIT_READ))],
)


def get_audit_service(db: AsyncSession = Depends(get_db)) -> AuditService:
    """审计服务依赖。"""
    return AuditService(db)


@router.get(
    "/logs",
    summary="审计日志列表（分页）",
    response_model=ApiResponse[PageResult[AuditLogResponse]],
)
async def list_audit_logs(
    page: PageParams = Depends(),
    user_id: int | None = Query(None, gt=0, description="按操作人 ID 筛选"),
    module: str | None = Query(None, max_length=32, description="业务模块，如 talent / knowledge"),
    action: str | None = Query(None, max_length=16, description="动作：create/update/delete/login/export/ai_call"),
    status: str | None = Query(None, pattern="^(success|fail)$", description="success / fail"),
    trace_id: str | None = Query(None, max_length=64, description="链路追踪 ID"),
    start_at: datetime | None = Query(None, description="时间区间起点（含）"),
    end_at: datetime | None = Query(None, description="时间区间终点（含）"),
    keyword: str | None = Query(None, max_length=64, description="用户名 / 路径 / 失败原因模糊搜索"),
    service: AuditService = Depends(get_audit_service),
):
    """分页查询审计日志（默认按 id 倒序，最新在前）。"""
    result = await service.list_with_filters(
        page,
        user_id=user_id,
        module=module,
        action=action,
        status=status,
        trace_id=trace_id,
        start_at=start_at,
        end_at=end_at,
        keyword=keyword,
    )
    return success(
        data=build_page_result(
            [AuditLogResponse.model_validate(item) for item in result.items], result.total, page
        ),
        message="查询成功",
    )


@router.get(
    "/logs/{log_id}",
    summary="审计日志详情",
    response_model=ApiResponse[AuditLogResponse],
)
async def get_audit_log(
    log_id: int, service: AuditService = Depends(get_audit_service)
):
    """单条审计详情（含 before_data / after_data，均已脱敏）。"""
    log = await service.repo.get_by_id(service.db, log_id)
    if log is None:
        raise NotFoundError("审计日志不存在")
    return success(data=AuditLogResponse.model_validate(log), message="OK")


@router.get(
    "/stats",
    summary="审计统计（动作分布 / 失败率）",
    response_model=ApiResponse[AuditStatsResponse],
)
async def audit_stats(
    start_at: datetime | None = Query(None, description="统计起点（含）"),
    end_at: datetime | None = Query(None, description="统计终点（含）"),
    service: AuditService = Depends(get_audit_service),
):
    """动作 / 模块分布与失败率（SQL 聚合，不拉全表到内存）。"""
    conditions = []
    if start_at is not None:
        conditions.append(AuditLog.created_at >= start_at)
    if end_at is not None:
        conditions.append(AuditLog.created_at <= end_at)

    async def _group(column) -> dict[str, int]:
        stmt = select(column, func.count(AuditLog.id)).group_by(column)
        if conditions:
            stmt = stmt.where(*conditions)
        rows = (await service.db.execute(stmt)).all()
        return {str(key or "unknown"): int(count) for key, count in rows}

    by_action = await _group(AuditLog.action)
    by_module = await _group(AuditLog.module)
    status_stmt = select(AuditLog.status, func.count(AuditLog.id)).group_by(AuditLog.status)
    if conditions:
        status_stmt = status_stmt.where(*conditions)
    by_status = {str(key or "unknown"): int(count) for key, count in (await service.db.execute(status_stmt)).all()}

    success_count = by_status.get("success", 0)
    failed_count = by_status.get("fail", 0) + by_status.get("failed", 0)
    total = success_count + failed_count
    return success(
        data=AuditStatsResponse(
            total=total,
            by_action=by_action,
            by_module=by_module,
            success=success_count,
            failed=failed_count,
            fail_rate=round(failed_count / total, 4) if total else 0.0,
            start_at=start_at,
            end_at=end_at,
        ),
        message="OK",
    )
