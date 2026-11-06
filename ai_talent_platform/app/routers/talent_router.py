"""
模块：app/routers/talent_router.py
职责：人才档案接口层（人才档案）。

标签：[RAG组] [P1]（人才档案、画像、查重合并、语义检索与 RAG 问答接口）

说明：合并 origin/develop 时这里原有一行以三引号开头又接中文的残句，
会把模块 docstring 提前闭合，导致「、」被当成代码 → 整个应用 import 失败。
此处合并为单个 docstring，并补上守门测试要求的「标签：」行。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from app.core.dependencies import CurrentUser, DbSession, require_permission
from app.core.dependencies import CurrentUser, DbSession, require_permission
from app.core.pagination import PageParams
from app.core.response import ApiResponse, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse  # SuccessResponse 为合并时补齐（原文件使用但未导入）
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.talent import (
    TalentAskRequest,
    TalentCreate,
    TalentMergeRequest,
    TalentQuery,
    TalentSemanticSearchRequest,
    TalentUpdate,
)
from app.schemas.talent import TalentSemanticSearchRequest, TalentMergeRequest, TalentAskRequest
from app.services.talent_profile_service import TalentService
from app.services.talent_retrieval_service import TalentRetrievalService

router = APIRouter(prefix="/talents", tags=["人才档案"], responses=COMMON_ERROR_RESPONSES)

def get_service(db: DbSession, user: CurrentUser):
    """gxd-注入公共异步会话与可信登录身份。"""
    return TalentService(db, user=user)

def get_retrieval_service(db: DbSession, user: CurrentUser):
    """gxd-注入同一权限上下文用于语义检索与档案问答。"""
    return TalentRetrievalService(db, user=user)

@router.get("", summary="人才档案分页列表", response_model=ApiResponse, dependencies=[Depends(require_permission("talent:read"))])
async def list_talents(page: PageParams = Depends(), query: TalentQuery = Depends(), service: TalentService = Depends(get_service)):
    """gxd-根据权限、筛选和分页参数返回人才列表。"""
    return success(data=await service.page_list(page, query))

@router.post("", summary="创建人才档案", status_code=201, response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:create"))])
async def create_talent(data: TalentCreate, service: TalentService = Depends(get_service)):
    """gxd-保存新人才并直接尝试生成画像和向量。"""
    return success(data=service.public_data(await service.create_talent(data)), code=201)

@router.post("/search/semantic", summary="自然语言语义检索人才", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:read"))])
async def search_talent(data: TalentSemanticSearchRequest, service: TalentRetrievalService = Depends(get_retrieval_service)):
    """gxd-执行权限约束下的语义检索，返回相似度与命中证据。"""
    return success(data=await service.search(data))

@router.post("/ask", summary="单人或多人档案RAG问答", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:read"))])
async def ask_talent(data: TalentAskRequest, service: TalentRetrievalService = Depends(get_retrieval_service)):
    """gxd-仅在指定人才范围内检索，并返回带原文引用的回答。"""
    return success(data=await service.ask(data))

@router.post("/merge", summary="确认合并重复人才", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:update"))])
async def merge_talent(data: TalentMergeRequest, service: TalentService = Depends(get_service)):
    """gxd-保留主档、迁移关联、留存冲突与审计快照。"""
    return success(data=await service.merge(data))

@router.get("/{talent_id}", summary="人才档案详情", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:read"))])
async def talent_detail(talent_id: int, service: TalentService = Depends(get_service)):
    """gxd-查询人才资料和关联简历；索引状态未独立持久化。"""
    return success(data=await service.detail(talent_id))

@router.patch("/{talent_id}", summary="部分修改人才档案", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:update"))])
@router.put("/{talent_id}", summary="更新人才档案", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:update"))])
async def update_talent(talent_id: int, data: TalentUpdate, service: TalentService = Depends(get_service)):
    """gxd-只写入明确提交字段，并直接尝试更新画像和向量。"""
    return success(data=service.public_data(await service.update_talent(talent_id, data)))

@router.delete("/{talent_id}", summary="逻辑删除人才", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:delete"))])
async def delete_talent(talent_id: int, service: TalentService = Depends(get_service)):
    """gxd-逻辑删除档案，查询时排除其历史向量。"""
    await service.delete_talent(talent_id)
    return success(data={"talent_id":talent_id, "deleted":True})

@router.get("/{talent_id}/portrait", summary="查询最新人才画像", response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:read"))])
async def talent_portrait(talent_id: int, service: TalentService = Depends(get_service)):
    """gxd-返回最新画像版本及证据标签。"""
    return success(data=await service.portrait(talent_id))

@router.post("/{talent_id}/portrait/refresh", summary="重新生成画像", status_code=200, response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:update"))])
async def refresh_portrait(talent_id: int, service: TalentService = Depends(get_service)):
    """gxd-直接生成画像并同步向量，返回各阶段结果而非任务编号。"""
    return success(data=await service.refresh_derived(talent_id))

@router.post("/{talent_id}/index/refresh", summary="重建单个人才向量索引", status_code=200, response_model=ApiResponse[dict], dependencies=[Depends(require_permission("talent:update"))])
async def refresh_index(talent_id: int, service: TalentService = Depends(get_service)):
    """gxd-在模型或向量服务恢复后直接补同步，返回本次结果。"""
    return success(data=await service.refresh_derived(talent_id, portrait=False))
