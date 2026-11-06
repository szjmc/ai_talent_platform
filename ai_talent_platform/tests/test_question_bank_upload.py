"""题库上传文件校验、事务、范围权限及Swagger契约。
标签：[RAG组] [P1]。
"""
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.core.exceptions import ParamError, PermissionDeniedError
from app.schemas.question_bank import QuestionImportBatch, QuestionImportResponse
from app.services.question_bank_import_service import QuestionBankImportService


def payload(**changes):
    return json.dumps([{
        'bank_id': 1, 'external_id': 'q1', 'technology': 'python', 'difficulty': 'intermediate',
        'question_type': 'true_false', 'stem': '生成器是迭代器。', 'answer': True, **changes,
    }], ensure_ascii=False).encode('utf-8')


def admin():
    return NS(id=7, is_superuser=False, role_codes=['admin'], organization_id=3)


class Session:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def service_fixture(indexed=1, failed=0, pending=0):
    db = Session()
    repo = NS(import_target=AsyncMock(return_value=None), import_index_counts=AsyncMock(return_value={
        'indexed_count': indexed, 'failed_count': failed, 'pending_count': pending,
    }))
    index = NS(import_questions=AsyncMock(return_value=[11]), sync=AsyncMock())
    service = QuestionBankImportService(repo=repo, index_service=index, session_factory=lambda: db, engine=object())
    return service, repo, index, db


@pytest.mark.parametrize(('data', 'name'), [
    (b'', 'bank.json'), (b'not json', 'bank.json'), (b'[]', 'bank.json'),
    (b'{}', 'bank.json'), (b'\xff', 'bank.json'), (payload(), 'bank.pdf'),
    (payload(question_type='unknown'), 'bank.json'), (payload(answer='true'), 'bank.json'),
])
@pytest.mark.asyncio
async def test_invalid_file_is_rejected_before_database_write(data, name):
    service, repo, index, db = service_fixture()
    with pytest.raises(ParamError):
        await service.upload(data, name, admin())
    repo.import_target.assert_not_awaited()
    index.import_questions.assert_not_awaited()
    index.sync.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_file_is_rejected(monkeypatch):
    service, repo, index, db = service_fixture()
    monkeypatch.setattr(settings, 'MAX_UPLOAD_SIZE_MB', 0)
    with pytest.raises(ParamError, match='大小'):
        await service.upload(payload(), 'bank.json', admin())
    index.import_questions.assert_not_awaited()


def test_duplicate_source_ids_rejected_by_shared_validator():
    item = json.loads(payload())[0]
    with pytest.raises(ValidationError, match='组合不能重复'):
        QuestionImportBatch.model_validate([item, item])


@pytest.mark.asyncio
async def test_upload_commits_before_sync_and_only_indexes_imported_ids():
    service, repo, index, db = service_fixture()

    async def synchronize(*args, **kwargs):
        db.commit.assert_awaited_once()
        assert kwargs['question_ids'] == [11]

    index.sync.side_effect = synchronize
    result = await service.upload(b'\xef\xbb\xbf' + payload(), 'bank.JSON', admin())
    assert result.imported_count == 1 and result.indexed_count == 1
    assert result.question_ids == [11] and result.sync_requested is True
    assert result.sync_error is None
    index.import_questions.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_rolls_back_and_does_not_index():
    service, repo, index, db = service_fixture()
    index.import_questions.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError):
        await service.upload(payload(), 'bank.json', admin())
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    index.sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_failure_preserves_committed_import_and_reports_pending():
    service, repo, index, db = service_fixture(indexed=0, pending=1)
    index.sync.side_effect = OSError('offline')
    result = await service.upload(payload(), 'bank.json', admin())
    assert result.imported_count == 1 and result.pending_count == 1
    assert result.sync_error is not None
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_can_skip_vector_sync():
    service, repo, index, db = service_fixture(indexed=0, pending=1)
    result = await service.upload(payload(), 'bank.json', admin(), sync_index=False)
    assert result.sync_requested is False and result.pending_count == 1
    index.sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonmanager_cannot_import():
    service, repo, index, db = service_fixture()
    actor = NS(id=9, is_superuser=False, role_codes=['employee'], organization_id=3)
    with pytest.raises(PermissionDeniedError):
        await service.upload(payload(), 'bank.json', actor)
    index.import_questions.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_manager_cannot_overwrite_other_owner(monkeypatch):
    from app.core.permissions import ROLE_PERMISSIONS
    monkeypatch.setitem(ROLE_PERMISSIONS, 'question_editor', {'knowledge:manage'})
    actor = NS(id=9, is_superuser=False, role_codes=['question_editor'], organization_id=3)
    service, repo, index, db = service_fixture()
    repo.import_target.return_value = NS(created_by=10)
    with pytest.raises(PermissionDeniedError, match='他人'):
        await service.upload(payload(), 'bank.json', actor)
    db.rollback.assert_awaited_once()
    index.import_questions.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_manager_cannot_import_to_another_department(monkeypatch):
    from app.core.permissions import ROLE_PERMISSIONS
    monkeypatch.setitem(ROLE_PERMISSIONS, 'question_editor', {'knowledge:manage'})
    actor = NS(id=9, is_superuser=False, role_codes=['question_editor'], organization_id=3)
    service, repo, index, db = service_fixture()
    with pytest.raises(PermissionDeniedError, match='其他部门'):
        await service.upload(payload(permission_scope='dept', organization_id=4), 'bank.json', actor)
    index.import_questions.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_http_multipart_contract_and_chinese_name(monkeypatch):
    from app.core.application import create_application
    from app.core.dependencies import get_current_user
    from app.routers import knowledge_router

    app = create_application()
    importer = NS(upload=AsyncMock(return_value=QuestionImportResponse(
        imported_count=1, question_ids=[11], sync_requested=False,
        indexed_count=0, failed_count=0, pending_count=1,
    )))
    monkeypatch.setattr(knowledge_router, 'QuestionBankImportService', lambda: importer)
    app.dependency_overrides[get_current_user] = admin
    path = settings.API_PREFIX + '/knowledge/questions/upload'
    document = app.openapi()
    operation = document['paths'][path]['post']
    assert operation['summary'] == '上传并导入笔试题库'
    schema_ref = operation['requestBody']['content']['multipart/form-data']['schema']['$ref']
    properties = document['components']['schemas'][schema_ref.rsplit('/', 1)[1]]['properties']
    assert properties['file']['format'] == 'binary'
    assert properties['sync_index']['default'] is True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(path, files={'file': ('bank.json', payload(), 'application/json')}, data={'sync_index': 'false'})
        assert response.status_code == 200
        assert response.json()['data']['question_ids'] == [11]
        args = importer.upload.call_args.args
        assert args[0] == payload() and args[1] == 'bank.json' and args[3] is False
        missing = await client.post(path)
        assert missing.status_code == 422
        app.dependency_overrides[get_current_user] = lambda: NS(is_superuser=False, role_codes=['employee'])
        denied = await client.post(path, files={'file': ('bank.json', payload(), 'application/json')})
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_index_batch_query_is_scoped_to_uploaded_ids():
    from sqlalchemy.dialects import mysql
    from app.repositories.question_bank_repository import QuestionBankRepository
    db = NS(scalars=AsyncMock(return_value=NS(all=lambda: [])))
    await QuestionBankRepository().index_batch(db, 0, 32, 'signature', question_ids=[11, 12])
    stmt = db.scalars.call_args.args[0]
    sql = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))
    assert 'id IN (11, 12)' in sql
