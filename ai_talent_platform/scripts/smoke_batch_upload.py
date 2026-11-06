"""真实批量接口测试：合成Excel、部分失败与检索，清理本次数据。
标签：[RAG组] [P1]
"""
import asyncio
import io
import json
from uuid import uuid4
from openpyxl import Workbook
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete
from app.core.application import create_application
from app.core.security import create_access_token, hash_password
from app.models.user import User
from app.models.file import File
from app.models.knowledge import KnowledgeDoc, KnowledgeChunk
from app.models.knowledge_revision import KnowledgeRevision
from app.infrastructure.database.session import get_session_factory, close_db
from app.infrastructure.vector.milvus import close_milvus
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter

async def main():
    run = uuid4().hex
    uid = None
    storage = get_storage('minio')
    def excel(topic):
        book = Workbook()
        book.active.append(['测试标识', '课程', '课时'])
        book.active.append([run, topic, 12])
        output = io.BytesIO()
        book.save(output)
        return output.getvalue()
    try:
        async with get_session_factory()() as db:
            user = User(username='batch_'+run[:12], password_hash=hash_password(run), is_superuser=True, is_active=True)
            user.roles = []
            db.add(user)
            await db.flush()
            uid = user.id
            await db.commit()
        headers = {'Authorization': 'Bearer '+create_access_token(uid)}
        async with AsyncClient(transport=ASGITransport(app=create_application(), raise_app_exceptions=False), base_url='http://test') as client:
            for partial in [False, True]:
                files = [('file', ('python.xlsx', excel('Python培训'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
                    ('file', ('bad.xlsx' if partial else 'mysql.xlsx', b'broken xlsx' if partial else excel('MySQL培训'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))]
                response = await client.post('/api/v1/knowledge/docs/upload', headers=headers,
                    data={'category': 'training'}, files=files)
                assert response.status_code == 200, response.text[:500]
                result = response.json()['data']
                print('BATCH_RESULT', json.dumps(result, ensure_ascii=False, default=str), flush=True)
                assert result['success_count'] == (1 if partial else 2), result
                assert result['failed_count'] == (1 if partial else 0), result
                for row in result['items']:
                    if row['status'] == 'published':
                        detail = await client.get('/api/v1/knowledge/revisions/%s' % row['revision_id'], headers=headers)
                        assert detail.json()['data']['status'] == 'published'
            response = await client.post('/api/v1/knowledge/retrieve', headers=headers,
                json={'question': run, 'category': 'training', 'mode': 'hybrid', 'top_k': 25})
            assert response.status_code == 200, response.text[:500]
            assert any(run in row['content'] for row in response.json()['data'])
            print('BATCH_AND_HYBRID_PASS', flush=True)
    finally:
        if uid is not None:
            async with get_session_factory()() as db:
                ids = list((await db.scalars(select(KnowledgeDoc.id).where(KnowledgeDoc.created_by == uid))).all())
                files = list((await db.scalars(select(File).where(File.uploader_id == uid))).all())
                resources = [(f.file_path, f.storage_type) for f in files]
                await db.execute(delete(KnowledgeRevision).where(KnowledgeRevision.doc_id.in_(ids)))
                await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(ids)))
                await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(ids)))
                await db.execute(delete(File).where(File.uploader_id == uid))
                await db.execute(delete(User).where(User.id == uid))
                await db.commit()
            for key, backend in resources:
                await get_storage(backend).delete(key)
            for did in ids:
                await MilvusAdapter().delete_document(did)
                for key in await storage.client.list_keys('_rag_bm25/v2/%s/' % did):
                    await storage.delete(key)
            for prefix in ['_rag_ingest_jobs/v1/', '_rag_ingest_jobs/archive/v1/']:
                for key in await storage.client.list_keys(prefix):
                    value = json.loads(await storage.read(key))
                    if value.get('doc_id') in ids:
                        await storage.delete(key)
        await close_db()
        await close_milvus()
        print('TEST_DATA_CLEANED', flush=True)

if __name__ == '__main__':
    asyncio.run(main())
