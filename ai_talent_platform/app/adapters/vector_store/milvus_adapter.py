# 向量主键包含文档与切片 ID。分类决定集合，精确切片白名单隔离未发布和历史版本。
"""兼容既有 knowledge_chunks 固定 Schema；不改表、不删除集合。
标签：[RAG组] [P0]。"""
import asyncio
import json
from app.config.settings import settings
from app.core.exceptions import AIError
from app.core.rag_constants import ALL_CATEGORIES, collection_for_category

def create_client(uri, token):
    from pymilvus import MilvusClient
    return MilvusClient(uri=uri, token=token)

class MilvusAdapter:
    def __init__(self, client=None, collection=None):
        self.client = client
        self.collection = collection or settings.MILVUS_COLLECTION_KNOWLEDGE
        # 显式指定集合用于隔离测试；正常业务按分类路由。
        self.explicit_collection = collection

    async def _client(self):
        if self.client is not None:
            return self.client
        from app.infrastructure.vector.milvus import get_milvus
        return await get_milvus()

    async def ensure_collection(self, dimension):
        # 共享集合由部署初始化；业务请求仅校验，不自动重建或改变索引。
        client = await self._client()
        if not await asyncio.to_thread(client.has_collection, collection_name=self.collection):
            raise AIError("配置的 Milvus 集合不存在，请先初始化固定 Schema")
        description = await asyncio.to_thread(client.describe_collection, collection_name=self.collection)
        fields = {f["name"]: f for f in description.get("fields", [])}
        expected = {"chunk_id": 21, "file_id": 21, "category": 21, "content": 21, "source": 21, "page_number": 5, "embedding": 101}
        if any(name not in fields or int(fields[name]["type"]) != kind for name, kind in expected.items()):
            raise AIError("Milvus 集合字段与 knowledge_chunks 契约不兼容")
        if not fields["chunk_id"].get("is_primary") or description.get("auto_id"):
            raise AIError("Milvus 必须使用非自动生成的 chunk_id 主键")
        if int(fields["embedding"].get("params", {}).get("dim", 0)) != dimension:
            raise AIError("Milvus embedding 维度不一致，禁止写入")

    @staticmethod
    def _prefix(doc_id):
        # 使用命名空间避免覆盖其他组写入的字符串主键；包含结尾分隔符避免 1 匹配 10。
        return "ragdoc-" + str(int(doc_id)) + "-chunk-"

    async def upsert(self, ids, vectors, doc_id, metadata=None):
        if not vectors or len(ids) != len(vectors) or any(len(v) != len(vectors[0]) for v in vectors):
            raise ValueError("向量与主键必须一一对应且维度一致")
        if metadata is None or len(metadata) != len(ids):
            raise ValueError("固定 Schema 要求每个切片提供完整来源元数据")
        categories = {m['category'] for m in metadata}
        if len(categories) != 1:
            raise ValueError('同一文档的切片必须属于同一种分类')
        target = collection_for_category(next(iter(categories)))
        if not self.explicit_collection and target != self.collection:
            return await MilvusAdapter(await self._client(), target).upsert(ids, vectors, doc_id, metadata)
        await self.ensure_collection(len(vectors[0]))
        client = await self._client()
        rows = []
        for pk, vector, meta in zip(ids, vectors, metadata):
            row = {"chunk_id": self._prefix(doc_id) + str(int(pk)), "embedding": vector,
                   "file_id": str(meta["file_id"]), "category": str(meta["category"]),
                   "content": str(meta["content"]), "source": str(meta["source"]),
                   "page_number": int(meta.get("page_number") or 0)}
            # VARCHAR 限制按 UTF-8 字节计；显式拒绝而不是截断来源或正文。
            for field, maximum in {"chunk_id":256,"file_id":128,"category":64,"content":65535,"source":512}.items():
                if len(row[field].encode("utf-8")) > maximum:
                    raise ValueError(field + " 超出 Milvus 字段长度")
            rows.append(row)
        for offset in range(0, len(rows), 500):
            await asyncio.to_thread(client.upsert, collection_name=self.collection, data=rows[offset:offset+500])

    async def search(self, vector, doc_ids, top_k=5, categories=None, active_chunks=None):
        if not doc_ids:
            return []
        # 各分类独立过滤，跨集合结果按同一COSINE分数合并后截取Top K。
        if not self.explicit_collection:
            requested = list(categories) if categories is not None else list(ALL_CATEGORIES)
            grouped = {}
            for category in requested:
                grouped.setdefault(collection_for_category(category), []).append(category)
            client = await self._client()
            results = await asyncio.gather(*[
                MilvusAdapter(client, name).search(vector, doc_ids, top_k, values, active_chunks)
                for name, values in grouped.items()
            ])
            best = {}
            for hits in results:
                for hit in hits:
                    if hit['id'] not in best or hit['score'] > best[hit['id']]['score']:
                        best[hit['id']] = hit
            return sorted(best.values(), key=lambda h: (-h['score'], h['id']))[:top_k]
        await self.ensure_collection(len(vector))
        client = await self._client()
        # 固定 Schema 无 doc_id：以命名空间前缀表达 MySQL 可见文档白名单。
        expr = " or ".join('chunk_id like ' + json.dumps(self._prefix(i) + '%') for i in doc_ids)
        if active_chunks is not None:
            allowed = set(doc_ids)
            keys = [self._prefix(did) + str(int(cid)) for did, cid in active_chunks if did in allowed]
            if not keys:
                return []
            # 精确主键白名单先于向量召回，未提交的新向量不能挤掉仍生效的旧切片。
            if len(keys) > 500:
                hits = []
                for offset in range(0, len(active_chunks), 500):
                    hits.extend(await self.search(vector, doc_ids, top_k, categories, active_chunks[offset:offset+500]))
                return sorted(hits, key=lambda h: (-h['score'], h['id']))[:top_k]
            expr = 'chunk_id in ' + json.dumps(keys)
        if categories is not None:
            expr = '(' + expr + ') and category in ' + json.dumps(list(categories))
        result = await asyncio.to_thread(client.search, collection_name=self.collection, data=[vector], anns_field="embedding", filter=expr, limit=top_k, output_fields=["chunk_id"], consistency_level="Strong", search_params={"metric_type":"COSINE"})
        return [{"id": int(str(hit["chunk_id"]).rsplit("-chunk-", 1)[1]), "score": float(hit["distance"])} for hit in result[0]]

    async def delete_document(self, doc_id):
        # 删除/重试/恢复都清理三个集合中本平台同一doc_id的命名空间，兼容旧版错放数据。
        if not self.explicit_collection:
            client = await self._client()
            names = {collection_for_category(c) for c in ALL_CATEGORIES}
            await asyncio.gather(*[MilvusAdapter(client, name).delete_document(doc_id) for name in names])
            return
        client = await self._client()
        expr = 'chunk_id like ' + json.dumps(self._prefix(doc_id) + '%')
        await asyncio.to_thread(client.delete, collection_name=self.collection, filter=expr)

    async def delete_chunks(self, doc_id, chunk_ids):
        """版本补偿只删除指定旧/暂存切片，禁止整份文档清理误伤新版本。"""
        if not chunk_ids:
            return
        if not self.explicit_collection:
            client = await self._client()
            names = {collection_for_category(c) for c in ALL_CATEGORIES}
            await asyncio.gather(*[MilvusAdapter(client, name).delete_chunks(doc_id, chunk_ids) for name in names])
            return
        client = await self._client()
        for offset in range(0, len(chunk_ids), 500):
            keys = [self._prefix(doc_id) + str(int(cid)) for cid in chunk_ids[offset:offset+500]]
            await asyncio.to_thread(client.delete, collection_name=self.collection, filter='chunk_id in ' + json.dumps(keys))
