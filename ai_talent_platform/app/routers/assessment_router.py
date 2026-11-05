"""
模块：app/routers/assessment_router.py
职责：人才测评接口层（人才测评）。

标签：[Vanna组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/assessments", tags=["人才测评"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> AssessmentService:
        return AssessmentService(db)

    @router.get("", summary="...", response_model=ApiResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: AssessmentService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /assessments              创建测评任务（assessment:manage）
    GET    /assessments              分页列表
    POST   /assessments/{id}/start   开始测评（返回题目，不含答案）
    POST   /assessments/{id}/submit  提交答案
    GET    /assessments/{id}/result  测评结果（分数/维度/等级/AI 报告）
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
from app.core.response import ApiResponse, PageResult, success
from app.routers._stub import not_implemented_response
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.services.assessment_service import AssessmentService

router = APIRouter(
    prefix="/assessments",
    tags=["人才测评"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> AssessmentService:
    """AssessmentService 依赖。"""
    return AssessmentService(db)


@router.get(
    "",
    summary="人才测评列表（占位）",
)
async def list_assessments():
    """占位接口：与 routers/_stub.py 同一 501 契约（未登录也能看到实现位置）。

    主责组实现后替换为真实分页查询，并挂回权限依赖：
        dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))]
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return not_implemented_response("人才测评", "Vanna组", "测评创建、答题、提交、结果分析（见 routers/assessment_router.py）")
