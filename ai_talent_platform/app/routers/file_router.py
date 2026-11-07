"""
模块：app/routers/file_router.py
职责：文件管理接口（/files）——把「文件」相关能力从知识库分组迁出，独立成组。

标签：[架构组] [P1]（文件管理分组：上传 / 版本文件 / 索引维护 / 题库导入）

============================ 本轮修正（重要） ============================
上一轮的做法是错的：为了填满 `/files` 分组，新建了一套 FileCenterService +
FileResponse + CRUD，等于在已经跑通的链路上又造了一个平行实现 —— 两套上传、
两套元数据、两套权限判断，长期必然分叉。

真正的做法是：**文件能力本来就已经实现**（KnowledgeService/KnowledgeRevisionService
/ QuestionBankImportService / IngestionService + FileService + files 表），
只是这些接口都挂在 `/knowledge/*` 与 `/ingestion/*` 路径下，于是 Swagger 里
「文件管理」分组是空的，而知识库分组里塞满了上传、下载、版本文件、题库导入等
与检索无关的写接口。

本轮把这些**已有实现原样迁移**到 `/files/*`（处理函数体不改逻辑，只换路径），
让分组与路径一致：
    POST /files/upload                              上传知识文档（原 /knowledge/docs/upload）
    POST /files/docs/{doc_id}/retry                 重试失败索引（原 /knowledge/docs/{doc_id}/retry）
    GET  /files/revisions/{revision_id}/download     下载版本文件/原件（原 /knowledge/revisions/{id}/file）
    POST /files/docs/{doc_id}/edit                  按文档修改正文另存草稿（版本基线由后端确定）
    POST /files/revisions/{revision_id}/edit        修改正文另存版本（旧入口，按 revision_id）
    POST /files/revisions/{revision_id}/publish     确认发布版本
    POST /files/revisions/{revision_id}/restore     历史版本恢复为新草稿
    POST /files/revisions/{revision_id}/discard     取消未发布草稿
    POST /files/questions/upload                    上传导入笔试题库（原 /knowledge/questions/upload）
    POST /files/index/delete                        删除文档索引（原 /ingestion/delete）

留在「知识库」的只保留检索与切片语义：
    /knowledge/retrieve、/knowledge/docs、/knowledge/docs/{id}/revisions、
    /knowledge/revisions/{id}（正文）、/knowledge/revisions/{id}/preview（生成/查看）、
    /knowledge/questions/search

注意：`/files/revisions/...` 等静态前缀路由必须先于任何 `/files/{file_id}` 形态的
动态路由注册，否则会被动态段吃掉（FastAPI 按注册顺序匹配）。
=========================================================================
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.config.settings import settings
from app.core.dependencies import require_permission
from app.core.permissions import PermissionEnum
from app.core.rag_constants import CategoryLiteral
from app.core.response import ApiResponse, success
from app.schemas.knowledge_revision import DocumentEdit, RevisionAction, RevisionEdit
from app.schemas.question_bank import QuestionImportResponse
from app.services.knowledge_revision_service import KnowledgeRevisionService
from app.services.question_bank_import_service import QuestionBankImportService
from app.routers.knowledge_router import get_revision_service, get_service
from app.services.ingestion_service import IngestionService
from app.schemas.rag import IndexDeleteRequest

router = APIRouter(prefix="/files", tags=["文件管理"])


# ============================================================================
# 简历自动入库（RAG 上传 -> 人才链路）
# ============================================================================
def _trace_id() -> str:
    """取当前请求的 trace_id，交给后台任务串起日志。"""
    try:
        from app.config.logging_config import trace_id_ctx

        return trace_id_ctx.get() or "-"
    except Exception:  # noqa: BLE001 - 日志模块未就绪不影响上传
        return "-"


def _queue_resume_ingest(tasks: BackgroundTasks, result: dict, user) -> dict:
    """原件已提交后调用：判断是否需要人才处理，需要就排后台任务。

    返回给前端的调度说明（scheduled / reason），不写库、不提交事务；
    身份只取当前登录用户 ID，角色在后台任务里从数据库重建。
    """
    from app.services.resume_ingest import plan, run_revision_ingest

    ingest = plan(result, user)
    if ingest.get("scheduled"):
        tasks.add_task(run_revision_ingest, ingest["revision_id"], user.id, _trace_id())
    return ingest


# ============================================================================
# 上传
# ============================================================================
@router.post(
    "/upload",
    summary="上传文件并建立文档草稿",
    description="上传知识文档原件。file可选择多份文件，整批必须且只能选择一个七大分类category。单份保存草稿，人工检查正文、预览切片并确认发布；多份逐份自动解析、切片、向量化并发布，无需人工确认，返回total、success_count、failed_count和逐份items，部分失败不撤销已成功文件。title省略时各自使用文件名；doc_id为空时各自新建文档，填写时沿用已有文档追加版本（多份顺序发布，最后成功版本生效）。category=resume时，原件与版本记录**提交成功后**自动触发一次人才处理（后台执行：原件→简历→人才档案→画像/向量），预览、发布、恢复都不会再次触发；返回值resume_ingest说明是否已排队及未排队原因（如缺少resume:parse权限）。需要知识库管理及文档归属权限。",
)
async def upload_document(
    tasks: BackgroundTasks,
    file: list[UploadFile] = File(
        ..., description="一份保存草稿，多份自动发布；所有文件共用category",
        json_schema_extra={"items": {"type": "string", "format": "binary"}},
    ),
    title: str | None = Form(None, min_length=1, max_length=255),
    category: CategoryLiteral = Form(...),
    doc_id: int | None = Form(None, ge=1),
    context=Depends(get_revision_service, scope="function"),
):
    """单文件保存草稿，多文件逐份解析并自动发布。"""
    service, user = context
    # 保留 multipart 字段名 file，旧单份客户端无需更名；按文件数选择处理流程。
    if len(file) == 1:
        item = file[0]
        data = await item.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
        result = await service.upload(
            data, item.filename or "document.txt", item.content_type, category,
            title if title is not None else (item.filename or "document.txt")[:255], user, doc_id,
        )
        # 先把原件 + 版本记录真正提交到 MySQL，再决定是否通知人才模块：
        # 依赖里的提交发生在响应之后，若在这里不提交，后台任务可能读到未提交的版本。
        await service.commit_upload()
        ingest = _queue_resume_ingest(tasks, result, user)
        data = {**result, "resume_ingest": ingest} if category == "resume" else result
        return success(
            data=data,
            message=("草稿已保存，简历已进入人才自动处理" if ingest.get("scheduled")
                     else "草稿已保存，请检查正文和切片预览后确认发布"),
        )
    results = []
    # 逐份读取并独立提交，避免一份失败回滚整批，或同时把多份大文件读入内存。
    for item in file:
        name = item.filename or "document.txt"
        try:
            data = await item.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
            result = await service.upload_and_publish(
                data, name, item.content_type, category,
                title if title is not None else name[:255], user, doc_id,
            )
        except Exception as exc:
            result = KnowledgeRevisionService.batch_error(name, exc)
        # upload_and_publish 内部已逐份提交草稿；提交成功（有 revision_id）才排人才处理。
        result["resume_ingest"] = _queue_resume_ingest(tasks, result, user)
        results.append(result)
    count = sum(item["status"] == "published" for item in results)
    return success(
        data={
            "total": len(results),
            "success_count": count,
            "failed_count": len(results) - count,
            "category": category,
            "items": results,
        },
        message="批量处理完成，请确认每份文件的处理结果",
    )


@router.post("/docs/{doc_id}/retry", summary="重试失败文档索引", description="根据知识文档ID读取已保存的原件，重新执行解析、分块、向量化和索引写入。需要知识库管理权限且为创建人或超级管理员；已成功索引的文档直接返回，正在索引的文档拒绝重复触发。doc_id是文档ID，不是文件ID。")
async def retry_document(
    doc_id: int,
    user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),
    service=Depends(get_service, scope="function"),
):
    """重试已保存原件的失败索引。"""
    return success(data=await service.retry(doc_id, user))


# ============================================================================
# 版本文件（编辑 / 发布 / 恢复 / 取消 / 下载）
# 这些操作都作用在「文件版本」上，因此归文件管理；切片预览仍属知识库。
# ============================================================================
@router.get("/revisions/{revision_id}/download", summary="下载版本文件或来源原件", description="source=false下载该版本文件，正文编辑后为新生成Word；source=true下载保留的来源原件。仅文档管理者可下载，不暴露MinIO对象键或凭证。")
async def download_revision_file(
    revision_id: int, source: bool = False, context=Depends(get_revision_service)
):
    """按权限下载版本文件或原始上传文件。"""
    service, user = context
    data, name, mime = await service.download(revision_id, user, source)
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)},
    )


@router.post("/revisions/{revision_id}/edit", summary="修改正文并另存新版本", description="基于指定版本编辑完整正文、标题和分类，生成新的Word文件和版本记录，原版本保持不变。expected_version必须匹配详情中的version，新版本需重新预览切片后发布。")
async def edit_revision(
    revision_id: int, request: RevisionEdit, context=Depends(get_revision_service, scope="function")
):
    """将编辑后的正文保存为新草稿。"""
    service, user = context
    return success(data=await service.edit(revision_id, request, user))


@router.post(
    "/docs/{doc_id}/edit",
    summary="按文档修改正文并另存新草稿",
    description="以文档为入口编辑正文，调用方只传 doc_id，不传版本ID。后端自动基于该文档「当前已发布且生效」的版本另存新草稿，不使用最新创建的版本或未发布草稿作为修改基础；title/category省略时沿用当前生效版本。原版本保持不变，新草稿需重新预览切片并确认发布。需要知识库管理权限及文档归属权限。",
)
async def edit_document(
    doc_id: int, request: DocumentEdit, context=Depends(get_revision_service, scope="function")
):
    """以文档为入口的编辑：版本基线由后端确定（当前已发布生效版本）。"""
    service, user = context
    return success(data=await service.edit_document(doc_id, request, user))


@router.post("/revisions/{revision_id}/publish", summary="确认发布文档版本", description="校验预览与并发版本，准备新向量及BM25快照后，在事务中切换当前内容；失败不影响旧版。旧向量由恢复任务延迟按精确切片ID清理。expected_version取预览返回值；当前同步等待发布完成。")
async def publish_revision(
    revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope="function")
):
    """发布已确认的切片快照，并切换可检索版本。"""
    service, user = context
    return success(data=await service.publish(revision_id, request, user))


@router.post("/revisions/{revision_id}/restore", summary="从历史版本恢复为新草稿", description="基于历史正文生成新的Word文件和草稿版本，不直接切换检索内容。需重新检查切片并确认发布，恢复过程保留完整版本链。")
async def restore_revision(
    revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope="function")
):
    """根据历史版本另存新草稿。"""
    service, user = context
    return success(data=await service.restore(revision_id, request, user))


@router.post("/revisions/{revision_id}/discard", summary="取消未发布草稿", description="归档未发布草稿，保留版本文件和来源关系，不影响当前已发布版本；已发布版本不能通过本接口取消。")
async def discard_revision(
    revision_id: int, request: RevisionAction, context=Depends(get_revision_service, scope="function")
):
    """归档未发布草稿。"""
    service, user = context
    return success(data=await service.discard(revision_id, request, user))


# ============================================================================
# 题库文件导入 / 索引维护
# ============================================================================
@router.post(
    "/questions/upload",
    summary="上传并导入笔试题库",
    description="上传UTF-8编码的JSON题库文件，内容为1至10000道题目的数组。每题携带bank_id、external_id、technology、difficulty、question_type、stem、answer，选择题另需options；支持单选、多选、判断、填空、简答和编程题。整批校验通过后按bank_id+external_id幂等写入，归属人为当前登录用户。sync_index默认true，数据库提交后仅同步本批题目向量；返回导入数量、题目ID、已同步/失败/待同步数量，索引失败保留题目供重试。需要knowledge:manage权限；普通管理者不可覆盖他人题目。仅导入结构化题库，不创建测评试卷。",
    response_model=ApiResponse[QuestionImportResponse],
    response_model_exclude_none=True,
)
async def upload_question_bank(
    file: UploadFile = File(
        ..., description="固定格式JSON题库文件，支持UTF-8及UTF-8 BOM",
        json_schema_extra={"type": "string", "format": "binary"},
    ),
    sync_index: bool = Form(True, description="是否在导入提交后同步本批题目向量，默认开启"),
    user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),
):
    """校验题库文件，提交题目后同步本批向量。"""
    data = await file.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    result = await QuestionBankImportService().upload(data, file.filename or "", user, sync_index)
    message = "题库导入完成"
    if result.failed_count or result.pending_count or result.sync_error:
        message = "题目已导入，请查看索引同步结果"
    return success(data=result, message=message)


@router.post(
    "/index/delete",
    status_code=202,
    summary="删除知识文档索引",
    description="按文件ID和分类受理删除，先提交文档逻辑删除，再由恢复任务清理向量。需要知识库管理权限及文档归属权限；HTTP202和deletion_pending表示已受理，不代表立即清理完成。此接口不物理删除原始文件。",
)
async def delete_index(
    request: IndexDeleteRequest,
    user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),
    service=Depends(get_service, scope="function"),
):
    """受理逻辑删除；202表示已受理，不代表远程向量已立即清理完成。"""
    bridge = IngestionService(service.db)
    # Service先记录持久化任务，再逻辑删除；恢复worker确认事务结果后清理向量。
    # 共享任务列表让外层get_service正确释放锁，并保留待补偿任务。
    bridge.ingest_jobs = service.ingest_jobs
    return success(
        data=await bridge.delete_document(request.file_id, request.category, user),
        code=202,
        message="删除已受理，向量由补偿任务清理",
    )
