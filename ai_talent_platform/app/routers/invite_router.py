"""人才邀约接口：创建发送（host，需权限）+ 候选人公开访问（凭 token）+ 面试间信令 WS。

标签：[Agent组] [P1]

路由说明：
- /invites              创建邀约（POST，talent:update）
- /invites?page...      邀约分页（GET，talent:read，可按 talent_id 过滤）
- /invites/{id}         邀约详情（GET）
- /invites/visit/{token}         公开落地页信息（无鉴权）
- /invites/exam/{token}          开始/恢复笔试（无鉴权，题面不含 answer）
- /invites/exam/{token}/submit   交卷阅卷（无鉴权，凭 token）
- /invites/exam/{token}/frame    监考帧上报（YOLO/Haar 检测违规）
- /invites/exam/{token}/result   查询成绩/违规统计
- /invites/ws/{token}?role=...   面试间 WebRTC 信令（WebSocket）

新建前缀 invites 不在 tests/test_router_stubs.py::EXPECTED_OWNER 中（守卫不受影响），
负责人按 PR 评审确认（跨 talents=RAG组 / assessments=Vanna组 / agents=Agent组）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.exceptions import BusinessException
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.infrastructure.database.session import session_scope
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.schemas.invitation import (
    ExamResultResponse,
    ExamStartResponse,
    ExamSubmitRequest,
    FrameCheckRequest,
    FrameCheckResponse,
    InviteCreateRequest,
    InviteResponse,
    InviteVisitResponse,
)
from app.services.invitation_service import InvitationService
from app.services.interview_room import ROLES, room_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invites", tags=["人才邀约"], responses=COMMON_ERROR_RESPONSES)


def get_service(db: AsyncSession = Depends(get_db)) -> InvitationService:
    return InvitationService(db)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None

# ------------------------------------------------------------------- host 侧（鉴权）
@router.post(
    "",
    summary="创建并发送邀约（笔试/测评/视频面试/AI 数字人面试）",
    response_model=ApiResponse[InviteResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_UPDATE))],
)
async def create_invite(
    body: InviteCreateRequest,
    user: CurrentUser,
    service: InvitationService = Depends(get_service),
):
    """创建邀约并按渠道真实发送；未配置/失败时 send_results.ok=False，不模拟成功。"""
    invite = await service.create(body, current_user=user)
    return success(data=invite, message="邀约已创建")


@router.get(
    "",
    summary="邀约分页列表",
    response_model=ApiResponse[PageResult[InviteResponse]],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def list_invites(
    user: CurrentUser,
    page: PageParams = Depends(),
    talent_id: int | None = Query(None, gt=0, description="按人才过滤"),
    type: str | None = Query(None, pattern=r"^(written_test|assessment|interview|ai_interview)$", description="邀约类型"),
    status: str | None = Query(
        None, pattern=r"^(created|sent|opened|submitted|finished|expired|cancelled)$", description="状态"
    ),
    keyword: str | None = Query(None, max_length=64, description="标题/姓名模糊搜索"),
    service: InvitationService = Depends(get_service),
):
    result = await service.page_list(page, talent_id=talent_id, type_=type, status=status, keyword=keyword)
    return success(data=result)


@router.get(
    "/{invite_id}",
    summary="邀约详情",
    response_model=ApiResponse[InviteResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TALENT_READ))],
)
async def get_invite(
    invite_id: int,
    user: CurrentUser,
    service: InvitationService = Depends(get_service),
):
    invite = await service.detail(invite_id)
    return success(data=invite)


# ------------------------------------------------------------------- 公开（凭 token）
@router.get("/visit/{token}", summary="【公开】邀约落地页信息", response_model=ApiResponse[InviteVisitResponse])
async def visit_invite(token: str, service: InvitationService = Depends(get_service)):
    info = await service.visit(token)
    return success(data=info)


@router.get("/exam/{token}", summary="【公开】开始/恢复笔试（题面）", response_model=ApiResponse[ExamStartResponse])
async def start_exam(token: str, request: Request, service: InvitationService = Depends(get_service)):
    data = await service.start_exam(token, client_ip=_client_ip(request))
    return success(data=data)


@router.post("/exam/{token}/submit", summary="【公开】交卷阅卷", response_model=ApiResponse[ExamResultResponse])
async def submit_exam(
    token: str,
    body: ExamSubmitRequest,
    request: Request,
    service: InvitationService = Depends(get_service),
):
    data = await service.submit(token, body.answers, client_ip=_client_ip(request))
    return success(data=data, message="交卷成功")


@router.post(
    "/exam/{token}/frame",
    summary="【公开】监考帧上报（YOLO 检测作弊）",
    response_model=ApiResponse[FrameCheckResponse],
)
async def report_frame(
    token: str,
    body: FrameCheckRequest,
    request: Request,
    service: InvitationService = Depends(get_service),
):
    data = await service.check_frame(token, body.image, client_ip=_client_ip(request))
    return success(data=data)


@router.get("/exam/{token}/result", summary="【公开】笔试成绩与违规统计", response_model=ApiResponse[ExamResultResponse])
async def exam_result(token: str, service: InvitationService = Depends(get_service)):
    data = await service.result(token)
    return success(data=data)


# ------------------------------------------------------------------- 面试间信令 WS
@router.websocket("/ws/{token}")
async def invite_ws(websocket: WebSocket, token: str, role: str = Query(default="candidate")):
    """面试间信令：候选人持 token 直连，面试官 ?role=interviewer。

    校验失败以 JSON error + 关闭码返回：
    4401 邀约无效/过期 · 4403 不在可面试时间窗 · 4404 非面试邀约 · 4409 角色占用
    """
    await websocket.accept()
    if role not in ROLES:
        await websocket.send_json({"type": "error", "code": 4400, "message": "非法角色"})
        await websocket.close(code=4400)
        return
    # 校验邀约（独立短会话；WS 生命周期内不占 db 连接）
    try:
        async with session_scope() as db:
            service = InvitationService(db)
            invite = await service._get_valid_invite(token)
            if invite.type != "interview":
                raise BusinessException("该邀约不是面试", code=4404, status_code=400)
            if invite.interview_time:
                window_start = invite.interview_time - timedelta(minutes=settings.INVITE_INTERVIEW_GRACE_MINUTES)
                window_end = invite.interview_time + timedelta(minutes=settings.INVITE_INTERVIEW_END_GRACE_MINUTES)
                if not (window_start <= datetime.now() <= window_end):
                    await websocket.send_json(
                        {"type": "error", "code": 4403, "message": "未到面试时间或已超出面试时段"}
                    )
                    await websocket.close(code=4403)
                    return
    except WebSocketDisconnect:  # pragma: no cover
        return
    except BusinessException as exc:
        await websocket.send_json({"type": "error", "code": exc.code, "message": exc.message})
        await websocket.close(code=4401)
        return
    except Exception as exc:  # noqa: BLE001 —— DB 不可用等系统错误
        await websocket.send_json({"type": "error", "code": 5000, "message": f"服务不可用: {exc}"})
        await websocket.close(code=1011)
        return

    ok, reason = await room_manager.join(token, role, websocket)
    if not ok:
        await websocket.send_json({"type": "error", "code": 4409, "message": reason or "无法进入面试间"})
        await websocket.close(code=4409)
        return

    peer_connected = len(room_manager.roles(token)) > 1
    await websocket.send_json({"type": "joined", "role": role, "peer_connected": peer_connected})
    if peer_connected:
        await room_manager.notify_peer(token, role, {"type": "peer-joined", "role": role})
        room_manager.mark_completed(token)  # 双人曾同房（用于收尾记 finished）

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            mtype = data.get("type")
            if mtype == "leave":
                break
            if mtype not in ("offer", "answer", "ice", "chat"):
                await websocket.send_json({"type": "error", "code": 4400, "message": f"非法消息类型: {mtype}"})
                continue
            await room_manager.relay(token, role, data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 —— 连接层异常按断开处理
        logger.info("面试间 WS 异常 token=%s: %s", token, exc)
    finally:
        peer = room_manager.leave(token, role, websocket)
        if peer is not None:
            await room_manager.notify_peer(token, role, {"type": "peer-left", "role": role})
        # 房内清空（曾有双人会面）-> 记 finished（best effort）
        if not room_manager.roles(token):
            await _mark_interview_finished(token)


async def _mark_interview_finished(token: str) -> None:
    """双人离房后把邀约置为 finished（失败不影响连接收尾）。"""
    try:
        async with session_scope() as db:
            service = InvitationService(db)
            invite = await service.repo.get_by_token(db, token)
            if invite is not None and invite.status in ("opened", "sent", "created"):
                invite.status = "finished"
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.info("面试 finished 状态更新失败 token=%s: %s", token, exc)
