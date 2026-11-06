# 上传使用同一个 multipart 接口。单份走草稿事务，多份逐份自动发布，调用方按 items 检查结果。
"""知识接入与检索接口；复用公共权限与统一响应。
标签：[RAG组] [P1]。"""
from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.core.dependencies import require_permission, DbSession
from app.core.permissions import PermissionEnum
from app.core.response import success, ApiResponse
from app.config.settings import settings
from app.schemas.knowledge import RetrieveRequest
from app.schemas.question_bank import QuestionSearchRequest, QuestionSearchResponse, QuestionImportResponse
from app.services.question_bank_service import QuestionBankService
from app.services.question_bank_import_service import QuestionBankImportService
from app.services.knowledge_service import KnowledgeService
from app.core.rag_constants import CategoryLiteral

from urllib.parse import quote
from fastapi import Query
from fastapi.responses import Response
from app.schemas.knowledge_revision import RevisionEdit, RevisionAction
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

@router.post("/docs/{doc_id}/retry", summary="重试失败文档索引", description="根据知识文档ID读取已保存的原件，重新执行解析、分块、向量化和索引写入。需要知识库管理权限且为创建人或超级管理员；已成功索引的文档直接返回，正在索引的文档拒绝重复触发。doc_id是文档ID，不是文件ID。")
async def retry_document(doc_id: int, user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)), service=Depends(get_service, scope="function")):
    """重试已保存原件的失败索引。"""
    return success(data=await service.retry(doc_id, user))


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


@router.post('/docs/upload', summary='单份草稿或批量上传知识文档', description='file可选择多份文件，整批必须且只能选择一个七大分类category。单份保存草稿，人工检查正文、预览切片并确认发布；多份逐份自动解析、切片、向量化并发布，无需人工确认，返回total、success_count、failed_count和逐份items，部分失败不撤销已成功文件。title省略时各自使用文件名；doc_id为空时各自新建文档，填写时沿用已有文档追加版本（多份顺序发布，最后成功版本生效）。需要知识库管理及文档归属权限。')
async def upload_document(file: list[UploadFile] = File(..., description='一份保存草稿，多份自动发布；所有文件共用category',
                 json_schema_extra={'items': {'type': 'string', 'format': 'binary'}}), title: str | None = Form(None, min_length=1, max_length=255),
                 category: CategoryLiteral = Form(...), doc_id: int | None = Form(None, ge=1),
                 context=Depends(get_revision_service, scope='function')):
    """单文件保存草稿，多文件逐份解析并自动发布。"""
    service, user = context
    # 保留multipart字段名file，旧单份客户端无需更名；按文件数选择处理流程。
    if len(file) == 1:
        item = file[0]
        data = await item.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
        return success(data=await service.upload(data, item.filename or 'document.txt',
            item.content_type, category, title if title is not None else (item.filename or 'document.txt')[:255], user, doc_id),
            message='草稿已保存，请检查正文和切片预览后确认发布')
    results = []
    # 逐份读取并独立提交，避免一份失败回滚整批，或同时把多份大文件读入内存。
    for item in file:
        name = item.filename or 'document.txt'
        try:
            data = await item.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
            result = await service.upload_and_publish(data, name, item.content_type, category,
                title if title is not None else name[:255], user, doc_id)
        except Exception as exc:
            result = KnowledgeRevisionService.batch_error(name, exc)
        results.append(result)
    count = sum(item['status'] == 'published' for item in results)
    return success(data={'total': len(results), 'success_count': count,
        'failed_count': len(results) - count, 'category': category, 'items': results},
        message='批量处理完成，请检查每份文件的处理结果')


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


@router.post('/revisions/{revision_id}/edit', summary='修改正文并另存新版本', description='基于指定版本编辑完整正文、标题和分类，生成新的Word文件和版本记录，原版本保持不变。expected_version必须匹配详情中的version，新版本需重新预览切片后发布。')
async def edit(revision_id: int, request: RevisionEdit, context=Depends(get_revision_service, scope='function')):
    """将编辑后的正文保存为新草稿。"""
    service, user = context
    return success(data=await service.edit(revision_id, request, user))


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


@router.post('/revisions/{revision_id}/publish', summary='确认发布文档版本', description='校验预览与并发版本，准备新向量及BM25快照后，在事务中切换当前内容；失败不影响旧版。旧向量由恢复任务延迟按精确切片ID清理。expected_version取预览返回值；当前同步等待发布完成。')
async def publish(revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope='function')):
    """发布已确认的切片快照，并切换可检索版本。"""
    service, user = context
    return success(data=await service.publish(revision_id, request, user))


@router.post('/revisions/{revision_id}/restore', summary='从历史版本恢复为新草稿', description='基于历史正文生成新的Word文件和草稿版本，不直接切换检索内容。需重新检查切片并确认发布，恢复过程保留完整版本链。')
async def restore(revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope='function')):
    """根据历史版本另存新草稿。"""
    service, user = context
    return success(data=await service.restore(revision_id, request, user))


@router.post('/revisions/{revision_id}/discard', summary='取消未发布草稿', description='归档未发布草稿，保留版本文件和来源关系，不影响当前已发布版本；已发布版本不能通过本接口取消。')
async def discard(revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope='function')):
    """归档未发布草稿。"""
    service, user = context
    return success(data=await service.discard(revision_id, request, user))


@router.get('/revisions/{revision_id}/file', summary='下载版本文件或来源原件', description='source=false下载该版本文件，正文编辑后为新生成Word；source=true下载保留的来源原件。仅文档管理者可下载，不暴露MinIO对象键或凭证。')
async def download(revision_id: int, source: bool = False, context=Depends(get_revision_service)):
    """按权限下载版本文件或原始上传文件。"""
    service, user = context
    data, name, mime = await service.download(revision_id, user, source)
    return Response(content=data, media_type=mime or 'application/octet-stream',
        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})


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


@router.post(
    '/questions/upload',
    summary='上传并导入笔试题库',
    description='上传UTF-8编码的JSON题库文件，内容为1至10000道题目的数组。每题携带bank_id、external_id、technology、difficulty、question_type、stem、answer，选择题另需options；支持单选、多选、判断、填空、简答和编程题。整批校验通过后按bank_id+external_id幂等写入，归属人为当前登录用户。sync_index默认true，数据库提交后仅同步本批题目向量；返回导入数量、题目ID、已同步/失败/待同步数量，索引失败保留题目供重试。需要knowledge:manage权限；普通管理者不可覆盖他人题目。仅导入结构化题库，不创建测评试卷。',
    response_model=ApiResponse[QuestionImportResponse],
    response_model_exclude_none=True,
)
async def upload_question_bank(
    file: UploadFile = File(..., description='固定格式JSON题库文件，支持UTF-8及UTF-8 BOM',
                            json_schema_extra={'type': 'string', 'format': 'binary'}),
    sync_index: bool = Form(True, description='是否在导入提交后同步本批题目向量，默认开启'),
    user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),
):
    """校验题库文件，提交题目后同步本批向量。"""
    data = await file.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    result = await QuestionBankImportService().upload(data, file.filename or '', user, sync_index)
    message = '题库导入完成'
    if result.failed_count or result.pending_count or result.sync_error:
        message = '题目已导入，请查看索引同步结果'
    return success(data=result, message=message)

