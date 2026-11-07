# [接口分组治理] 本文件只保留「读语义内容」：检索、文档与版本正文/列表、切片预览、题库检索。
# 上传、版本文件流转、题库导入、索引删除已迁到 app/routers/file_router.py（「文件管理」分组），
# 处理函数原样复用 KnowledgeRevisionService / QuestionBankImportService，逻辑未变。
"""知识接入与检索接口；复用公共权限与统一响应。
标签：[RAG组] [P1]。"""
from fastapi import APIRouter, Depends, Query
from app.core.dependencies import require_permission, DbSession
from app.core.permissions import PermissionEnum
from app.core.response import success, ApiResponse
from app.config.settings import settings
from app.schemas.knowledge import RetrieveRequest
from app.schemas.question_bank import QuestionSearchRequest, QuestionSearchResponse
from app.services.question_bank_service import QuestionBankService
from app.services.knowledge_service import KnowledgeService
from app.core.rag_constants import CategoryLiteral

from app.schemas.knowledge_revision import RevisionAction
from app.core.pagination import PageParams
from app.services.knowledge_revision_service import KnowledgeRevisionService

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

@asynccontextmanager
async def recovery_lifespan(app):
    """启动入库补偿扫描，服务关闭时取消后台任务。"""
    async def worker():
        from app.services.rag_recovery import recover
        while True:
            try:
                await recover()
            except Exception:
                logging.getLogger(__name__).exception("入库补偿扫描失败，下轮重试")
            await asyncio.sleep(60)
    task = asyncio.create_task(worker()) if settings.RAG_DURABLE_INGEST_ENABLED else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

router = APIRouter(prefix="/knowledge", tags=["知识库"], lifespan=recovery_lifespan)

async def get_service():
    """管理兼容入库接口的事务及回滚补偿。"""
    # 显式包住 commit：提交失败也执行外部资源补偿。
    from app.infrastructure.database.session import get_session_factory
    async with get_session_factory()() as db:
        service = KnowledgeService(db)
        try:
            yield service
            await db.commit()
            await service.finish_jobs(committed=True)
        except BaseException:
            await db.rollback()
            # 持久化模式由恢复任务核实提交结果，避免网络中断时误删已提交数据。
            if not service.ingest_jobs:
                await service.compensate_rollback()
            raise
        finally:
            await service.finish_jobs(committed=False)

@router.post("/retrieve", summary="检索知识片段", description="在当前用户有权访问的文档中执行语义检索、中文BM25或混合检索，可按分类筛选、设置返回数量并启用模型重排。返回切片原文、来源、页码和得分。有章节快照时额外返回parent_content和heading_path。需要知识库读取权限；相似度阈值仅作用于语义召回。")
async def retrieve(request: RetrieveRequest, user=Depends(require_permission(PermissionEnum.KNOWLEDGE_READ)), service=Depends(get_service, scope="function")):
    """检索可见子片，结构化版本额外返回父片正文与标题路径。"""
    return success(data=await service.search(request, user))

# 草稿、正文编辑、切片预览和确认发布统一归属知识库，沿用原上传URL。
async def get_revision_service(user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE))):
    """管理版本事务，在提交结果确定后确认补偿任务。"""
    from app.infrastructure.database.session import get_session_factory
    async with get_session_factory()() as db:
        service = KnowledgeRevisionService(db)
        committed = False
        try:
            yield service, user
            await db.commit()
            committed = True
        except BaseException:
            await db.rollback()
            raise
        finally:
            try:
                await service.finish(committed)
            except Exception:
                logging.getLogger(__name__).exception('版本任务确认失败，持久日志由恢复任务继续处理')


@router.get('/docs/{doc_id}/revisions', summary='查询文档版本列表', description='返回同一文档的草稿、历史及当前生效版本，含来源版本、文件ID和乐观锁version。仅文档管理者可查看。')
async def list_revisions(doc_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                         context=Depends(get_revision_service)):
    """分页返回当前用户可管理的文档版本。"""
    service, user = context
    return success(data=await service.list(doc_id, user, offset, limit))


@router.get('/docs', summary='查询可管理文档列表', description='分页查询本人创建的文档，包括未发布草稿与当前已发布文档；管理员可查看全部。使用文档ID继续查询版本列表。')
async def documents(page: PageParams = Depends(), category: CategoryLiteral | None = None,
                    context=Depends(get_revision_service)):
    """分页返回可管理文档，包括草稿。"""
    service, user = context
    from app.repositories.knowledge_repository import KnowledgeRepository
    from app.models.knowledge import KnowledgeDoc
    where = [] if user.is_superuser or 'admin' in user.role_codes else [KnowledgeDoc.created_by == user.id]
    result = await KnowledgeRepository().paginate(service.db, page, filters={'category': category}, extra_where=where)
    result.items = [{'doc_id': d.id, 'title': d.title, 'category': d.category, 'status': d.status,
                     'file_id': d.file_id, 'chunk_count': d.chunk_count} for d in result.items]
    return success(data=result)


@router.get('/revisions/{revision_id}', summary='预览文档版本正文', description='读取完整正文、标题、分类、文件和来源版本。返回的version用于编辑、切片预览和发布时的并发校验。仅管理者可查看，草稿不参与检索。')
async def detail(revision_id: int, context=Depends(get_revision_service)):
    """读取版本正文及并发校验所需的版本号。"""
    service, user = context
    return success(data=await service.detail(revision_id, user))


@router.post('/revisions/{revision_id}/preview', summary='生成文档切片预览', description='按当前正文及配置生成切片快照。简历按技能、工作经历、项目经历等字段组织，超长内容在字段或经历内递归拆分。预览仅用于检查，不写入Milvus。正文、标题、分类或分块配置改变后必须重新生成。expected_version取最近详情返回值；本操作会递增version。')
async def preview(revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope='function')):
    """按当前章节切片规则生成快照，供发布前核对。"""
    service, user = context
    return success(data=await service.preview(revision_id, request, user))


@router.get('/revisions/{revision_id}/preview', summary='查看已保存的切片预览', description='分页查看已有预览，不重新分块。preview_current说明正文及分块配置是否匹配；base_document_current说明预览后是否已有其他版本发布。失效时须重新生成预览。')
async def get_preview(revision_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      context=Depends(get_revision_service)):
    """分页读取已生成的切片快照。"""
    service, user = context
    return success(data=await service.preview_detail(revision_id, user, offset, limit))


@router.post(
    '/questions/search',
    summary='按技术、等级和题型查询笔试题库',
    description='接收JSON选题条件。query非空时按知识点向量召回，否则按条件随机抽题；counts分别指定各题型数量，总数最多100。仅返回已发布且有权访问的题目，按实际数量报告缺额，不跨等级或题型补题。默认不返回答案；include_answers=true额外要求assessment:manage权限。本接口不创建测评或保存试卷快照。',
    response_model=ApiResponse[QuestionSearchResponse],
    response_model_exclude_none=True,
)
async def search_question_bank(
    request: QuestionSearchRequest,
    db: DbSession,
    user=Depends(require_permission(PermissionEnum.KNOWLEDGE_READ)),
):
    """按等级和题型配额从可见题库选题。"""
    result = await QuestionBankService(db).search(request, user)
    return success(data=result)


