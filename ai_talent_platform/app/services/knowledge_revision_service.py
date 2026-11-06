# 版本处理分为原件与正文保存、切片预览、索引发布。批量上传逐份提交，发布失败后仍可用已保存的版本继续操作。
"""文档版本流程：完整正文编辑、版本文件、预览与原子发布。
标签：[RAG组] [P0]
"""
import asyncio
import hashlib
import io
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from langchain_core.documents import Document
from app.config.settings import settings
from app.core.exceptions import BusinessException, DataConflictError, FileError, NotFoundError, ParamError, PermissionDeniedError
from app.core.rag_constants import collection_for_category
from app.models.knowledge import KnowledgeDoc, KnowledgeChunk
from app.models.knowledge_revision import KnowledgeRevision
from app.repositories.knowledge_revision_repository import KnowledgeRevisionRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.rag_ingest_job import IngestJob
from app.services.file_service import FileService
from app.ai.rag.document_loader import load
from app.ai.rag.document_splitter import RESUME_SPLIT_VERSION, SECTION_SPLIT_VERSION, split_for_index
from app.ai.rag.embedding_service import EmbeddingService
from app.ai.core.model_selector import select_embedding_model
from app.adapters.vector_store.milvus_adapter import MilvusAdapter


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def preview_fingerprint(revision):
    """正文、标题、分类或分块配置有变动，旧预览均不能发布。"""
    return digest(json.dumps([revision.content_hash, revision.title, revision.category,
        settings.CHUNK_STRATEGY, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP,
        settings.SEMANTIC_SPLIT_THRESHOLD, SECTION_SPLIT_VERSION,
        *([RESUME_SPLIT_VERSION] if revision.category == 'resume' else [])], ensure_ascii=False))


def make_word(title, body):
    from docx import Document as WordDocument
    word = WordDocument()
    word.add_heading(title, 0)
    for paragraph in body.split('\n'):
        word.add_paragraph(paragraph)
    output = io.BytesIO()
    word.save(output)
    return output.getvalue()


