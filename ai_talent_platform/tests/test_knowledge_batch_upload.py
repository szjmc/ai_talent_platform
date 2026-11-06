"""同接口多文件上传、分类与独立事务验证，不连接远程数据库。
标签：[RAG组] [P1]
"""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from app.core.exceptions import FileError
from app.services.knowledge_revision_service import KnowledgeRevisionService


def test_swagger_file_is_array_and_category_is_one_required_enum():
    from app.routers.knowledge_router import router
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    schema = app.openapi()
    body = schema['paths']['/api/v1/knowledge/docs/upload']['post']['requestBody']['content']['multipart/form-data']['schema']
    model = schema['components']['schemas'][body['$ref'].split('/')[-1]]
    assert model['properties']['file']['type'] == 'array'
    item = model['properties']['file']['items']
    assert item.get('format') == 'binary' or item.get('contentMediaType') == 'application/octet-stream', item
    assert len(model['properties']['category']['enum']) == 7
    assert {'file', 'category'} <= set(model['required'])


@pytest.mark.asyncio
async def test_batch_same_category_and_one_failure_does_not_stop_next():
    from app.routers.knowledge_router import router, get_revision_service
    service = NS(upload=AsyncMock(), upload_and_publish=AsyncMock(side_effect=[
        FileError('坏文件'), {'status': 'published', 'doc_id': 2, 'revision_id': 3}]))
    user = NS(id=4)
    async def context():
        yield service, user
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_revision_service] = context
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/knowledge/docs/upload', data={'category': 'training', 'doc_id': '9'},
            files=[('file', ('bad.txt', b'bad', 'text/plain')), ('file', ('good.txt', b'good', 'text/plain'))])
        result = response.json()['data']
        assert response.status_code == 200
        assert (result['total'], result['success_count'], result['failed_count']) == (2, 1, 1)
        assert result['items'][0]['error'] == '坏文件'
        assert result['items'][1]['revision_id'] == 3
        assert [c.args[3] for c in service.upload_and_publish.await_args_list] == ['training', 'training']
        assert [c.args[-1] for c in service.upload_and_publish.await_args_list] == [9, 9]
        service.upload.assert_not_awaited()
        for category in (None, 'unknown', 'training,resume'):
            rejected = await client.post('/api/v1/knowledge/docs/upload',
                data={'category': category} if category else {},
                files=[('file', ('a.txt', b'a')), ('file', ('b.txt', b'b'))])
            assert rejected.status_code == 422
        assert service.upload_and_publish.await_count == 2


@pytest.mark.asyncio
async def test_batch_keeps_committed_draft_when_publish_fails_and_processes_next(monkeypatch):
    from app.infrastructure.database import session
    sessions = []
    class DB:
        def __init__(self):
            self.commit, self.rollback = AsyncMock(), AsyncMock()
        async def __aenter__(self):
            sessions.append(self)
            return self
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(session, 'get_session_factory', lambda: DB)
    upload = AsyncMock(side_effect=[{'revision_id': 1, 'version': 1, 'status': 'draft', 'doc_id': 10},
        {'revision_id': 2, 'version': 1, 'status': 'draft', 'doc_id': 11}])
    preview = AsyncMock(return_value={'version': 2, 'chunk_count': 3})
    publish = AsyncMock(side_effect=[FileError('向量服务不可用'), {'status': 'published', 'revision_id': 2}])
    monkeypatch.setattr(KnowledgeRevisionService, 'upload', upload)
    monkeypatch.setattr(KnowledgeRevisionService, 'preview', preview)
    monkeypatch.setattr(KnowledgeRevisionService, 'publish', publish)
    finish = AsyncMock()
    monkeypatch.setattr(KnowledgeRevisionService, 'finish', finish)
    service = KnowledgeRevisionService(NS())
    failed = await service.upload_and_publish(b'a', 'a.txt', 'text/plain', 'training', 'a', NS(id=1))
    success = await service.upload_and_publish(b'b', 'b.txt', 'text/plain', 'training', 'b', NS(id=1))
    assert failed['status'] == 'failed' and failed['revision_status'] == 'draft'
    assert failed['doc_id'] == 10 and failed['revision_id'] == 1
    assert success['status'] == 'published' and success['chunk_count'] == 3
    assert sessions[0].commit.await_count == 1 and sessions[0].rollback.await_count == 1
    assert sessions[1].commit.await_count == 2 and sessions[1].rollback.await_count == 0
    assert [call.args[0] for call in finish.await_args_list] == [True, False, True, True]


@pytest.mark.asyncio
async def test_parse_failure_preserves_original_and_failed_revision(monkeypatch):
    from app.services import knowledge_revision_service as module
    service = KnowledgeRevisionService(NS(flush=AsyncMock()))
    created = []
    async def add(db, row):
        row.id = 10 + len(created)
        created.append(row)
        return row
    service.repo = NS(add=add, published=AsyncMock(return_value=None), next_number=AsyncMock(return_value=1))
    service.artifact = AsyncMock(return_value=NS(id=20, stored_name='source.txt', file_name='source.txt'))
    service.json_file = AsyncMock(return_value=NS(id=21))
    service.detail = AsyncMock(return_value={'status': 'failed', 'revision_id': 11})
    def invalid(*args):
        raise FileError('无法解析原件')
    monkeypatch.setattr(module, 'load', invalid)
    result = await service.upload(b'invalid', 'source.txt', 'text/plain', 'training', '标题', NS(id=1), preserve_failure=True)
    assert created[1].source_file_id == 20 and created[1].status == 'failed'
    assert created[0].status == 'failed'
    assert result['error'] == '无法解析原件'
