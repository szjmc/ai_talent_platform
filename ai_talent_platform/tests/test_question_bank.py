"""题库参数、权限、配额、召回回表及索引同步离线验证。
标签：[RAG组] [P1]。
"""
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import mysql

from app.adapters.vector_store.question_bank_adapter import QuestionBankVectorAdapter
from app.ai.rag.question_embeddings import question_embedding_signature
from app.config.settings import settings
from app.core.exceptions import AIError, PermissionDeniedError
from app.repositories.question_bank_repository import QuestionBankRepository
from app.schemas.question_bank import QuestionImport, QuestionSearchRequest
from app.services.question_bank_index_service import QuestionBankIndexService, question_index_text
from app.services.question_bank_service import QuestionBankService


def user(roles=None):
    return NS(id=7, organization_id=3, is_superuser=False, role_codes=roles or ['employee'])


def request(**overrides):
    return QuestionSearchRequest.model_validate({
        'technology': 'python', 'difficulty': 'intermediate', 'counts': {'single': 2}, **overrides,
    })


def question(pk=1, **overrides):
    return NS(**{
        'id': pk, 'bank_id': 1, 'external_id': str(pk), 'content_version': 1,
        'technology': 'python', 'difficulty': 'intermediate', 'question_type': 'single',
        'knowledge_points': ['生成器'], 'stem': '生成器的特性是什么？',
        'options': [{'key': 'A', 'text': '按需产出'}, {'key': 'B', 'text': '一次性产出'}],
        'answer': 'A', 'analysis': '参考解析', 'status': 'published', 'is_deleted': False,
        'index_status': 'indexed', 'indexed_version': 1, 'index_signature': question_embedding_signature(),
        **overrides,
    })


@pytest.mark.parametrize('patch', [
    {'counts': {}}, {'counts': {'single': 101}}, {'counts': {'single': -1}},
    {'counts': {'single': True}}, {'counts': {'single': 60, 'text': 60}},
    {'counts': {'unknown': 1}}, {'difficulty': 'unknown'}, {'bank_ids': [0]},
    {'exclude_question_ids': [-1]}, {'technology': 'python" or true'}, {'extra': 1},
])
def test_invalid_selection_is_rejected(patch):
    with pytest.raises(ValidationError):
        request(**patch)


def test_selection_normalizes_technology_and_blank_query():
    value = request(technology=' Python ', query='   ')
    assert value.technology == 'python' and value.query is None


@pytest.mark.parametrize(('kind', 'answer', 'options'), [
    ('single', 'A', [{'key': 'A', 'text': '甲'}, {'key': 'B', 'text': '乙'}]),
    ('multiple', ['A', 'B'], [{'key': 'A', 'text': '甲'}, {'key': 'B', 'text': '乙'}]),
    ('true_false', True, None), ('fill_blank', [['yield']], None),
    ('text', '参考答案', None), ('coding', 'def solve(): pass', None),
])
def test_all_question_types_import(kind, answer, options):
    value = QuestionImport(bank_id=1, external_id='q1', technology='python', difficulty='beginner',
                           question_type=kind, stem='测试题', answer=answer, options=options)
    assert value.answer == answer


@pytest.mark.parametrize(('kind', 'answer'), [('true_false', 'true'), ('fill_blank', ['yield']), ('single', 'C'), ('multiple', ['A', 'A'])])
def test_invalid_answers_are_rejected(kind, answer):
    with pytest.raises(ValidationError):
        QuestionImport(bank_id=1, external_id='q1', technology='python', difficulty='beginner',
                       question_type=kind, stem='题目', answer=answer,
                       options=[{'key': 'A', 'text': '甲'}, {'key': 'B', 'text': '乙'}] if kind in {'single', 'multiple'} else None)


@pytest.mark.asyncio
async def test_random_selection_reports_shortage_and_hides_answers():
    repo = NS(random_questions=AsyncMock(return_value=[question()]))
    model = NS(aembed_query=AsyncMock())
    result = await QuestionBankService(None, repo=repo, embeddings=model).search(request(), user())
    assert result.returned_count == 1 and result.shortage == {'single': 1}
    assert result.items[0].answer is None and result.items[0].analysis is None
    model.aembed_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_access_requires_assessment_manage():
    repo = NS(random_questions=AsyncMock(return_value=[question()]))
    service = QuestionBankService(None, repo=repo)
    with pytest.raises(PermissionDeniedError):
        await service.search(request(include_answers=True), user())
    repo.random_questions.assert_not_awaited()
    result = await service.search(request(include_answers=True), user(['hr']))
    assert result.items[0].answer == 'A'


@pytest.mark.asyncio
async def test_service_cannot_bypass_knowledge_read():
    with pytest.raises(PermissionDeniedError):
        await QuestionBankService(None).search(request(), user(['unknown']))


