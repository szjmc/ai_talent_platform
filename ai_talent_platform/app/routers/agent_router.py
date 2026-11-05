"""
模块：app/routers/agent_router.py
职责：AI 智能体接口层（对话 / SSE 流式 / 会话查询 / 任务派发 / 总路由信息）。

标签：[Agent组] [P1]  依赖：agent_service（LangGraph 总路由 + dispatch）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/agents", tags=["AI 智能体"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> AgentService:
        return AgentService(db)

    @router.post("/chat", response_model=ApiResponse[AgentChatResponse],
                 dependencies=[Depends(require_permission(PermissionEnum.AGENT_USE))])
    async def chat(body: AgentChatRequest, user: CurrentUser, service = Depends(get_service)):
        return success(data=await service.chat(body, current_user=user))

接口清单：
    GET    /agents                      智能体清单与总路由表（已注册 Agent / 关键词 / 图后端）
    POST   /agents/chat                 对话（非流式）
    POST   /agents/chat/stream          对话（SSE 流式输出：start/delta/done/error）
    GET    /agents/conversations        我的会话列表
    GET    /agents/conversations/{id}/messages  会话消息历史（越权 403/2004）
    POST   /agents/tasks                执行指定 Agent 任务（async=true 立即返回 task_id）
    GET    /agents/tasks/{task_id}      任务状态查询
实现要点：
- Router 不写 SQL、不写业务规则；参数校验交给 Schema，业务校验交给 Service；
- 每个接口都挂权限依赖：dependencies=[Depends(require_permission(PermissionEnum.AGENT_USE))]；
- SSE 用 StreamingResponse + text/event-stream（禁压缩、X-Accel-Buffering: no），
  事件格式与 frontend/src/services/api.ts 的 streamAgentChat 解析对齐：
  event 行 + data JSON 行 + 空行；delta.data 同时带 text 与 delta 键；
- 会话查询统一分页（PageParams + build_page_result），返回 PageResult。
========================================================================
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentConversationResponse,
    AgentMessageResponse,
    AgentTaskRequest,
    AgentTaskResponse,
)
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.services.agent_service import AgentService

router = APIRouter(
    prefix="/agents",
    tags=["AI 智能体"],
    responses=COMMON_ERROR_RESPONSES,
)

_AGENT_USE = [Depends(require_permission(PermissionEnum.AGENT_USE))]


def get_service(db: AsyncSession = Depends(get_db)) -> AgentService:
    """AgentService 依赖。"""
    return AgentService(db)


def _sse(event: str, data: Any) -> str:
    """SSE 帧：event 行 + data JSON 行 + 空行（前端按行解析）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ---------------------------------------------------------------- 智能体清单
@router.get(
    "",
    summary="智能体清单与总路由表",
    response_model=ApiResponse[dict[str, Any]],
    dependencies=_AGENT_USE,
)
async def list_agents(user: CurrentUser, service: AgentService = Depends(get_service)):
    """已注册 Agent、关键词路由表、图后端（langgraph/fallback）与步数上限。"""
    return success(data=await service.describe_routes(), message="查询成功")


# ---------------------------------------------------------------- 对话
@router.post(
    "/chat",
    summary="Agent 对话（非流式）",
    response_model=ApiResponse[AgentChatResponse],
    dependencies=_AGENT_USE,
)
async def chat(body: AgentChatRequest, user: CurrentUser, service: AgentService = Depends(get_service)):
    """LangGraph 总路由完整执行：路由 → dispatch → 聚合（Dify 双模式见 Service）。"""
    return success(data=await service.chat(body, current_user=user))


@router.post(
    "/chat/stream",
    summary="Agent 对话（SSE 流式输出）",
    dependencies=_AGENT_USE,
)
async def chat_stream(
    body: AgentChatRequest,
    user: CurrentUser,
    service: AgentService = Depends(get_service),
):
    """SSE：start → delta* → done；失败以 error 事件结束（HTTP 状态在流前已确定）。"""
    session = await service.prepare_chat(body, current_user=user)

    async def event_source() -> AsyncIterator[str]:
        async for item in service.stream_events(session):
            yield _sse(str(item.get("event") or "message"), item.get("data") or {})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------- 会话查询
@router.get(
    "/conversations",
    summary="我的会话列表",
    response_model=ApiResponse[PageResult[AgentConversationResponse]],
    dependencies=_AGENT_USE,
)
async def list_conversations(
    user: CurrentUser,
    page: PageParams = Depends(),
    service: AgentService = Depends(get_service),
):
    result = await service.list_conversations(user, page)
    return success(data=result, message="查询成功")


@router.get(
    "/conversations/{conversation_id}/messages",
    summary="会话消息历史",
    response_model=ApiResponse[PageResult[AgentMessageResponse]],
    dependencies=_AGENT_USE,
)
async def list_messages(
    conversation_id: str,
    user: CurrentUser,
    page: PageParams = Depends(),
    service: AgentService = Depends(get_service),
):
    """越权访问他人会话 → 403/2004；会话不存在 → 404/3001。"""
    result = await service.list_messages(conversation_id, user, page)
    return success(data=result, message="查询成功")


# ---------------------------------------------------------------- 任务
@router.post(
    "/tasks",
    summary="执行指定 Agent 任务",
    response_model=ApiResponse[AgentTaskResponse],
    dependencies=_AGENT_USE,
)
async def submit_task(
    body: AgentTaskRequest,
    user: CurrentUser,
    service: AgentService = Depends(get_service),
):
    """{"agent_type":"resume","payload":{...},"async":true} → 立即返回 task_id。"""
    return success(data=await service.submit_task(body, current_user=user), message="任务已提交")


@router.get(
    "/tasks/{task_id}",
    summary="任务状态查询",
    response_model=ApiResponse[AgentTaskResponse],
    dependencies=_AGENT_USE,
)
async def get_task_status(
    task_id: str,
    user: CurrentUser,
    service: AgentService = Depends(get_service),
):
    return success(data=await service.get_task_status(task_id, current_user=user))
