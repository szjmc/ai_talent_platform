"""完整业务链路联调，数据库回滚，仅清理本次文档向量及对象。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME', 'Windows')
import asyncio
from uuid import uuid4
from sqlalchemy import select
from app.models.user import User
from app.models.file import File
from app.infrastructure.database.session import get_session_factory, close_db
from app.infrastructure.vector.milvus import close_milvus
from app.services.knowledge_service import KnowledgeService
from app.schemas.knowledge import RetrieveRequest
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter

async def main():
    factory = get_session_factory()
    key = None
    doc_id = None
    try:
        async with factory() as db:
            try:
                user = await db.scalar(select(User).where(User.is_deleted.is_(False), User.is_superuser.is_(True)).limit(1))
                if user is None:
                    # Service 层测试身份，不创建账号；HTTP 认证需独立验证。
                    from types import SimpleNamespace
                    user = SimpleNamespace(id=None, is_superuser=False, organization_id=None, role_codes=['employee'])
                service = KnowledgeService(db)
                marker = uuid4().hex
                content = '联调唯一标记' + marker + '：Python后端岗位需要掌握FastAPI、MySQL和异步编程。'
                result = await service.upload(content.encode('utf-8'), 'rag-test-' + marker + '.txt', 'text/plain', '联调测试', user)
                doc_id = result['id']
                record = await db.get(File, result['file_id'])
                key = record.file_path
                print('INGEST_RESULT',result,flush=True)
                assert result['status'] == 'indexed', result
                for mode in ['semantic','keyword','hybrid']:
                    request = RetrieveRequest(question=marker if mode == 'keyword' else 'Python后端岗位需要掌握什么？',category='联调测试',mode=mode,top_k=25)
                    hits = await service.search(request,user)
                    assert any(h['doc_id']==doc_id for h in hits), mode
                    print(mode.upper()+'_SEARCH_PASS',flush=True)
            finally:
                await db.rollback()
                if doc_id is not None:
                    await MilvusAdapter().delete_document(doc_id)
                if key is not None:
                    await get_storage().delete(key)
                print('TEST_DATA_ROLLED_BACK_AND_CLEANED',flush=True)
    finally:
        await close_db()
        await close_milvus()

if __name__ == '__main__':
    asyncio.run(main())
