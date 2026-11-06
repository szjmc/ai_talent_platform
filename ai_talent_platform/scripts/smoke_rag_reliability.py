"""故障注入验证：失败→重试、顺序去重及回滚补偿。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import select, func
from app.infrastructure.database.session import get_session_factory, close_db
from app.infrastructure.vector.milvus import close_milvus
from app.services.knowledge_service import KnowledgeService
from app.models.knowledge import KnowledgeChunk
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.ai.rag.embedding_service import EmbeddingService

async def main():
    try:
        async with get_session_factory()() as db:
            service=KnowledgeService(db)
            user=SimpleNamespace(id=None,is_superuser=False)
            marker=uuid4().hex
            content=('故障注入测试'+marker+'：掌握Python和FastAPI。').encode()
            key=None
            try:
                original=MilvusAdapter.upsert
                async def fail_after_write(self,*args,**kwargs):
                    await original(self,*args,**kwargs)
                    raise RuntimeError('injected after remote write')
                with patch.object(MilvusAdapter,'upsert',fail_after_write):
                    result=await service.upload(content,marker+'.txt','text/plain','联调测试',user)
                assert result['status']=='failed',result
                assert await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.doc_id==result['id']))==0
                print('PARTIAL_WRITE_FAILURE_PASS',flush=True)
                result=await service.retry(result['id'],user)
                assert result['status']=='indexed',result
                print('RETRY_PASS',flush=True)
                duplicate=await service.upload(content,marker+'.txt','text/plain','联调测试',user)
                assert duplicate['id']==result['id']
                print('SEQUENTIAL_DUPLICATE_PASS',flush=True)
                key=service.created_assets[0]['file'].file_path
                await db.rollback()
                await service.compensate_rollback()
                assert not await get_storage().exists(key)
                vector=await EmbeddingService().embed_query('Python')
                assert not await MilvusAdapter().search(vector,[result['id']],5)
                print('ROLLBACK_COMPENSATION_PASS',flush=True)
            finally:
                await db.rollback()
                await service.compensate_rollback()
    finally:
        await close_db()
        await close_milvus()
if __name__=='__main__': asyncio.run(main())
