"""
模块：app/routers/dify_router.py
职责：Dify 中台对外接口——只做「取参数 -> 调 Service -> 包统一响应」，
      阻塞接口返回统一 ApiResponse，流式接口返回 SSE（StreamingResponse）。

标签：[Dify组] [P0]
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends,Path, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db,get_current_user_optional
from app.core.response import ApiResponse, success,PageResult
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.services.dify_service import DifyService,WorkflowInfoService

from app.core.pagination import PageParams
from app.models.user import User
from app.schemas.dify import (
    ChatRequest,
    WorkflowInfoCreate,
    WorkflowInfoResponse,
    WorkflowInfoUpdate,
    WorkflowRequest,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dify", tags=["Dify 中台"], responses=COMMON_ERROR_RESPONSES)

# SSE 响应头：关闭缓存与代理缓冲，保证逐事件实时下发
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


# ------------------------------------------------------- 服务依赖
def get_dify_service(db: AsyncSession = Depends(get_db)) -> DifyService:
    return DifyService(db)


# ------------------------------------------------------- SSE 包装
async def _sse_event(event_stream: AsyncIterator[dict[str, object]]):
    """把业务事件流逐行转成 SSE；流式开始后无法走全局异常，错误在这里回传。"""
    try:
        async for event in event_stream:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as exc:  # noqa: BLE001
        payload = {"event": "error", "message": str(exc)}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ------------------------------------------------------- 工作流
@router.post(
    "/workflow/run",
    summary="运行工作流（阻塞）",
    response_model=ApiResponse[dict],
)
async def run_workflow(
    req: WorkflowRequest,
    service: DifyService = Depends(get_dify_service),
):
    outputs = await service.run_workflow(req.inputs, user=req.user, app=req.app)
    return success(data=outputs, message="工作流执行成功")


@router.post("/workflow/stream", summary="运行工作流（SSE 流式）")
async def run_workflow_stream(
    req: WorkflowRequest,
    service: DifyService = Depends(get_dify_service),
):
    event_stream = service.run_workflow_stream(req.inputs, user=req.user, app=req.app)
    return StreamingResponse(
        _sse_event(event_stream),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ------------------------------------------------------- 聊天
@router.post(
    "/chat",
    summary="聊天（阻塞）",
    response_model=ApiResponse[dict],
)
async def chat(
    req: ChatRequest,
    service: DifyService = Depends(get_dify_service),
):
    result = await service.chat(
        req.query,
        user=req.user,
        app=req.app,
        conversation_id=req.conversation_id,
        inputs=req.inputs,
    )
    return success(data=result, message="调用成功")


@router.post("/chat/stream", summary="聊天（SSE 流式）")
async def chat_stream(
    req: ChatRequest,
    service: DifyService = Depends(get_dify_service),
):
    event_stream = service.chat_stream(
        req.query,
        user=req.user,
        app=req.app,
        conversation_id=req.conversation_id,
        inputs=req.inputs,
    )
    return StreamingResponse(
        _sse_event(event_stream),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def get_workflow_info_service(
    db: AsyncSession = Depends(get_db),
) -> WorkflowInfoService:
    return WorkflowInfoService(db)


def _operator(user: User | None) -> int | None:
    return user.id if user is not None else None


@router.post(
    "/workflow-configs",
    summary="新增工作流配置",
    response_model=ApiResponse[WorkflowInfoResponse],
)
async def create_workflow_config(
    data: WorkflowInfoCreate,
    reveal: bool = Query(False, description="true 时响应返回明文 wf_apikey"),
    service: WorkflowInfoService = Depends(get_workflow_info_service),
    user: User | None = Depends(get_current_user_optional),
):
    return success(
        data=await service.create(data, operator=_operator(user), reveal=reveal),
        message="创建成功",
    )


@router.get(
    "/workflow-configs",
    summary="工作流配置列表（分页）",
    response_model=ApiResponse[PageResult[WorkflowInfoResponse]],
)
async def list_workflow_configs(
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=100, description="按名称/说明模糊搜索"),
    workflow_name: str | None = Query(None, max_length=100, description="按名称精确筛选"),
    include_deleted: bool = Query(False, description="是否包含已逻辑删除的记录"),
    service: WorkflowInfoService = Depends(get_workflow_info_service),
    user: User | None = Depends(get_current_user_optional),
):
    result = await service.page_list(
        page,
        keyword=keyword,
        workflow_name=workflow_name,
        include_deleted=include_deleted,
        reveal=False,  # 列表一律脱敏
    )
    return success(data=result)


@router.get(
    "/workflow-configs/{record_id}",
    summary="按 id 查询单条配置",
    response_model=ApiResponse[WorkflowInfoResponse],
)
async def get_workflow_config(
    record_id: int = Path(..., gt=0, description="记录唯一标识 id"),
    include_deleted: bool = Query(False, description="是否可查已逻辑删除的记录"),
    reveal: bool = Query(False, description="true 时返回明文 wf_apikey"),
    service: WorkflowInfoService = Depends(get_workflow_info_service),
    user: User | None = Depends(get_current_user_optional),
):
    return success(
        data=await service.get_detail(
            record_id, include_deleted=include_deleted, reveal=reveal
        )
    )


@router.put(
    "/workflow-configs/{record_id}",
    summary="更新工作流配置（全量）",
    response_model=ApiResponse[WorkflowInfoResponse],
    operation_id="update_workflow_config_full",
)
@router.patch(
    "/workflow-configs/{record_id}",
    summary="更新工作流配置（部分字段）",
    response_model=ApiResponse[WorkflowInfoResponse],
    operation_id="update_workflow_config_partial",
)
async def update_workflow_config(
    data: WorkflowInfoUpdate,
    record_id: int = Path(..., gt=0, description="记录唯一标识 id"),
    reveal: bool = Query(False, description="true 时响应返回明文 wf_apikey"),
    service: WorkflowInfoService = Depends(get_workflow_info_service),
    user: User | None = Depends(get_current_user_optional),
):
    return success(
        data=await service.update(record_id, data, operator=_operator(user), reveal=reveal),
        message="更新成功",
    )


@router.delete(
    "/workflow-configs/{record_id}",
    summary="删除工作流配置（默认逻辑删除）",
    response_model=ApiResponse[dict],
)
async def delete_workflow_config(
    record_id: int = Path(..., gt=0, description="记录唯一标识 id"),
    hard: bool = Query(False, description="true=物理删除且不可恢复；默认 false=逻辑删除"),
    service: WorkflowInfoService = Depends(get_workflow_info_service),
    user: User | None = Depends(get_current_user_optional),
):
    result = await service.delete(record_id, operator=_operator(user), hard=hard)
    return success(data=result, message="删除成功")


__all__ = ["router"]