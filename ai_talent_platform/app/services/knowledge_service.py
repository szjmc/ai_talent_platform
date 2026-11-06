# 该服务维护兼容入库、索引重试和检索入口。旧流程的资源补偿与版本发布分别管理。
"""知识文档入库编排；不写 SQL，不处理 HTTP。
标签：[RAG组] [P0]。"""
import asyncio
import logging
from app.utils.file_utils import get_file_hash
from app.core.exceptions import NotFoundError, PermissionDeniedError, DataConflictError
logger = logging.getLogger(__name__)
import tempfile
from pathlib import Path
from datetime import datetime
from app.models.knowledge import KnowledgeDoc, KnowledgeChunk
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.file_service import FileService
from app.ai.rag.document_loader import load
from app.ai.rag.document_splitter import split_for_index
from app.ai.rag.embedding_service import EmbeddingService
from app.ai.rag.retriever import Retriever
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.ai.core.model_selector import select_embedding_model

class KnowledgeService:
    def __init__(self, db):
        self.db = db
        self.repo = KnowledgeRepository()
        self.files = FileService(db)
        self.created_assets = []
        self.ingest_jobs = []

    @staticmethod
    def _result(doc):
        return {"id":doc.id,"file_id":doc.file_id,"category":doc.category,"status":doc.status,"chunk_count":doc.chunk_count,"index_error":doc.index_error}

    async def upload(self, data, filename, content_type, category, user):
        from app.core.rag_constants import collection_for_category
        # 在创建原件与任务之前拒绝未知分类，Service调用也遵守同一契约。
        collection_for_category(category)
        from app.config.settings import settings
        if settings.RAG_DURABLE_INGEST_ENABLED and not self.ingest_jobs:
            from app.repositories.rag_ingest_job import IngestJob
            identity = str(user.id) + ":" + category + ":" + get_file_hash(data)
            self.ingest_jobs.append(await IngestJob.start(identity))
        existing = await self.repo.find_upload(self.db, get_file_hash(data), category, user.id)
        if existing is not None:
            if existing.status == "indexed":
                return self._result(existing)
            return await self.retry(existing.id, user)
        async def before_write(key):
            await self.ingest_jobs[-1].record(object_key=key, storage_type=settings.STORAGE_BACKEND)
        file = await self.files.upload(data, filename, content_type, user.id, category, before_write=before_write if self.ingest_jobs else None)
        self.created_assets.append({"file": file, "doc_id": None})
        doc = KnowledgeDoc(title=file.file_name, category=category, source_type="file", file_id=file.id, status="indexing", created_by=user.id)
        self.db.add(doc)
        await self.db.flush()
        self.created_assets[-1]["doc_id"] = doc.id
        if self.ingest_jobs:
            await self.ingest_jobs[-1].record(doc_id=doc.id)
        return await self._ingest(doc, file, data)

    async def retry(self, doc_id, user):
        from app.config.settings import settings
        if settings.RAG_DURABLE_INGEST_ENABLED and not self.ingest_jobs:
            from app.repositories.rag_ingest_job import IngestJob
            self.ingest_jobs.append(await IngestJob.start("retry:" + str(doc_id)))
        # 行锁防止同一失败文档被多个请求同时重建切片。
        doc = await self.repo.lock_document(self.db, doc_id)
        if doc is None:
            raise NotFoundError("知识文档不存在")
        if not user.is_superuser and doc.created_by != user.id:
            raise PermissionDeniedError("只能重试自己的文档")
        if doc.status == 'draft':
            # 兼容入口不能绕过草稿确认，也不登记旧式整文档补偿任务。
            raise DataConflictError('草稿请使用版本切片预览和确认发布接口')
        if self.ingest_jobs:
            await self.ingest_jobs[-1].record(doc_id=doc.id)
        if doc.status == "indexed":
            return self._result(doc)
        if doc.status != "failed":
            raise DataConflictError("文档正在索引")
        file = doc.file
        if file is None or file.is_deleted:
            raise NotFoundError("原文件不存在")
        data = await self.files.read_object(file)
        doc.status = "indexing"
        doc.index_error = None
        return await self._ingest(doc, file, data)

    async def _ingest(self, doc, file, data):
        if self.ingest_jobs:
            await self.ingest_jobs[-1].record(doc_id=doc.id)
        store = MilvusAdapter()
        category = doc.category
        try:
            # 保存点失败只撤销切片，不丢失文档记录；保留原件供重试。
            async with self.db.begin_nested():
                await self.repo.clear_chunks(self.db, doc.id)
                await store.delete_document(doc.id)
                await self._index(doc, file, data, category, store)
        except Exception as exc:
            doc.status = "failed"
            doc.chunk_count = 0
            doc.index_error = type(exc).__name__ + ": 索引失败，可重试"
            try:
                await store.delete_document(doc.id)
            except Exception:
                doc.index_error += "; cleanup_pending"
                logger.exception("向量补偿失败 doc_id=%s", doc.id)
        await self.db.flush()
        if self.ingest_jobs:
            await self.ingest_jobs[-1].record(outcome=doc.status)
        return self._result(doc)

    async def _index(self, doc, file, data, category, store):
        # 2. Loader 只处理临时文件，临时目录在成功或异常时都会自动清理。
        with tempfile.TemporaryDirectory(prefix="rag-") as temp:
            path = Path(temp) / file.stored_name
            await asyncio.to_thread(path.write_bytes, data)
            documents = await asyncio.to_thread(load, str(path), {"file_id": file.id, "file_name": file.file_name, "doc_id": doc.id, "category": category})
            from app.config.settings import settings
            chunks, _ = await split_for_index(documents, category=category)
        # Excel 来源随每个切片进入正文，兼容现有无 sheet 字段的固定表结构。
        for chunk in chunks:
            if "sheet" in chunk.metadata:
                label = "工作表：" + str(chunk.metadata["sheet"]) + "；行：" + str(chunk.metadata["row"]) + "\n"
                if not chunk.page_content.startswith(label):
                    chunk.page_content = label + chunk.page_content
        if not chunks:
            raise ValueError("清洗后没有可索引文本")
        # 3. MySQL 分配切片主键；同一主键写入 Milvus，可回表取得可信原文。
        vectors = await EmbeddingService().embed_texts([c.page_content for c in chunks])
        rows = [KnowledgeChunk(doc_id=doc.id, chunk_index=i, content=c.page_content, page_no=c.metadata.get("page")) for i,c in enumerate(chunks)]
        await self.repo.save_chunks(self.db, rows)
        await store.upsert([c.id for c in rows], vectors, doc.id, metadata=[
            {"file_id": file.id, "category": category, "content": c.content,
             "source": file.file_name, "page_number": c.page_no} for c in rows
        ])
        for row in rows:
            row.milvus_pk = row.id
        # 4. 只有全部写入成功才能对检索可见，失败文档不会进入召回白名单。
        doc.status = "indexed"
        doc.chunk_count = len(rows)
        doc.embedding_model = select_embedding_model()
        doc.indexed_at = datetime.now()

    async def search(self, request, user):
        return await Retriever(self.db).search(
            request.question, user, request.category, request.top_k, request.mode,
            request.score_threshold, request.use_rerank,
            result_unit=request.result_unit, doc_id=request.doc_id,
        )

    async def compensate_rollback(self):
        """只清理本请求新建资源；不删除重试文档原件。调用方必须先回滚数据库。"""
        from app.adapters.storage.base_storage import get_storage
        for asset in self.created_assets:
            if asset["doc_id"] is not None:
                try:
                    await MilvusAdapter().delete_document(asset["doc_id"])
                except Exception:
                    logger.exception("回滚后向量清理失败 doc_id=%s", asset["doc_id"])
            file = asset["file"]
            try:
                await get_storage(file.storage_type).delete(file.file_path)
            except Exception:
                logger.exception("回滚后对象清理失败 key=%s", file.file_path)

    async def finish_jobs(self, committed):
        """提交确认后标记完成；回滚或不确定提交结果留给恢复任务核对。"""
        for job in self.ingest_jobs:
            try:
                if committed and job.values.get("outcome") != "failed":
                    # 只对已提交文档建快照；失败时保留pending由恢复worker重试。
                    if job.values.get("doc_id"):
                        from app.infrastructure.database.session import get_session_factory
                        from app.ai.rag.bm25_index import search_index
                        async with get_session_factory()() as index_db:
                            indexed_doc = await index_db.get(KnowledgeDoc, job.values["doc_id"])
                            if indexed_doc is not None and indexed_doc.status == "indexed":
                                await search_index(index_db, self.repo, [indexed_doc], "", 1)
                    await job.record(state='done')
            except Exception:
                logger.exception('补偿任务确认失败，保留 pending 供恢复')
            finally:
                await job.close()
        self.ingest_jobs.clear()
