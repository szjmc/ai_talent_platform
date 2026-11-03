"""
模块：app/routers/knowledge_router.py
职责：知识库接口层（知识库）。

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/knowledge", tags=["知识库"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> KnowledgeService:
        return KnowledgeService(db)

    @router.get("", summary="...", response_model=SuccessResponse[PageResult[XxxResponse]],
                dependencies=[Depends(require_permission(PermissionEnum.XXX_READ))])
    async def list_xxx(page: PageParams = Depends(), service: KnowledgeService = Depends(get_service)):
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))

接口清单：

    POST   /knowledge/docs           新建文档 / 文本（knowledge:manage）
    POST   /knowledge/docs/upload    上传文件并触发索引
    GET    /knowledge/docs           文档列表（含索引状态）
    DELETE /knowledge/docs/{id}      删除文档（同步删除向量）
    POST   /knowledge/retrieve       向量检索（返回 chunk + 得分）
    POST   /knowledge/ask            RAG 问答（返回答案 + 引用）
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
from app.services.knowledge_service import KnowledgeService

router = APIRouter(
    prefix="/knowledge",
    tags=["知识库"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> KnowledgeService:
    """KnowledgeService 依赖。"""
    return KnowledgeService(db)


@router.get(
    "",
    summary="知识库列表（占位）",
    dependencies=[Depends(require_permission(PermissionEnum.KNOWLEDGE_READ))],
)
async def list_knowledge(page: PageParams = Depends(), service: KnowledgeService = Depends(get_service)):
    """占位接口：KnowledgeService 实现后替换为真实分页查询。

    替换示例：
        result = await service.page_list(page)
        return success(data=build_page_result(result.items, result.total, page))
    """
    return success(data=build_page_result([], 0, page), message="知识库模块待实现")
