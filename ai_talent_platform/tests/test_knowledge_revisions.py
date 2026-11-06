"""版本流程的隔离、失效与补偿契约；不连接远程服务。
标签：[RAG组] [P1]
"""
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.services.knowledge_revision_service import KnowledgeRevisionService, preview_fingerprint
from app.core.exceptions import DataConflictError
from tests.test_collection_routing import Client


def test_version_routes_registered_with_chinese_descriptions():
    from app.core.application import create_application
    from app.routers.knowledge_router import router
    paths = create_application().openapi()['paths']
    assert len(router.routes) == 15
    assert '/api/v1/knowledge/drafts/upload' not in paths
    assert '/api/v1/files/upload' not in paths
    assert sum(route.path == '/knowledge/docs/upload' for route in router.routes) == 1
    for route in router.routes:
        for method in route.methods:
            operation = paths['/api/v1' + route.path][method.lower()]
            assert operation['description']
            assert operation['tags'] == ['知识库']
            assert any('\u4e00' <= c <= '\u9fff' for c in operation['summary'])


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/v1/knowledge/docs/upload', '/api/v1/files/upload'])
async def test_original_upload_urls_use_draft_service(path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.routers.knowledge_router import router, get_revision_service
    from app.routers.a_rag_router import router as files_router
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.include_router(files_router, prefix='/api/v1')
    service = NS(upload=AsyncMock(return_value={'doc_id': 1, 'revision_id': 2, 'status': 'draft'}))
    user = NS(id=3)
    async def context():
        yield service, user
    app.dependency_overrides[get_revision_service] = context
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(path, data={'category': 'training'},
            files={'file': ('example.txt', b'preview first', 'text/plain')})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'draft'
    service.upload.assert_awaited_once_with(b'preview first', 'example.txt', 'text/plain', 'training', 'example.txt', user, None)


@pytest.mark.asyncio
async def test_semantic_search_uses_exact_current_chunks():
    client = Client()
    await MilvusAdapter(client).search([1, 0], [9], categories=['training'], active_chunks=[(9, 11), (10, 12)])
    assert len(client.queries) == 1
    assert 'chunk_id in ["ragdoc-9-chunk-11"]' in client.queries[0]['filter']
    assert 'like' not in client.queries[0]['filter']
    client.queries.clear()
    assert await MilvusAdapter(client).search([1, 0], [9], categories=['training'], active_chunks=[]) == []
    assert client.queries == []


@pytest.mark.asyncio
async def test_compensation_deletes_only_exact_ids_in_three_collections():
    client = Client()
    await MilvusAdapter(client).delete_chunks(9, [11, 12])
    assert len(client.deletes) == 3
    assert all(item['filter'] == 'chunk_id in ["ragdoc-9-chunk-11", "ragdoc-9-chunk-12"]' for item in client.deletes)


@pytest.mark.asyncio
async def test_stale_preview_rejected_before_embedding():
    service = KnowledgeRevisionService(NS())
    row = NS(status='previewed', content_hash='body', title='标题', category='training', preview_file_id=1)
    row.preview_hash = preview_fingerprint(row)
    service.revision = AsyncMock(return_value=(NS(version=5), row))
    service.read_json = AsyncMock(return_value={'base_doc_version': 4, 'chunks': []})
    with pytest.raises(DataConflictError, match='其他版本发布'):
        await service.publish(1, NS(expected_version=2), NS())


@pytest.mark.asyncio
async def test_all_job_locks_closed_even_when_confirmation_fails():
    service = KnowledgeRevisionService(NS())
    first = NS(values={'operation': 'revision_assets'}, record=AsyncMock(side_effect=OSError('offline')), close=AsyncMock())
    second = NS(values={'operation': 'revision_publish'}, close=AsyncMock())
    service.jobs = [first, second]
    with pytest.raises(OSError):
        await service.finish(True)
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    assert service.jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize('committed,expected', [(True, [1]), (False, [3])])
async def test_recovery_preserves_current_sql_chunks(monkeypatch, committed, expected):
    from app.services import knowledge_revision_recovery as recovery
    repo = NS(document=AsyncMock(return_value=NS(status='draft')), revision=AsyncMock(return_value=NS(index_manifest=8 if committed else None)),
        file=AsyncMock(return_value=NS()), chunks=AsyncMock(return_value=[NS(id=2)]))
    monkeypatch.setattr(recovery, 'KnowledgeRevisionRepository', lambda: repo)
    # FileService.read(file_id) 与 read_object(record) 已拆分：恢复流程用后者（直接读已加载记录）
    files = NS(read_object=AsyncMock(return_value=json.dumps({'chunk_ids': [2, 3]})))
    monkeypatch.setattr(recovery, 'FileService', lambda db: files)
    store = NS(delete_chunks=AsyncMock())
    monkeypatch.setattr(recovery, 'MilvusAdapter', lambda: store)
    await recovery.recover_revision_job(None, {'operation': 'revision_publish', 'doc_id': 9,
        'revision_id': 6, 'new_chunk_ids': [2, 3], 'old_chunk_ids': [1, 2]})
    store.delete_chunks.assert_awaited_once_with(9, expected)


@pytest.mark.asyncio
async def test_strict_bm25_does_not_trust_memory_cache(monkeypatch):
    from app.ai.rag import bm25_index as index
    index._cache.clear()
    storage = NS(exists=AsyncMock(return_value=False), client=NS(put_object=AsyncMock()))
    monkeypatch.setattr(index, 'get_storage', lambda *_: storage)
    repo = NS(lexical_corpus=AsyncMock(return_value=[NS(id=1, content='培训')]))
    doc = NS(id=1, version=2, indexed_at=None, chunk_count=1)
    await index.search_index(None, repo, [doc], '培训', 1)
    storage.client.put_object.side_effect = OSError('offline')
    with pytest.raises(OSError):
        await index.search_index(None, repo, [doc], '', 1, strict=True)
    index._cache.clear()


@pytest.mark.asyncio
async def test_strict_bm25_replaces_snapshot_left_by_rolled_back_attempt(monkeypatch):
    from app.ai.rag import bm25_index as index
    index._cache.clear()
    storage = NS(exists=AsyncMock(return_value=True), read=AsyncMock(return_value=b'[{"id":99,"content":"old","tokens":["old"]}]'),
        client=NS(put_object=AsyncMock()))
    monkeypatch.setattr(index, 'get_storage', lambda *_: storage)
    repo = NS(lexical_corpus=AsyncMock(return_value=[NS(id=1, content='培训')]))
    doc = NS(id=1, version=2, indexed_at=None, chunk_count=1)
    await index.search_index(None, repo, [doc], '培训', 1, strict=True)
    payload = json.loads(storage.client.put_object.call_args.args[1])
    assert payload[0]['id'] == 1
    storage.read.assert_not_awaited()
    index._cache.clear()
