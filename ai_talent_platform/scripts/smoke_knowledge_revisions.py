"""真实版本接口联调；不建表，只创建并清理本次唯一标识的测试数据。
标签：[RAG组] [P1]
运行：db312/python -m scripts.smoke_knowledge_revisions
"""
import asyncio
import io
import json
from uuid import uuid4
from unittest.mock import patch
from docx import Document
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
from app.repositories.rag_ingest_job import PREFIX
from app.services.knowledge_revision_recovery import recover_revision_job


async def main():
    run = uuid4().hex[:12]
    doc_ids, users = [], []
    storage = get_storage('minio')
    results = []

    def passed(name):
        results.append(name)
        print('PASS', name, flush=True)

    try:
        async with get_session_factory()() as db:
            for suffix in ('owner', 'other'):
                user = User(username='revision_' + run + suffix, password_hash=hash_password(uuid4().hex),
                    is_active=True, is_superuser=True)
                user.roles = []
                db.add(user)
                await db.flush()
                users.append(user.id)
            await db.commit()
        headers = {'Authorization': 'Bearer ' + create_access_token(users[0])}
        app = create_application()
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test', timeout=180) as client:
            async def call(method, path, expected=200, **kwargs):
                response = await client.request(method, '/api/v1/knowledge' + path, headers=headers, **kwargs)
                assert response.status_code == expected, (path, response.status_code, response.text[:800])
                if expected != 200:
                    return None
                return response.json().get('data')

            async def retrieve(mode='keyword'):
                rows = await call('POST', '/retrieve', json={'question': run + ' 培训报销上限',
                    'category': 'training', 'mode': mode, 'top_k': 20})
                return [row for row in rows if row['doc_id'] in doc_ids]

            original = '版本联调' + run + '，培训报销上限为2000元，请提交发票。'
            draft = await call('POST', '/docs/upload', data={'title': run, 'category': 'training'},
                files={'file': ('revision.txt', original.encode(), 'text/plain')})
            doc_ids.append(draft['doc_id'])
            assert draft['status'] == 'draft' and draft['body'] == original
            assert not await retrieve()
            passed('上传仅保存草稿，正文可读且未进入检索')
            await call('POST', '/revisions/%s/publish' % draft['revision_id'], expected=409,
                json={'expected_version': draft['version']})
            body = original.replace('2000', '3000')
            edited = await call('POST', '/revisions/%s/edit' % draft['revision_id'], json={
                'expected_version': draft['version'], 'title': run, 'category': 'training', 'body': body})
            assert edited['revision_no'] == 2 and edited['parent_revision_id'] == draft['revision_id']
            assert (await call('GET', '/revisions/%s' % draft['revision_id']))['body'] == original
            response = await client.get('/api/v1/knowledge/revisions/%s/file' % edited['revision_id'], headers=headers)
            assert response.status_code == 200
            assert body in '\n'.join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            passed('编辑另存新版本及Word，来源正文保持不变，未预览禁止发布')
            preview = await call('POST', '/revisions/%s/preview' % edited['revision_id'],
                json={'expected_version': edited['version']})
            assert preview['chunks'] and not await retrieve()
            await call('POST', '/revisions/%s/publish' % edited['revision_id'], expected=409,
                json={'expected_version': edited['version']})
            published = await call('POST', '/revisions/%s/publish' % edited['revision_id'],
                json={'expected_version': preview['version']})
            hits = await retrieve('hybrid')
            assert hits and all(h['revision_id'] == published['revision_id'] and '3000' in h['content'] for h in hits)
            passed('预览不入库、乐观锁冲突保护、发布后混合检索含版本引用')

            newer = await call('POST', '/revisions/%s/edit' % published['revision_id'], json={
                'expected_version': published['version'], 'title': run, 'category': 'training',
                'body': body.replace('3000', '4000')})
            next_preview = await call('POST', '/revisions/%s/preview' % newer['revision_id'],
                json={'expected_version': newer['version']})
            original_upsert = MilvusAdapter.upsert
            async def fail_after_write(*args, **kwargs):
                await original_upsert(*args, **kwargs)
                raise RuntimeError('INJECTED_FAILURE_AFTER_VECTOR_WRITE')
            with patch.object(MilvusAdapter, 'upsert', fail_after_write):
                await call('POST', '/revisions/%s/publish' % newer['revision_id'], expected=500,
                    json={'expected_version': next_preview['version']})
            assert all('3000' in h['content'] for h in await retrieve('hybrid'))
            assert (await call('GET', '/revisions/%s' % newer['revision_id']))['status'] == 'previewed'
            passed('写入向量后模拟故障，SQL回滚且检索仍只看到旧版')
            # 只恢复本次测试文档的任务，不扫描/清理其他人的任务。
            async def recover_ours():
                for key in await storage.client.list_keys(PREFIX):
                    value = json.loads(await storage.read(key))
                    if value.get('doc_id') in doc_ids and value['state'] == 'pending':
                        async with get_session_factory()() as db:
                            await recover_revision_job(db, value)
                            await db.commit()
                        value['state'] = 'done'
                        await storage.client.put_object(key, json.dumps(value).encode(), 'application/json')
            await recover_ours()
            published_new = await call('POST', '/revisions/%s/publish' % newer['revision_id'],
                json={'expected_version': next_preview['version']})
            assert all('4000' in h['content'] for h in await retrieve('hybrid'))
            await recover_ours()
            passed('故障任务可补偿、重试发布成功、旧向量按切片ID清理')
            history = await call('GET', '/docs/%s/revisions' % doc_ids[0])
            old = next(row for row in history['items'] if row['revision_id'] == published['revision_id'])
            assert old['status'] == 'archived'
            restored = await call('POST', '/revisions/%s/restore' % old['revision_id'],
                json={'expected_version': old['version']})
            assert restored['body'] == body and restored['status'] == 'draft'
            assert all('4000' in h['content'] for h in await retrieve())
            restored_preview = await call('POST', '/revisions/%s/preview' % restored['revision_id'],
                json={'expected_version': restored['version']})
            await call('POST', '/revisions/%s/publish' % restored['revision_id'],
                json={'expected_version': restored_preview['version']})
            assert all('3000' in h['content'] for h in await retrieve('hybrid'))
            await recover_ours()
            await call('POST', '/revisions/%s/discard' % draft['revision_id'],
                json={'expected_version': draft['version']})
            await call('POST', '/revisions/%s/preview' % draft['revision_id'], expected=409,
                json={'expected_version': draft['version'] + 1})
            listed = await call('GET', '/docs?category=training')
            assert any(row['doc_id'] == doc_ids[0] for row in listed['items'])
            passed('历史版本恢复需再次确认发布、草稿取消、文档与版本列表')
            anonymous = await client.get('/api/v1/knowledge/revisions/%s' % draft['revision_id'])
            assert anonymous.status_code == 401
            passed('版本接口要求认证')
        print('ALL_PASS', len(results), flush=True)
    finally:
        # 限定本次doc_id及用户ID；不运行任何建表/删表语句。
        async with get_session_factory()() as db:
            files = list((await db.scalars(select(File).where(
                File.biz_type == 'knowledge_revision', File.biz_id.in_(doc_ids)))).all()) if doc_ids else []
            resources = [(f.file_path, f.storage_type) for f in files]
            if doc_ids:
                await db.execute(delete(KnowledgeRevision).where(KnowledgeRevision.doc_id.in_(doc_ids)))
                await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(doc_ids)))
                await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(doc_ids)))
            if files:
                await db.execute(delete(File).where(File.id.in_([f.id for f in files])))
            if users:
                await db.execute(delete(User).where(User.id.in_(users)))
            await db.commit()
        for key, backend in resources:
            await get_storage(backend).delete(key)
        removed_keys = {key for key, _ in resources}
        for doc_id in doc_ids:
            await MilvusAdapter().delete_document(doc_id)
            for key in await storage.client.list_keys('_rag_bm25/v2/%s/' % doc_id):
                await storage.delete(key)
        for key in await storage.client.list_keys(PREFIX):
            value = json.loads(await storage.read(key))
            if value.get('doc_id') in doc_ids:
                for item in value.get('objects', []):
                    if item['key'] not in removed_keys:
                        await get_storage(item['backend']).delete(item['key'])
                await storage.delete(key)
        await close_db()
        await close_milvus()
        print('TEST_DATA_CLEANED', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
