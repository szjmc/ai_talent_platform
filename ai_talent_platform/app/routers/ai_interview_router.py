"""
模块：app/routers/ai_interview_router.py
职责：AI 数字人面试接口层（HR 管理 + 候选人免登录面试）。

标签：[Agent组] [P1]（AI 面试 REST 接口）

接口清单（统一前缀 /api/v1）：
    GET    /ai-interviews/config                     可选数字人 / 默认配置（ai_interview:read）
    GET    /ai-interviews                            面试分页列表（ai_interview:read）
    POST   /ai-interviews                            创建面试并发链接（ai_interview:manage）
    POST   /ai-interviews/{id}/cancel                取消面试（ai_interview:manage）
    GET    /ai-interviews/{id}                       面试详情 + 逐轮对话（ai_interview:read）
    GET    /ai-interviews/{id}/report                评估报告（ai_interview:read）

候选人公开（token 免登录，无需 Authorization）：
    GET    /ai-interviews/session/{token}            获取面试状态 / 断点恢复
    POST   /ai-interviews/session/{token}/start      开始面试（幂等）
    POST   /ai-interviews/session/{token}/answer     提交单题回答
    POST   /ai-interviews/session/{token}/repeat     让数字人重复当前问题
    POST   /ai-interviews/session/{token}/finish     提前结束并生成报告

注意路由顺序：/config 必须注册在 /{session_id} 之前，否则 "config" 会先被
int 路径参数捕获并返回 422（FastAPI 的路径参数校验发生在匹配之后）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import CurrentUser, DbSession, require_permission
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.schemas.ai_interview import (
    AiInterviewAnswerRequest,
    AiInterviewAnswerResult,
    AiInterviewConfigResponse,
    AiInterviewCreateRequest,
    AiInterviewDetailResponse,
    AiInterviewPublicState,
    AiInterviewSessionResponse,
)
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.services import ai_interview_engine as engine
from app.services.ai_interview_service import AiInterviewService

router = APIRouter(
    prefix="/ai-interviews",
    tags=["AI 面试"],
    responses=COMMON_ERROR_RESPONSES,
)

P = PermissionEnum


def get_service(db: DbSession) -> AiInterviewService:
    return AiInterviewService(db)


# ================================================================== 配置
@router.get(
    "/config",
    summary="AI 面试可选数字人与默认配置",
    response_model=SuccessResponse[AiInterviewConfigResponse],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def interview_config():
    from app.config.settings import settings

    personas = [
        {
            "key": key,
            "name": persona["name"],
            "title": persona["title"],
            "style": persona["style"],
            "tagline": persona.get("tagline", ""),
            "avatar_theme": persona["avatar_theme"],
            "language": persona["language"],
            "voice_hint": persona["voice_hint"],
        }
        for key, persona in engine.PERSONAS.items()
    ]
    return success(
        data={
            "personas": personas,
            "default_question_count": settings.AI_INTERVIEW_DEFAULT_QUESTION_COUNT,
            "max_question_count": settings.AI_INTERVIEW_MAX_QUESTION_COUNT,
            "default_duration_minutes": settings.AI_INTERVIEW_DEFAULT_DURATION_MINUTES,
            "default_expires_hours": settings.AI_INTERVIEW_LINK_TTL_HOURS,
            "languages": [
                {"value": "zh-CN", "label": "中文"},
                {"value": "en-US", "label": "English"},
            ],
            "difficulties": [
                {"value": "junior", "label": "初级"},
                {"value": "middle", "label": "中级"},
                {"value": "senior", "label": "高级"},
            ],
        }
    )


# ================================================================== host 侧
@router.post(
    "",
    summary="创建 AI 数字人面试并生成候选人链接",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_MANAGE))],
)
async def create_interview(
    body: AiInterviewCreateRequest,
    user: CurrentUser,
    db: DbSession,
):
    data = await AiInterviewService(db).create(body, user)
    return success(data=data, message="AI 面试已创建")


@router.get(
    "",
    summary="AI 面试分页列表",
    response_model=SuccessResponse[PageResult[AiInterviewSessionResponse]],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def list_interviews(
    page: PageParams = Depends(),
    status: str | None = Query(
        None,
        pattern=r"^(pending|in_progress|completed|cancelled|expired)$",
        description="状态",
    ),
    keyword: str | None = Query(None, max_length=64, description="候选人 / 岗位 / 标题模糊搜索"),
    talent_id: int | None = Query(None, gt=0),
    position_id: int | None = Query(None, gt=0),
    db: DbSession = None,
):
    result = await AiInterviewService(db).page_list(
        page, status=status, keyword=keyword, talent_id=talent_id, position_id=position_id
    )
    return success(data=result)


@router.post(
    "/{session_id}/cancel",
    summary="取消 AI 面试",
    response_model=SuccessResponse[AiInterviewSessionResponse],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_MANAGE))],
)
async def cancel_interview(session_id: int, user: CurrentUser, db: DbSession):
    data = await AiInterviewService(db).cancel(session_id, user)
    return success(data=data, message="面试已取消")


@router.get(
    "/{session_id}/report",
    summary="AI 面试评估报告",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def get_interview_report(session_id: int, db: DbSession):
    data = await AiInterviewService(db).report(session_id)
    return success(data=data)


@router.get(
    "/{session_id}",
    summary="AI 面试详情（含逐轮对话）",
    response_model=SuccessResponse[AiInterviewDetailResponse],
    dependencies=[Depends(require_permission(P.AI_INTERVIEW_READ))],
)
async def get_interview(session_id: int, db: DbSession):
    data = await AiInterviewService(db).detail(session_id)
    return success(data=data)


# ================================================================== 候选人公开（token）
@router.get(
    "/session/{token}",
    summary="【公开】获取 AI 面试状态（断点恢复）",
    response_model=SuccessResponse[AiInterviewPublicState],
)
async def public_interview_state(token: str, db: DbSession):
    data = await AiInterviewService(db).public_state(token)
    return success(data=data)


@router.post(
    "/session/{token}/start",
    summary="【公开】开始 AI 面试（幂等）",
    response_model=SuccessResponse[AiInterviewPublicState],
)
async def public_interview_start(token: str, db: DbSession):
    data = await AiInterviewService(db).start(token)
    return success(data=data, message="面试已开始")


@router.post(
    "/session/{token}/answer",
    summary="【公开】提交单题回答",
    response_model=SuccessResponse[AiInterviewAnswerResult],
)
async def public_interview_answer(token: str, body: AiInterviewAnswerRequest, db: DbSession):
    data = await AiInterviewService(db).answer(
        token,
        answer=body.answer,
        duration_seconds=body.duration_seconds,
        input_mode=body.input_mode,
    )
    return success(data=data, message="回答已收到")


@router.post(
    "/session/{token}/repeat",
    summary="【公开】让数字人重复当前问题",
    response_model=SuccessResponse[dict],
)
async def public_interview_repeat(token: str, db: DbSession):
    data = await AiInterviewService(db).repeat(token)
    return success(data=data)


@router.post(
    "/session/{token}/finish",
    summary="【公开】提前结束 AI 面试并生成报告",
    response_model=SuccessResponse[AiInterviewPublicState],
)
async def public_interview_finish(token: str, db: DbSession):
    data = await AiInterviewService(db).finish(token)
    return success(data=data, message="面试已结束")


__all__ = ["router"]