class KnowledgeRevisionService:
    def __init__(self, db):
        self.db = db
        self.repo = KnowledgeRevisionRepository()
        self.files = FileService(db)
        self.jobs = []
        self.asset_job = None
        self.doc_id = None

    @staticmethod
    def authorize(doc, user):
        if not user.is_superuser and 'admin' not in user.role_codes and doc.created_by != user.id:
            raise PermissionDeniedError('只能管理本人文档版本')

    async def document(self, doc_id, user, lock=False):
        doc = await self.repo.document(self.db, doc_id, lock)
        if doc is None:
            raise NotFoundError('知识文档不存在')
        self.authorize(doc, user)
        self.doc_id = doc.id
        return doc

    async def revision(self, revision_id, user, lock=False, expected=None):
        # 先定位所属文档并校验归属，再在写路径上用最新版本检查并发修改。
        row = await self.repo.revision(self.db, revision_id)
        if row is None:
            raise NotFoundError('文档版本不存在')
        doc = await self.document(row.doc_id, user, lock)
        if lock:
            row = await self.repo.revision(self.db, revision_id, True)
        if expected is not None and row.version != expected:
            raise DataConflictError('版本已被修改，请重新读取详情')
        return doc, row

    async def start_job(self, identity, **values):
        # 新流程强制先持久化意图，不依赖旧自动入库开关。
        job = await IngestJob.start(identity)
        self.jobs.append(job)
        await job.record(**values)
        return job

    async def artifact(self, data, name, mime, user, category):
        if self.asset_job is None:
            self.asset_job = await self.start_job('revision-assets:' + uuid4().hex,
                operation='revision_assets', doc_id=self.doc_id, objects=[])
        job = self.asset_job
        async def before_write(key):
            objects = list(job.values['objects'])
            objects.append({'key': key, 'backend': settings.STORAGE_BACKEND})
            await job.record(objects=objects)
        # 预览和发布清单是JSON内容的.txt文件，复用现有受控FileService，不放开用户上传扩展名。
        file = await self.files.upload(data, name, mime, user.id, category,
            before_write=before_write, biz_type='knowledge_revision')
        file.biz_id = self.doc_id
        await self.db.flush()
        return file

    async def json_file(self, value, name, user, category):
        return await self.artifact(json.dumps(value, ensure_ascii=False).encode(), name + '.txt',
            'application/json', user, category)

    async def read_json(self, file_id):
        file = await self.repo.file(self.db, file_id)
        if file is None:
            raise NotFoundError('版本内容文件不存在')
        return json.loads(await self.files.read_object(file))

    async def baseline(self, doc, user):
        """为旧自动入库文档补首版快照，只在开始版本管理时执行，不自动改旧内容。"""
        if doc.status != 'indexed' or await self.repo.published(self.db, doc.id):
            return
        chunks = await self.repo.chunks(self.db, doc.id)
        text = '\n'.join(c.content for c in chunks)
        body = await self.json_file({'text': text, 'documents': [{'content': c.content,
            'metadata': {'page': c.page_no}} for c in chunks]}, 'legacy-body', user, doc.category)
        snapshot = await self.json_file({'chunks': [{'content': c.content, 'metadata': {'page': c.page_no}}
            for c in chunks]}, 'legacy-preview', user, doc.category)
        manifest = await self.json_file({'chunk_ids': [c.id for c in chunks], 'doc_id': doc.id,
            'category': doc.category, 'embedding_model': doc.embedding_model}, 'legacy-manifest', user, doc.category)
        await self.repo.add(self.db, KnowledgeRevision(doc_id=doc.id,
            revision_no=await self.repo.next_number(self.db, doc.id), title=doc.title, category=doc.category,
            source_file_id=doc.file_id, file_id=doc.file_id, body_file_id=body.id,
            preview_file_id=snapshot.id, index_manifest=manifest.id, content_hash=digest(text),
            status='published', published_at=doc.indexed_at, created_by=user.id))

    async def upload(self, data, filename, mime, category, title, user, doc_id=None, preserve_failure=False):
        collection_for_category(category)
        if not title.strip():
            raise ParamError('标题不能为空')
        if doc_id is None:
            doc = await self.repo.add(self.db, KnowledgeDoc(title=title, category=category,
                source_type='file', status='draft', created_by=user.id))
        else:
            doc = await self.document(doc_id, user, True)
            await self.baseline(doc, user)
        self.doc_id = doc.id
        source = await self.artifact(data, filename, mime, user, category)
        parse_error = None
        try:
            with tempfile.TemporaryDirectory(prefix='revision-') as directory:
                path = Path(directory) / source.stored_name
                await asyncio.to_thread(path.write_bytes, data)
                documents = await asyncio.to_thread(load, str(path), {'file_name': source.file_name, 'category': category})
        except FileError as exc:
            if not preserve_failure:
                raise
            # 批量解析失败也保留已校验的原件和版本，便于下载、人工修正及再次处理。
            documents, parse_error = [], exc.message
        text = '\n\n'.join(d.page_content for d in documents)
        body = await self.json_file({'text': text, 'documents': [
            {'content': d.page_content, 'metadata': d.metadata} for d in documents]}, 'body', user, category)
        parent = await self.repo.published(self.db, doc.id)
        row = await self.repo.add(self.db, KnowledgeRevision(doc_id=doc.id,
            revision_no=await self.repo.next_number(self.db, doc.id), parent_revision_id=parent.id if parent else None,
            source_file_id=source.id, file_id=source.id, body_file_id=body.id, title=title,
            category=category, content_hash=digest(text), status='failed' if parse_error else 'draft',
            error=parse_error, created_by=user.id))
        if parse_error and doc.status != 'indexed':
            doc.status, doc.index_error = 'failed', parse_error
            await self.db.flush()
        result = await self.detail(row.id, user)
        if parse_error:
            result['error'] = parse_error
        return result

    async def upload_and_publish(self, data, filename, mime, category, title, user, doc_id=None):
        """批量中的单份文件独立提交：先保留原件，再自动预览并发布。

        不复用请求外层事务，保证后续文件失败不会撤销之前文件；调用方逐份调用，
        不积累整批文件正文或并发占满数据库连接。doc_id沿用单份上传语义。
        """
        from app.infrastructure.database.session import get_session_factory
        from app.schemas.knowledge_revision import RevisionAction
        result = None
        async with get_session_factory()() as db:
            service = KnowledgeRevisionService(db)
            committed = False
            try:
                result = await service.upload(data, filename, mime, category, title, user, doc_id,
                    preserve_failure=True)
                await db.commit()
                committed = True
            except Exception as exc:
                await db.rollback()
                return self.batch_error(filename, exc)
            finally:
                await self.finish_batch_job(service, committed)
            if result['status'] == 'failed':
                return {'file_name': filename, **result}

            # 自动处理也复用同一预览/发布链路，保留版本及切片快照，但不要求用户确认。
            service = KnowledgeRevisionService(db)
            committed = False
            try:
                stage = 'preview'
                preview = await service.preview(result['revision_id'],
                    RevisionAction(expected_version=result['version']), user)
                stage = 'publish'
                published = await service.publish(result['revision_id'],
                    RevisionAction(expected_version=preview['version']), user)
                await db.commit()
                committed = True
                return {'file_name': filename, **published, 'chunk_count': preview['chunk_count']}
            except Exception as exc:
                await db.rollback()
                # 第一阶段已提交，失败时仍可通过这些ID读取原件、编辑或重新预览发布。
                error = self.batch_error(filename, exc)
                error['stage'] = stage
                return {**result, **error, 'revision_status': result['status']}
            finally:
                await self.finish_batch_job(service, committed)

    @staticmethod
    def batch_error(filename, exc):
        import logging
        logging.getLogger(__name__).warning('批量文件处理失败 file=%s type=%s', filename, type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__))
        return {'file_name': filename, 'status': 'failed',
            'error': exc.message if isinstance(exc, BusinessException) else '文件处理失败，请通过trace_id查看服务日志',
            'error_type': type(exc).__name__}

    @staticmethod
    async def finish_batch_job(service, committed):
        import logging
        try:
            await service.finish(committed)
        except Exception:
            # 确认日志失败不能把已提交发布误报为失败，持久日志仍由恢复任务核对。
            logging.getLogger(__name__).exception('批量上传任务确认失败，恢复任务将继续处理')

    async def edit(self, revision_id, request, user):
        doc, parent = await self.revision(revision_id, user, True, request.expected_version)
        if not request.body.strip() or not request.title.strip():
            raise ParamError('正文和标题不能为空')
        # 每次保存形成新的不可覆盖记录和文件，来源版本保持原样供追溯。
        word = await asyncio.to_thread(make_word, request.title, request.body)
        file = await self.artifact(word, 'revision.docx',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document', user, request.category)
        body = await self.json_file({'text': request.body, 'documents': [{'content': request.body,
            'metadata': {'source': file.file_name}}]}, 'body', user, request.category)
        row = await self.repo.add(self.db, KnowledgeRevision(doc_id=doc.id,
            revision_no=await self.repo.next_number(self.db, doc.id), parent_revision_id=parent.id,
            source_file_id=parent.source_file_id, file_id=file.id, body_file_id=body.id,
            title=request.title, category=request.category, content_hash=digest(request.body),
            created_by=user.id, status='draft'))
        return await self.detail(row.id, user)

    async def detail(self, revision_id, user):
        _, row = await self.revision(revision_id, user)
        body = await self.read_json(row.body_file_id)
        return {**self.summary(row), 'body': body['text']}

    @staticmethod
    def summary(row):
        return {'revision_id': row.id, 'doc_id': row.doc_id, 'revision_no': row.revision_no,
            'parent_revision_id': row.parent_revision_id, 'version': row.version, 'title': row.title,
            'category': row.category, 'status': row.status, 'file_id': row.file_id,
            'source_file_id': row.source_file_id, 'content_hash': row.content_hash,
            'published_at': row.published_at, 'created_by': row.created_by, 'created_at': row.created_at}

    async def list(self, doc_id, user, offset, limit):
        await self.document(doc_id, user)
        rows, total = await self.repo.list_revisions(self.db, doc_id, offset, limit)
        return {'items': [self.summary(r) for r in rows], 'total': total}

    async def preview(self, revision_id, request, user):
        doc, row = await self.revision(revision_id, user, True, request.expected_version)
        if row.status not in {'draft', 'previewed', 'failed'}:
            raise DataConflictError('已发布或历史版本不可覆盖预览，请另存新版本')
        body = await self.read_json(row.body_file_id)
        documents = [Document(page_content=d['content'], metadata=d['metadata']) for d in body['documents']]
        chunks, parents = await split_for_index(documents, category=row.category)
        if not chunks:
            raise FileError('清洗后没有有效切片')
        # Excel每片保留来源标记；正文编辑后不伪造原PDF页码。
        for chunk in chunks:
            if 'sheet' in chunk.metadata:
                label = '工作表：' + str(chunk.metadata['sheet']) + '；行：' + str(chunk.metadata['row']) + '\n'
                if not chunk.page_content.startswith(label):
                    chunk.page_content = label + chunk.page_content
        fingerprint = preview_fingerprint(row)
        value = {'fingerprint': fingerprint, 'base_doc_version': doc.version, 'parents': parents,
            'chunks': [{'content': c.page_content, 'metadata': c.metadata} for c in chunks]}
        file = await self.json_file(value, 'preview', user, row.category)
        row.preview_file_id, row.preview_hash = file.id, fingerprint
        row.status, row.version = 'previewed', row.version + 1
        await self.db.flush()
        return {**self.summary(row), 'chunk_count': len(chunks), 'chunks': value['chunks']}

    async def publish(self, revision_id, request, user):
        # 同时校验版本记录和预览基线，阻止过期预览覆盖已经发布的内容。
        doc, row = await self.revision(revision_id, user, True, request.expected_version)
        if row.status == 'published':
            return self.summary(row)
        if row.status != 'previewed' or row.preview_hash != preview_fingerprint(row):
            raise DataConflictError('请基于当前正文和分块配置重新预览后发布')
        preview = await self.read_json(row.preview_file_id)
        if preview['base_doc_version'] != doc.version:
            raise DataConflictError('已有其他版本发布，请重新预览以确认当前内容')
        chunks = preview['chunks']
        vectors = await EmbeddingService().embed_texts([c['content'] for c in chunks])
        old = await self.repo.chunks(self.db, doc.id)
        previous = await self.repo.published(self.db, doc.id)
        # 文档行锁负责串行发布；每次操作独立命名锁，避免恢复任务与发布者反向等锁。
        job = await self.start_job('revision-publish:' + str(doc.id) + ':' + uuid4().hex, operation='revision_publish',
            doc_id=doc.id, revision_id=row.id, new_chunk_ids=[], old_chunk_ids=[c.id for c in old],
            not_before=time.time() + 60)
        # 当前SQL事务以外的读者仍看到旧切片；新向量必须由精确切片白名单隔离。
        await KnowledgeRepository().clear_chunks(self.db, doc.id)
        rows = [KnowledgeChunk(doc_id=doc.id, chunk_index=i, content=c['content'],
            page_no=c['metadata'].get('page')) for i, c in enumerate(chunks)]
        await KnowledgeRepository().save_chunks(self.db, rows)
        await job.record(new_chunk_ids=[c.id for c in rows])
        await MilvusAdapter().upsert([c.id for c in rows], vectors, doc.id, metadata=[
            {'file_id': row.file_id, 'category': row.category, 'content': c.content,
             'source': row.title, 'page_number': c.page_no} for c in rows])
        for chunk in rows:
            chunk.milvus_pk = chunk.id
        manifest = await self.json_file({'doc_id': doc.id, 'revision_id': row.id,
            'chunk_ids': [c.id for c in rows], 'category': row.category,
            'collection': collection_for_category(row.category), 'preview_hash': row.preview_hash,
            'chunk_strategy': settings.CHUNK_STRATEGY, 'chunk_size': settings.CHUNK_SIZE,
            'chunk_overlap': settings.CHUNK_OVERLAP,
            'embedding_model': select_embedding_model()}, 'manifest', user, row.category)
        # 先释放新表唯一键，再标记新版本；全部与旧表投影在同一个MySQL事务中提交。
        if previous:
            previous.status = 'archived'
            previous.version += 1
            await self.db.flush()
        row.status, row.version = 'published', row.version + 1
        row.index_manifest, row.published_by, row.published_at = manifest.id, user.id, datetime.now()
        doc.title, doc.category, doc.file_id = row.title, row.category, row.file_id
        doc.status, doc.chunk_count, doc.index_error = 'indexed', len(rows), None
        # 旧表DATETIME没有微秒精度，与提交后BM25版本指纹保持一致。
        doc.embedding_model, doc.indexed_at, doc.version = select_embedding_model(), datetime.now().replace(microsecond=0), doc.version + 1
        await self.db.flush()
        from app.ai.rag.bm25_index import search_index
        await search_index(self.db, KnowledgeRepository(), [doc], '', 1, strict=True)
        return self.summary(row)

    async def download(self, revision_id, user, source=False):
        _, row = await self.revision(revision_id, user)
        file = await self.repo.file(self.db, row.source_file_id if source else row.file_id)
        if file is None:
            raise NotFoundError('版本文件不存在')
        return await self.files.read_object(file), file.file_name, file.content_type

    async def preview_detail(self, revision_id, user, offset, limit):
        doc, row = await self.revision(revision_id, user)
        if row.preview_file_id is None:
            raise NotFoundError('尚未生成切片预览')
        preview = await self.read_json(row.preview_file_id)
        chunks = preview['chunks']
        return {**self.summary(row), 'chunks': chunks[offset:offset+limit], 'total': len(chunks),
            'preview_current': row.preview_hash == preview_fingerprint(row),
            'base_document_current': preview.get('base_doc_version') == doc.version}

    async def restore(self, revision_id, request, user):
        _, row = await self.revision(revision_id, user, True, request.expected_version)
        body = await self.read_json(row.body_file_id)
        from app.schemas.knowledge_revision import RevisionEdit
        return await self.edit(revision_id, RevisionEdit(expected_version=request.expected_version,
            title=row.title, category=row.category, body=body['text']), user)

    async def discard(self, revision_id, request, user):
        _, row = await self.revision(revision_id, user, True, request.expected_version)
        if row.status not in {'draft', 'previewed', 'failed'}:
            raise DataConflictError('仅未发布草稿可取消，历史版本仍保留供追溯')
        row.status, row.version = 'archived', row.version + 1
        await self.db.flush()
        return self.summary(row)

    async def finish(self, committed):
        errors = []
        for job in self.jobs:
            try:
                # 发布任务必须保留到恢复worker核实事务结果，延迟清旧向量。
                if committed and job.values.get('operation') == 'revision_assets':
                    await job.record(state='done')
            except Exception as exc:
                errors.append(exc)
            finally:
                try:
                    await job.close()
                except Exception as exc:
                    errors.append(exc)
        self.jobs.clear()
        if errors:
            raise errors[0]