@pytest.mark.asyncio
async def test_semantic_merges_batches_filters_stale_ids_and_preserves_rank():
    class Repo:
        async def candidate_batches(self, *args):
            yield [(1, 1), (2, 1)]
            yield [(3, 1)]

        async def selected_questions(self, *args):
            assert args[-1] == [3, 1]
            return [question(1), question(3)]

    store = NS(search=AsyncMock(side_effect=[
        [{'question_id': 1, 'content_version': 1, 'score': .7},
         {'question_id': 2, 'content_version': 0, 'score': .99},
         {'question_id': 999, 'content_version': 1, 'score': 1.0}],
        [{'question_id': 3, 'content_version': 1, 'score': .9}],
    ]))
    embeddings = NS(aembed_query=AsyncMock(return_value=[.1, .2]))
    result = await QuestionBankService(None, repo=Repo(), embeddings=embeddings, store=store).search(
        request(query='生成器'), user(),
    )
    assert [item.question_id for item in result.items] == [3, 1]
    assert result.shortage == {}
    embeddings.aembed_query.assert_awaited_once_with('生成器')


@pytest.mark.asyncio
async def test_empty_candidates_do_not_call_external_services():
    class Repo:
        async def candidate_batches(self, *args):
            for batch in []:
                yield batch

        async def selected_questions(self, *args):
            return []
    embeddings = NS(aembed_query=AsyncMock())
    store = NS(search=AsyncMock())
    result = await QuestionBankService(None, repo=Repo(), embeddings=embeddings, store=store).search(
        request(query='协程'), user(),
    )
    assert result.shortage == {'single': 2}
    embeddings.aembed_query.assert_not_awaited()
    store.search.assert_not_awaited()


def test_repository_applies_scope_labels_banks_and_exclusions():
    stmt = QuestionBankRepository().selection_query(
        request(bank_ids=[20], exclude_question_ids=[100]), user(), 'single',
    )
    sql = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))
    assert "technology = 'python'" in sql and "difficulty = 'intermediate'" in sql
    assert "question_type = 'single'" in sql and "status = 'published'" in sql
    assert 'bank_id IN (20)' in sql and 'id NOT IN (100)' in sql
    assert "permission_scope = 'public'" in sql and 'organization_id = 3' in sql
    assert 'created_by = 7' in sql and 'is_deleted IS false' in sql


def description():
    return {'auto_id': False, 'fields': [
        {'name': name, 'type': kind, 'is_primary': name == 'question_id',
         'params': {'dim': settings.EMBEDDING_DIM} if name == 'embedding' else {}}
        for name, kind in QuestionBankVectorAdapter.FIELD_TYPES.items()
    ]}


@pytest.mark.asyncio
async def test_vector_search_uses_authorized_ids_and_exact_metadata(monkeypatch):
    monkeypatch.setattr(settings, 'EMBEDDING_DIM', 2)
    client = NS(has_collection=Mock(return_value=True), describe_collection=Mock(return_value=description()),
                search=Mock(return_value=[[{'entity': {'question_id': 7, 'content_version': 2}, 'distance': .8}]]))
    adapter = QuestionBankVectorAdapter(client, 'isolated_questions')
    result = await adapter.search([.1, .2], request(), 'single', [(7, 2)], 'sig', 2)
    kwargs = client.search.call_args.kwargs
    assert 'question_id in [7]' in kwargs['filter'] and 'content_version == 2' in kwargs['filter']
    assert 'technology == "python"' in kwargs['filter'] and 'difficulty == "intermediate"' in kwargs['filter']
    assert 'question_type == "single"' in kwargs['filter'] and 'index_signature == "sig"' in kwargs['filter']
    assert result[0]['question_id'] == 7


@pytest.mark.asyncio
async def test_incompatible_collection_is_never_recreated():
    client = NS(has_collection=Mock(return_value=True), describe_collection=Mock(return_value={'fields': []}))
    with pytest.raises(AIError, match='Schema'):
        await QuestionBankVectorAdapter(client).ensure_collection()


@pytest.mark.parametrize('vector', [[0, 0], [float('nan'), 1], [1]])
def test_invalid_vectors_are_rejected(monkeypatch, vector):
    monkeypatch.setattr(settings, 'EMBEDDING_DIM', 2)
    with pytest.raises(AIError):
        QuestionBankVectorAdapter.validate_vector(vector)


def test_embedding_text_contains_labels_and_options_not_answer():
    text = question_index_text(question(answer='secret_answer', analysis='secret_analysis'))
    assert '等级：中级' in text and '题型：单选题' in text and 'A. 按需产出' in text
    assert 'secret_answer' not in text and 'secret_analysis' not in text


