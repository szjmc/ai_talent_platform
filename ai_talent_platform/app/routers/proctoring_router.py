"""
模块：app/routers/proctoring_router.py
职责：在线监考 / 防作弊接口层（候选人上报 + HR 复盘），AI 面试 / 笔试 / 面试间共用。

标签：[Agent组] [P1]（防作弊监考接口：切屏 / 远程控制 / 投屏 / 多显示器 / 虚拟摄像头）

接口清单（统一前缀 /api/v1，Swagger 分组「在线监考」）：
    GET  /proctoring/config                     监考策略（登录用户：前端据此决定是否要求共享屏幕）
    POST /proctoring/{subject_type}/{token}/heartbeat   心跳 + 事件批量上报（公开，凭 token）
    POST /proctoring/{subject_type}/{token}/snapshot    可疑快照上报（公开，凭 token）
    GET  /proctoring/{subject_type}/{token}/state       候选人自查（是否被阻断 / 还缺什么）
    GET  /proctoring/sessions                   HR：风险会话分页（ai_interview:read）
    GET  /proctoring/sessions/{id}              HR：证据链（时间线 + 快照）
    POST /proctoring/sessions/{id}/review       HR：复核结论（ai_interview:manage）

安全说明：
- 上报接口不需要登录（候选人无账号），只凭 token + 对象存在性校验，
  但**分数与等级一律服务端计算**，客户端无法通过请求体影响结论；
- 请求体有硬上限（事件条数 / 快照体积），超限 422，防刷分与存储炸弹；
- HR 接口走 RBAC，且只返回监考相关字段，不泄露候选人答卷内容。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.config.settings import settings
from app.core.dependencies import DbSession, require_permission
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.proctoring import (
    ProctorAckResponse,
    ProctorHeartbeatRequest,
    ProctorReportResponse,
    ProctorSessionSummary,
    ProctorSnapshotRequest,
    ProctorStateResponse,
)
from app.services.proctoring_service import ProctoringService

router = APIRouter(
    prefix="/proctoring",
    tags=["在线监考"],
    responses=COMMON_ERROR_RESPONSES,
)

P = PermissionEnum


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def get_service(db: DbSession) -> ProctoringService:
    return ProctoringService(db)


# ================================================================== 策略配置
@router.get(
    "/config",
    summary="监考策略（是否强制全屏 / 屏幕共享 / 单显示器）",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def proctor_config():
    """前端在面试 / 笔试开始前拉取策略，决定要不要请求屏幕共享与拦截多屏。

    未登录的候选人页面用公开的 /state 接口拿候选人视图，不暴露阈值细节。
    """
    return success(
        data={
            "enabled": bool(settings.PROCTOR_ENABLED),
            "strict": bool(settings.PROCTOR_ENABLED and settings.PROCTOR_STRICT_MODE),
            "require_screen_share": bool(settings.PROCTOR_ENABLED and settings.PROCTOR_REQUIRE_SCREEN_SHARE),
            "allow_multi_display": bool(settings.PROCTOR_ALLOW_MULTI_DISPLAY),
            "heartbeat_seconds": max(5, int(settings.PROCTOR_HEARTBEAT_SECONDS or 15)),
            "heartbeat_timeout_seconds": max(20, int(settings.PROCTOR_HEARTBEAT_TIMEOUT_SECONDS or 60)),
            "snapshot_interval_seconds": max(5, int(settings.PROCTOR_SNAPSHOT_INTERVAL_SECONDS or 20)),
            "block_score": max(1, int(settings.PROCTOR_RISK_BLOCK_SCORE or 85)),
            "subject_types": ["ai_interview", "exam", "interview", "assessment"],
        }
    )


# ================================================================== HR 复盘
@router.get(
    "/sessions",
    summary="监考会话分页（按风险等级 / 分数筛选可疑对象）",
    response_model=SuccessResponse[PageResult[ProctorSessionSummary]],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def list_sessions(
    page: PageParams = Depends(),
    subject_type: str | None = Query(
        None, pattern="^(ai_interview|exam|interview|assessment)$", description="对象类型"
    ),
    risk_level: str | None = Query(
        None, pattern="^(clean|watch|review|serious|void|flagged)$",
        description="风险等级；flagged=只看需要人工处理的",
    ),
    min_score: float | None = Query(None, ge=0, le=100, description="最低风险分"),
    keyword: str | None = Query(None, max_length=64, description="候选人 / 岗位 / token 模糊搜索"),
    service: ProctoringService = Depends(get_service),
):
    data = await service.page_sessions(
        page, subject_type=subject_type, risk_level=risk_level, keyword=keyword, min_score=min_score
    )
    return success(data=data)


@router.get(
    "/sessions/{session_ref_id}",
    summary="监考证据链（事件时间线 + 可疑快照 + 处置建议）",
    response_model=SuccessResponse[ProctorReportResponse],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def get_session_report(session_ref_id: int, service: ProctoringService = Depends(get_service)):
    return success(data=await service.report(session_ref_id))


@router.post(
    "/sessions/{session_ref_id}/review",
    summary="HR 复核监考结论（确认作弊 / 误报 / 待定）",
    response_model=SuccessResponse[ProctorReportResponse],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_MANAGE))],
)
async def review_session(
    session_ref_id: int,
    verdict: str = Query(..., pattern="^(confirmed|false_positive|pending)$", description="复核结论"),
    note: str = Query("", max_length=200, description="复核备注"),
    service: ProctoringService = Depends(get_service),
):
    data = await service.review(session_ref_id, verdict=verdict, note=note)
    return success(data=data, message={"confirmed": "已确认为作弊", "false_positive": "已标记为误报"}.get(
        verdict, "复核记录已保存"
    ))


# ================================================================== 候选人公开
@router.post(
    "/{subject_type}/{token}/heartbeat",
    summary="【公开】监考心跳 + 行为事件上报（切屏/远程/投屏等）",
    response_model=SuccessResponse[ProctorAckResponse],
)
async def report_heartbeat(
    subject_type: str,
    token: str,
    body: ProctorHeartbeatRequest,
    request: Request,
    service: ProctoringService = Depends(get_service),
):
    data = await service.heartbeat(subject_type, token, body, client_ip=_client_ip(request))
    return success(data=data, message="已记录")


@router.post(
    "/{subject_type}/{token}/snapshot",
    summary="【公开】上报可疑快照（摄像头 / 屏幕抽样帧）",
    response_model=SuccessResponse[dict],
)
async def report_snapshot(
    subject_type: str,
    token: str,
    body: ProctorSnapshotRequest,
    request: Request,
    service: ProctoringService = Depends(get_service),
):
    data = await service.save_snapshot(subject_type, token, body, client_ip=_client_ip(request))
    return success(data=data, message="快照已留存" if data.get("stored") else "快照已忽略")


@router.get(
    "/{subject_type}/{token}/state",
    summary="【公开】候选人监考状态（是否被阻断 / 还缺哪些环境要求）",
    response_model=SuccessResponse[ProctorStateResponse],
)
async def get_state(subject_type: str, token: str, service: ProctoringService = Depends(get_service)):
    return success(data=await service.state(subject_type, token))


__all__ = ["router"]
