# 恢复任务从持久日志判断待处理资源，通过命名锁避开进行中的请求，失败任务保留供后续扫描。
"""恢复 MinIO 入库日志；不创建或改变数据库表。
标签：[RAG组] [P0]。"""
import asyncio
import json
import time
from sqlalchemy import select, func
from app.repositories.rag_ingest_job import IngestJob, PREFIX, archive_completed_job
from app.infrastructure.database.session import get_engine,get_session_factory,close_db
from app.models.knowledge import KnowledgeDoc
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.infrastructure.vector.milvus import close_milvus

async def recover():
    storage=get_storage('minio')
    for path in await storage.client.list_keys(PREFIX):
        try:
            value=json.loads(await storage.read(path))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('单条补偿日志读取失败，保留待下轮恢复 key=%s',path)
            continue
        if value['state']!='pending':
            if value['state'] == 'done':
                try:
                    await archive_completed_job(storage, path, value)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('已完成任务归档失败，下轮重试 key=%s', path)
            continue
        if value.get('not_before', 0) > time.time():
            continue
        async with get_engine().connect() as conn:
            locked=await conn.scalar(select(func.GET_LOCK(value['lock_key'],0)))
            await conn.commit()
            if locked!=1:
                continue  # 上传还在执行，不能按时间猜测已崩溃。
            job=IngestJob(conn,value['lock_key'],value['id'])
            try:
                # 获锁后重读，避免清理已经提交并确认的任务。
                job.values=json.loads(await storage.read(path))
                if job.values['state']!='pending':
                    if job.values['state'] == 'done':
                        await archive_completed_job(storage, path, job.values)
                    continue
                value=job.values
                if value.get('operation') in {'revision_assets', 'revision_publish'}:
                    from app.services.knowledge_revision_recovery import recover_revision_job
                    async with get_session_factory()() as revision_db:
                        await recover_revision_job(revision_db, value)
                        await revision_db.commit()
                    await job.record(state='done', error=None)
                    continue
                async with get_session_factory()() as db:
                    doc=await db.scalar(select(KnowledgeDoc).where(KnowledgeDoc.id==value['doc_id']).with_for_update()) if value['doc_id'] else None
                    if doc is not None and doc.status=='indexed' and not doc.is_deleted:
                        from app.ai.rag.bm25_index import search_index
                        from app.repositories.knowledge_repository import KnowledgeRepository
                        await search_index(db, KnowledgeRepository(), [doc], "", 1)
                    else:
                        if value['doc_id']:
                            await MilvusAdapter().delete_document(value['doc_id'])
                        if doc is None and value['object_key']:
                            await get_storage(value['storage_type']).delete(value['object_key'])
                        if doc is not None:
                            doc.status='failed'
                            doc.chunk_count=0
                            doc.index_error='恢复任务已清理未完成索引，可重试'
                            await db.commit()
                await job.record(state='done',error=None)
                print('RECOVERED',job.id,flush=True)
            except Exception as exc:
                try:
                    await job.record(error=type(exc).__name__)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('补偿错误状态回写失败，保留原pending')
                print('RETRY_PENDING',job.id,type(exc).__name__,str(exc),flush=True)
            finally:
                await job.close()
