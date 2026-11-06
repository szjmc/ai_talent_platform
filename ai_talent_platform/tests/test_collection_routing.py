"""三集合七分类的存取契约验证，不连接远程数据库。
标签：[RAG组] [P1]
"""
import pytest
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.core.rag_constants import ALL_CATEGORIES, collection_for_category
from app.config.settings import settings


class Client:
    def __init__(self):
        self.writes = []
        self.queries = []
        self.deletes = []

    def has_collection(self, **kwargs):
        return True

    def describe_collection(self, **kwargs):
        kinds = {'chunk_id': 21, 'file_id': 21, 'category': 21, 'content': 21,
                 'source': 21, 'page_number': 5, 'embedding': 101}
        return {'auto_id': False, 'fields': [
            {'name': name, 'type': kind, 'is_primary': name == 'chunk_id',
             'params': {'dim': 2} if name == 'embedding' else {}}
            for name, kind in kinds.items()]}

    def upsert(self, **kwargs):
        self.writes.append(kwargs)

    def search(self, **kwargs):
        self.queries.append(kwargs)
        return [[{'chunk_id': 'ragdoc-9-chunk-11', 'distance': 0.8}]]

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


@pytest.mark.asyncio
async def test_all_seven_categories_write_to_mapped_collection():
    client = Client()
    for category in ALL_CATEGORIES:
        await MilvusAdapter(client).upsert([11], [[1, 0]], 9, [{
            'file_id': 2, 'category': category, 'content': 'test', 'source': 'test.txt'}])
        assert client.writes[-1]['collection_name'] == collection_for_category(category)
        assert client.writes[-1]['data'][0]['category'] == category


@pytest.mark.asyncio
async def test_category_query_and_cross_collection_merge():
    client = Client()
    adapter = MilvusAdapter(client)
    await adapter.search([1, 0], [9], categories=['resume'])
    assert len(client.queries) == 1
    assert client.queries[0]['collection_name'] == settings.MILVUS_COLLECTION_TALENT
    assert 'category in ["resume"]' in client.queries[0]['filter']
    client.queries.clear()
    hits = await adapter.search([1, 0], [9])
    assert len(client.queries) == 3
    assert len(hits) == 1  # 同一MySQL切片不会因历史重复向量重复返回。
    assert all('ragdoc-9-chunk-' in q['filter'] for q in client.queries)


@pytest.mark.asyncio
async def test_delete_cleans_all_three_namespaces():
    client = Client()
    await MilvusAdapter(client).delete_document(9)
    assert len(client.deletes) == 3
    assert all('ragdoc-9-chunk-' in item['filter'] for item in client.deletes)


def test_unknown_category_rejected():
    from app.core.exceptions import ParamError
    with pytest.raises(ParamError):
        collection_for_category('unknown')


def test_job_description_belongs_to_knowledge_collection():
    assert collection_for_category('job_description') == settings.MILVUS_COLLECTION_KNOWLEDGE
    assert collection_for_category('resume') == settings.MILVUS_COLLECTION_TALENT
