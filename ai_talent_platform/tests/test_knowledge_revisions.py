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
    """知识库只留检索/切片语义；文件类动作已迁到 /files 分组。

    拆分依据：/knowledge 下只保留「读语义内容」的接口（检索、文档与版本正文、
    切片预览、题库检索），任何「写文件 / 换版本 / 删索引」的动作都归「文件管理」。
    这样 Swagger 分组与路径前缀一致，联调方不会再在知识库分组里找上传接口。
    """
    from app.core.application import create_application
    from app.routers.file_router import router as file_router
    from app.routers.knowledge_router import router

    paths = create_application().openapi()['paths']

    # 用「路径集合」而非「条数」守护归属：
    # 条数断言在每次新增接口时都要改测试（/files/docs/{doc_id}/edit 落地时就是这样误报的），
    # 真正要防的是「读写动作挂错分组」，所以逐条列出允许的路径。
    assert {r.path for r in router.routes} == {
        '/knowledge/retrieve',
        '/knowledge/docs',
        '/knowledge/docs/{doc_id}/revisions',
        '/knowledge/revisions/{revision_id}',
        '/knowledge/revisions/{revision_id}/preview',
        '/knowledge/questions/search',
    }, sorted(r.path for r in router.routes)
    assert {r.path for r in file_router.routes} == {
        '/files/upload',
        '/files/docs/{doc_id}/retry',
        '/files/docs/{doc_id}/editable',
        '/files/docs/{doc_id}/edit',
        '/files/revisions/{revision_id}/download',
        '/files/revisions/{revision_id}/edit',
        '/files/revisions/{revision_id}/publish',
        '/files/revisions/{revision_id}/restore',
        '/files/revisions/{revision_id}/discard',
        '/files/questions/upload',
        '/files/index/delete',
    }, sorted(r.path for r in file_router.routes)
    assert '/api/v1/knowledge/drafts/upload' not in paths

    # 上传只存在于文件管理分组，且知识库下不再有同名路径
    assert '/api/v1/files/upload' in paths
    assert paths['/api/v1/files/upload']['post']['tags'] == ['文件管理']
    assert '/api/v1/knowledge/docs/upload' not in paths

    for target, tag in ((router, '知识库'), (file_router, '文件管理')):
        for route in target.routes:
            for method in route.methods:
                operation = paths['/api/v1' + route.path][method.lower()]
                assert operation['description'], route.path
                assert operation['tags'] == [tag], (route.path, operation['tags'])
                assert any('\u4e00' <= c <= '\u9fff' for c in operation['summary']), route.path


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/v1/files/upload'])
async def test_original_upload_urls_use_draft_service(path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.routers.file_router import router
    from app.routers.knowledge_router import get_revision_service
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    # 上传接口在返回前显式提交草稿（提交后才可能触发人才自动处理），测试替身需要该入口。
    service = NS(upload=AsyncMock(return_value={'doc_id': 1, 'revision_id': 2, 'status': 'draft'}),
                 commit_upload=AsyncMock())
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


# ============================================================ doc_id 编辑入口
# 需求：用户操作对象是「文档」，编辑接口不应要求调用方查找并传入 revision_id；
# 后端必须以「当前已发布且生效的版本」为基线另存新草稿，不能用最新草稿。
# 全部离线运行（SQLite 内存库），不依赖 MySQL / Milvus。
import json as _json
from typing import ClassVar

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base


@pytest_asyncio.fixture
async def edit_db():
    """SQLite 内存库：只为本组用例建表，不依赖 MySQL 集成夹具。"""
    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(type_, compiler, **kw):
        # SQLite 只有 INTEGER PRIMARY KEY 才自增，BIGINT 会丢 AUTOINCREMENT
        return "INTEGER"

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _EditUser:
    """最小身份：文档创建人（超管，避免数据范围干扰基线判断）。"""

    def __init__(self, uid: int):
        self.id = uid
        self.is_superuser = True
        self.role_codes = ["super_admin"]


async def _seed_edit_fixture(db):
    """造「已生效 v1 + 更新的未发布草稿 v2」，并返回 (user, doc_id, published_id, draft_id)。"""
    from app.adapters.storage.base_storage import get_storage
    from app.models.file import File
    from app.models.knowledge import KnowledgeDoc
    from app.models.knowledge_revision import KnowledgeRevision

    user = _EditUser(1)
    doc = KnowledgeDoc(title="文档", category="resume", source_type="file",
                       status="indexed", created_by=user.id)
    db.add(doc)
    await db.flush()

    async def make(data: bytes, name: str) -> File:
        key = await get_storage("local").save(data, name, subdir="knowledge_revision/resume")
        row = File(file_name=name, stored_name=name, file_path=key, file_size=len(data),
                   content_type="text/plain", extension=".txt", file_hash="f" * 64,
                   storage_type="local", biz_type="knowledge_revision", uploader_id=user.id)
        db.add(row)
        await db.flush()
        return row

    def body(text: str) -> bytes:
        return _json.dumps({"text": text, "documents": []}).encode()

    published_body = await make(body("生效正文"), "p.txt")
    published_file = await make(b"p", "p.docx")
    published = KnowledgeRevision(doc_id=doc.id, revision_no=1, file_id=published_file.id,
                                  body_file_id=published_body.id, title="生效版本",
                                  category="resume", content_hash="h1", status="published",
                                  created_by=user.id)
    db.add(published)
    await db.flush()

    # 更新的草稿：revision_no 更大、id 更大，用来证明「不会拿它当基线」
    draft_body = await make(body("草稿正文"), "d.txt")
    draft_file = await make(b"d", "d.docx")
    draft = KnowledgeRevision(doc_id=doc.id, revision_no=2, parent_revision_id=published.id,
                              file_id=draft_file.id, body_file_id=draft_body.id,
                              title="未发布草稿", category="resume", content_hash="h2",
                              status="draft", created_by=user.id)
    db.add(draft)
    await db.flush()
    return user, doc.id, published.id, draft.id


@pytest.fixture(autouse=True)
def _stub_durable_job(monkeypatch):
    """编辑会写一份对象补偿日志（绑定全局 MySQL 引擎），本组用例离线运行，桩掉即可。"""
    from app.services.knowledge_revision_service import KnowledgeRevisionService

    class _Job:
        values: ClassVar[dict] = {}

        async def record(self, **values):
            self.values.update(values)

        async def close(self):
            return None

    async def _start(self, identity, **values):
        job = _Job()
        job.values.update(values)
        self.jobs.append(job)
        return job

    monkeypatch.setattr(KnowledgeRevisionService, "start_job", _start)


async def test_document_editable_reads_published_not_latest_draft(edit_db):
    """按 doc_id 读取编辑基线：必须返回生效版本正文，而不是更新的未发布草稿。"""
    from app.services.knowledge_revision_service import KnowledgeRevisionService

    user, doc_id, published_id, draft_id = await _seed_edit_fixture(edit_db)
    data = await KnowledgeRevisionService(edit_db).document_editable(doc_id, user)

    assert data["body"] == "生效正文"
    assert data["revision_id"] == published_id != draft_id
    assert data["base_version"] >= 1


async def test_edit_document_uses_published_version_as_parent(edit_db):
    """只传 doc_id 编辑：新草稿来自当前生效版本，生效版本本身保持发布状态。"""
    from app.core.exceptions import NotFoundError
    from app.models.knowledge import KnowledgeDoc
    from app.models.knowledge_revision import KnowledgeRevision
    from app.schemas.knowledge_revision import DocumentEdit
    from app.services.knowledge_revision_service import KnowledgeRevisionService

    user, doc_id, published_id, _draft_id = await _seed_edit_fixture(edit_db)
    result = await KnowledgeRevisionService(edit_db).edit_document(
        doc_id, DocumentEdit(body="修改后的正文"), user)

    assert result["status"] == "draft"
    assert result["parent_revision_id"] == published_id
    assert result["doc_id"] == doc_id

    # 生效版本没有被改动，新草稿需要重新预览 + 发布
    rows = (await edit_db.execute(
        select(KnowledgeRevision).where(KnowledgeRevision.doc_id == doc_id))).scalars().all()
    assert [r.id for r in rows if r.status == "published"] == [published_id]

    # 文档未发布过任何版本时，按 doc_id 编辑必须明确拒绝（不能退化成基于草稿）

    empty = KnowledgeDoc(title="空文档", category="resume", source_type="file",
                         status="draft", created_by=user.id)
    edit_db.add(empty)
    await edit_db.flush()
    with pytest.raises(NotFoundError):
        await KnowledgeRevisionService(edit_db).document_editable(empty.id, user)
    with pytest.raises(NotFoundError):
        await KnowledgeRevisionService(edit_db).edit_document(
            empty.id, DocumentEdit(body="无基线"), user)


def test_edit_route_is_document_scoped_and_legacy_is_deprecated():
    """路由契约：编辑入口按 doc_id，旧 revision_id 入口标注废弃（仍保留兼容）。"""
    from app.core.application import create_application

    paths = create_application().openapi()["paths"]
    edit_doc = paths["/api/v1/files/docs/{doc_id}/edit"]["post"]
    edit_rev = paths["/api/v1/files/revisions/{revision_id}/edit"]["post"]

    # 编辑接口只接收 doc_id 与正文/标题/分类，不暴露 revision_id 或 expected_version
    schema = edit_doc["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/DocumentEdit")
    props = create_application().openapi()["components"]["schemas"]["DocumentEdit"]["properties"]
    assert set(props) == {"title", "category", "body"}
    assert "expected_version" not in props
    # 旧入口保留兼容但明确废弃，避免新前端继续按 revision_id 调用
    assert edit_rev.get("deprecated") is True
    assert "deprecated" not in edit_doc or edit_doc["deprecated"] is False
