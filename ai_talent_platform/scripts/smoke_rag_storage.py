"""隔离远程存储联调；仅删除本次 UUID 创建的对象和集合。
标签：[RAG组] [P1]。"""
import asyncio
from uuid import uuid4
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.ai.rag.embedding_service import EmbeddingService
from app.infrastructure.vector.milvus import get_milvus, close_milvus

async def main():
    run = uuid4().hex
    from app.config.settings import settings
    collection = settings.MILVUS_COLLECTION_KNOWLEDGE
    doc_id = int(run[:12], 16)
    storage = get_storage()
    key = None
    client = None
    created = False
    try:
        content = '联调测试：Python 后端岗位要求掌握 FastAPI、MySQL 和异步编程。'
        key = await storage.save(content.encode('utf-8'), run + '.txt', subdir='knowledge/联调测试')
        assert await storage.read(key) == content.encode('utf-8')
        print('MINIO_WRITE_READ_PASS', flush=True)
        embeddings = EmbeddingService()
        vectors = await embeddings.embed_texts([content])
        client = await get_milvus()
        store = MilvusAdapter(client=client, collection=collection)
        await store.ensure_collection(len(vectors[0]))
        created = True
        await store.upsert([1], vectors, doc_id, metadata=[{"file_id":run,"category":"联调测试","content":content,"source":run+".txt","page_number":1}])
        query = await embeddings.embed_query('Python后端需要掌握什么技术？')
        hits = await store.search(query, [doc_id], 5)
        assert hits and hits[0]['id'] == 1
        print('MILVUS_WRITE_SEARCH_PASS score=',hits[0]['score'],flush=True)
        assert not await store.search(query, [doc_id + 1], 5)
        print('DOCUMENT_FILTER_PASS',flush=True)
        await store.delete_document(doc_id)
        assert not await store.search(query, [doc_id], 5)
        print('MILVUS_DELETE_PASS',flush=True)
    finally:
        if created and client is not None:
            await MilvusAdapter(client=client, collection=collection).delete_document(doc_id)
            print('TEST_VECTORS_REMOVED',flush=True)
        if key is not None:
            await storage.delete(key)
            print('TEST_OBJECT_REMOVED',flush=True)
        await close_milvus()

if __name__ == '__main__':
    asyncio.run(main())