@pytest.mark.asyncio
async def test_import_is_idempotent_and_updates_version_on_change():
    item = QuestionImport(bank_id=1, external_id='q1', technology='python', difficulty='beginner',
                          question_type='true_false', stem='判断陈述', answer=True)
    values = item.model_dump(mode='json')
    row = NS(**values, id=1, is_deleted=False, content_version=1, version=1, index_status='indexed')
    row.restore = Mock()
    db = NS(scalar=AsyncMock(return_value=row), flush=AsyncMock(), add=Mock())
    repo = QuestionBankRepository()
    await repo.upsert_import(db, item, 7)
    assert row.content_version == 1
    await repo.upsert_import(db, item.model_copy(update={'stem': '新陈述'}), 7)
    assert row.content_version == 2 and row.index_status == 'pending'
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_index_failure_is_recorded_and_later_questions_continue():
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def commit(self):
            pass
        async def rollback(self):
            pass
    repo = NS(
        index_batch=AsyncMock(side_effect=[[question(1), question(2), question(3, status='disabled')], []]),
        mark_indexed=AsyncMock(), mark_index_error=AsyncMock(),
    )
    model = NS(aembed_documents=AsyncMock(side_effect=[OSError('offline'), [[.1, .2]]]))
    store = NS(upsert=AsyncMock(), delete_questions=AsyncMock())
    result = await QuestionBankIndexService(repo, model, store)._sync_batches(Session, 32, False)
    assert result == {'indexed': 2, 'failed': 1}
    assert repo.mark_index_error.call_args.args[1:3] == (1, 1)
    store.delete_questions.assert_awaited_once_with([3])
    assert store.upsert.call_count == 1


@pytest.mark.asyncio
async def test_http_contract_and_answer_redaction(monkeypatch):
    import httpx
    from app.core.application import create_application
    from app.core.dependencies import get_current_user, get_db
    from app.routers import knowledge_router

    app = create_application()
    repo = NS(random_questions=AsyncMock(return_value=[question()]))
    service = QuestionBankService(None, repo=repo)
    monkeypatch.setattr(knowledge_router, 'QuestionBankService', lambda db: service)

    async def fake_db():
        yield None
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: user()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = settings.API_PREFIX + '/knowledge/questions/search'
        response = await client.post(url, json=request().model_dump())
        assert response.status_code == 200
        data = response.json()['data']
        assert data['returned_count'] == 1 and data['shortage'] == {'single': 1}
        assert 'answer' not in data['items'][0] and 'analysis' not in data['items'][0]
        forbidden = await client.post(url, json=request(include_answers=True).model_dump())
        assert forbidden.status_code == 403
        invalid = await client.post(url, json={'technology': 'python'})
        assert invalid.status_code == 422
        app.dependency_overrides[get_current_user] = lambda: user(['unknown'])
        denied = await client.post(url, json=request().model_dump())
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_initialization_builds_only_independent_question_schema(monkeypatch):
    from pymilvus import MilvusClient
    monkeypatch.setattr(settings, 'EMBEDDING_DIM', 2)
    client = NS(
        has_collection=Mock(side_effect=[False, True]),
        create_schema=MilvusClient.create_schema,
        prepare_index_params=MilvusClient.prepare_index_params,
        create_collection=Mock(), describe_collection=Mock(return_value=description()),
        load_collection=Mock(),
    )
    await QuestionBankVectorAdapter(client, 'new_question_vectors').initialize_collection()
    kwargs = client.create_collection.call_args.kwargs
    assert kwargs['collection_name'] == 'new_question_vectors'
    fields = {field.name: field for field in kwargs['schema'].fields}
    assert fields['question_id'].is_primary
    assert fields['embedding'].params['dim'] == 2
    assert set(fields) == set(QuestionBankVectorAdapter.FIELD_TYPES)
    client.load_collection.assert_called_once_with(collection_name='new_question_vectors')


@pytest.mark.asyncio
async def test_existing_collection_initialization_does_not_rebuild():
    client = NS(has_collection=Mock(return_value=True), describe_collection=Mock(return_value=description()),
                create_collection=Mock(), load_collection=Mock())
    await QuestionBankVectorAdapter(client).initialize_collection()
    client.create_collection.assert_not_called()


def test_native_langchain_embedding_uses_existing_provider_config(monkeypatch):
    from app.ai.rag import question_embeddings as module
    import langchain_openai
    monkeypatch.setattr(module, 'select_provider', lambda _: 'openai')
    monkeypatch.setattr(module, 'select_embedding_model', lambda _: 'test-bge')
    monkeypatch.setattr(module, 'resolve_base_url', lambda _: 'https://example.invalid/v1')
    monkeypatch.setattr(module, 'resolve_api_key', lambda _: 'synthetic-test-key')
    constructor = Mock()
    monkeypatch.setattr(langchain_openai, 'OpenAIEmbeddings', constructor)
    assert module.get_question_embeddings() is constructor.return_value
    kwargs = constructor.call_args.kwargs
    assert kwargs['model'] == 'test-bge'
    assert kwargs['base_url'] == 'https://example.invalid/v1'
    assert kwargs['check_embedding_ctx_length'] is False
