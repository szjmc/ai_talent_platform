"""
模块：app/routers/agent_router.py
职责：AI 智能体接口层（AI 智能体）。

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/agents", tags=["AI 智能体"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> AgentService:
        return AgentService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: AgentService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /agents/chat              对话（非流式）
    POST   /agents/chat/stream       对话（SSE 流式输出）
    GET    /agents/conversations     我的会话列表
    GET    /agents/conversations/{id}/messages  会话消息历史
    POST   /agents/tasks             执行指定 Agent 任务（resume/talent/matching/...）
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
from app.services.agent_service import AgentService

router = APIRouter(
    prefix="/agents",
    tags=["AI 智能体"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> AgentService:
    """AgentService 依赖。"""
    return AgentService(db)


@router.get(
    "",
    summary="AI 智能体列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.AGENT_USE))],
)
async def list_agents(page: PageParams = Depends(), service: AgentService = Depends(get_service)):
    """占位接口：AgentService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="AI 智能体模块待实现")
