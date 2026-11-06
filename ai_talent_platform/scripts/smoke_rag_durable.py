"""并发防重和中断恢复验证，仅清理本次 UUID 测试资源。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import delete
from app.config.settings import settings
from app.services.knowledge_service import KnowledgeService
from app.infrastructure.database.session import get_session_factory,close_db
from app.infrastructure.vector.milvus import close_milvus
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.models.knowledge import KnowledgeDoc,KnowledgeChunk
from app.models.file import File
from app.repositories.rag_ingest_job import IngestJob
from scripts.recover_rag_jobs import recover

async def main():
    settings.RAG_DURABLE_INGEST_ENABLED=True
    marker=uuid4().hex
    print('TEST_RUN',marker,flush=True)
    data=('并发联调测试'+marker+'：Python FastAPI MySQL').encode()
    user=SimpleNamespace(id=None,is_superuser=False)
    storage=get_storage('minio')
    doc_id=file_id=key=None
    logs=[]
    async def upload():
        nonlocal doc_id,file_id,key
        async with get_session_factory()() as db:
            service=KnowledgeService(db)
            try:
                result=await service.upload(data,marker+'.txt','text/plain','联调测试',user)
                assert result['status']=='indexed',result
                doc_id,file_id=result['id'],result['file_id']
                file=await db.get(File,file_id)
                key=file.file_path
                await db.commit()
                logs.extend(job.path for job in service.ingest_jobs)
                await service.finish_jobs(True)
                return result['id']
            finally:
                await db.rollback()
                await service.finish_jobs(False)
    try:
        ids=await asyncio.gather(upload(),upload())
        assert ids[0]==ids[1],ids
        print('CONCURRENT_DEDUP_PASS',flush=True)
        # 模拟写完对象但进程未提交即退出：持久化 pending，断开锁连接，再由独立恢复入口扫描。
        job=await IngestJob.start('crash-test:'+marker)
        logs.append(job.path)
        orphan='_rag_ingest_test/'+marker+'.txt'
        await job.record(object_key=orphan)
        await storage.client.put_object(orphan,b'non-business crash test','text/plain')
        await job.close()
        await recover()
        assert not await storage.exists(orphan)
        print('CRASH_PENDING_RECOVERY_PASS',flush=True)
        # 模拟 commit 成功但确认日志前退出，恢复任务必须保留对象。
        job=await IngestJob.start('committed-test:'+marker)
        logs.append(job.path)
        await job.record(doc_id=doc_id,object_key=key)
        await job.close()
        await recover()
        assert await storage.exists(key)
        print('COMMITTED_DATA_PRESERVED_PASS',flush=True)
        await recover()
        print('RECOVERY_IDEMPOTENT_PASS',flush=True)
    finally:
        if doc_id:
            await MilvusAdapter().delete_document(doc_id)
            async with get_session_factory()() as db:
                await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id==doc_id))
                await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id==doc_id))
                await db.execute(delete(File).where(File.id==file_id))
                await db.commit()
        await storage.delete("_rag_ingest_test/"+marker+".txt")
        if key: await storage.delete(key)
        for path in logs: await storage.delete(path)
        await close_db()
        await close_milvus()
if __name__=='__main__': asyncio.run(main())
