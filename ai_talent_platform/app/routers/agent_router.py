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
from pydantic import Field
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission, require_roles
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
from app.schemas.common import COMMON_ERROR_RESPONSES, BaseSchema
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


@router.delete(
    "/conversations/{conversation_id}",
    summary="删除会话",
    response_model=ApiResponse[dict],
    dependencies=_AGENT_USE,
)
async def delete_conversation(
    conversation_id: str,
    user: CurrentUser,
    service: AgentService = Depends(get_service),
):
    """逻辑删除会话及其消息（越权 → 403/2004；不存在 → 404/3001）。"""
    await service.delete_conversation(conversation_id, user)
    return success(data={"deleted": True, "conversation_id": conversation_id}, message="会话已删除")


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

# ========================================================================
# 以下为 [Agent组] 报告类接口：周期报告 + 关键岗位继任方案（规则引擎，不走 chat）
# ========================================================================


class PeriodReportRequest(BaseSchema):
    """周期报告请求：只指定周期类型，系统按固定模板定时生成，不接受用户自然语言提问。"""

    report_type: str = Field(
        ..., description="周期类型：weekly(周报) / monthly(月报) / yearly(年报)"
    )
    force: bool = Field(
        False, description="是否强制重新生成（默认读缓存；true 时忽略缓存重新计算）"
    )


class SuccessionRequest(BaseSchema):
    """继任方案请求：指定关键岗位，系统按规则引擎自动计算继任梯队。"""

    post_name: str = Field(
        ..., min_length=1, max_length=64, description="关键岗位名，如：研发负责人"
    )
    force: bool = Field(
        False, description="是否强制重新计算（默认读缓存；true 时重新查库计算）"
    )


@router.post(
    "/report/period",
    summary="周期报告（周报/月报/年报）",
    dependencies=[Depends(require_roles("super_admin", "admin", "hr", "manager"))],
)
async def period_report(
    req: PeriodReportRequest,
    user: CurrentUser,
):
    """周期报告：按固定模板定时生成（周/月/年），不接受用户自然语言提问。

    业务说明：周期报告与「单次分析报告」是两类不同产物——
    单次分析由用户提问驱动（/agents/chat），周期报告由系统定时触发，
    板块骨架固定，取数问题由模板写死，与用户输入无关。

    返回结构：{insight: {...LLM业务推理结论...}, data: {charts, ...}, report_type, period_label, ...}
    与前端 analytics 页渲染契约一致。
    """
    from app.ai.workflows.talent_decision_report import PERIOD_REPORT_TEMPLATE, period_label
    from app.core.exceptions import ParamError
    from app.services.report_cache import get as cache_get
    from app.services.report_cache import set as cache_set
    from app.services.report_cache import touch_time as cache_time

    report_type = req.report_type.strip()
    if report_type not in PERIOD_REPORT_TEMPLATE:
        raise ParamError(
            f"不支持的周期类型：{report_type}，可选：weekly / monthly / yearly"
        )

    cache_key = f"period_v2:{report_type}"
    if not req.force:
        cached = cache_get(cache_key)
        if cached is not None:
            cached["from_cache"] = True
            cached["period_updated_at"] = cache_time(cache_key)
            return success(data=cached)

    # 通过 analytics Agent 生成：完成取数->出图->LLM业务推理->结构化洞察
    from app.ai.agent import dispatch
    from app.ai.context import set_context
    from app.core.permissions import get_data_scope

    set_context(user=user)
    label = period_label(report_type)
    dispatch_result = await dispatch(
        "analytics",
        payload={
            "question": f"生成人才决策{PERIOD_REPORT_TEMPLATE[report_type]['label']}（{label}）",
            "report_type": report_type,
        },
        user=user,
    )

    insight = {}
    data = {}
    if isinstance(dispatch_result, dict) and dispatch_result.get("ok"):
        inner = dispatch_result.get("result") or {}
        # analytics parse_output 返回的结构化结果作为 insight
        insight = {k: v for k, v in inner.items() if k != "answer"}
        # data.report 是 run_analysis 里 build_report 产出的含 charts 的原始报告
        data = inner.get("data") or {}
        if isinstance(data, dict) and "report" in data:
            # 把 report 里的 charts/risks 等提升到 data 顶层，便于前端读取
            report = data.get("report") or {}
            for k in ("charts", "chart_count", "chart_groups", "risks", "risk_count",
                     "missing", "row_count", "analysis_type"):
                if k in report and k not in data:
                    data[k] = report[k]

    result: dict[str, Any] = {
        "insight": insight,
        "data": data,
        "report_type": report_type,
        "period_label": label,
        "from_cache": False,
    }

    # 员工端输出脱敏
    from app.services.desensitize import is_employee, sanitize_for_employee

    if is_employee(user.role_codes):
        result = sanitize_for_employee(result)

    result["period_updated_at"] = cache_set(cache_key, result)
    return success(data=result)


@router.post(
    "/succession",
    summary="关键岗位继任方案",
    dependencies=[Depends(require_roles("super_admin", "admin", "hr", "manager"))],
)
async def succession(
    req: SuccessionRequest,
    user: CurrentUser,
):
    """关键岗位继任方案：按规则引擎计算继任梯队（准备度/空缺风险/建议动作）。"""
    from app.services.report_cache import get as cache_get
    from app.services.report_cache import set as cache_set
    from app.services.succession_service import SuccessionService

    post_name = req.post_name.strip()

    cache_key = f"succession:{post_name}"
    if not req.force:
        cached = cache_get(cache_key)
        if cached is not None:
            cached["from_cache"] = True
            return success(data=cached)

    result = await SuccessionService().generate(post_name, user)

    # 员工端输出脱敏
    from app.services.desensitize import is_employee, sanitize_for_employee

    if is_employee(user.role_codes):
        result = sanitize_for_employee(result)

    if isinstance(result, dict):
        result["from_cache"] = False
        cache_set(cache_key, result)

    return success(data=result)
