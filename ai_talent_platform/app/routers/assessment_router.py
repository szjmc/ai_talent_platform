"""
模块：app/routers/assessment_router.py
职责：人才测评接口层（人才测评）。

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/assessments", tags=["人才测评"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> AssessmentService:
        return AssessmentService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
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
from app.core.response import PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
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
    dependencies=[Depends(require_permission(PermissionEnum.ASSESSMENT_READ))],
)
async def list_assessments(page: PageParams = Depends(), service: AssessmentService = Depends(get_service)):
    """占位接口：AssessmentService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="人才测评模块待实现")
