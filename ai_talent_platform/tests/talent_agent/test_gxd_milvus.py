"""
gxd-可选真实Milvus适配测试，随机临时集合，结束时删除自己创建的集合。

标签：[RAG组] [P2]（Milvus 联调用例，需外部服务）
"""
import os
from uuid import uuid4

import pytest

from app.adapters.vector_store.talent_vector_store import TalentVectorStore
from app.config.settings import settings


@pytest.mark.skipif(os.getenv("GXD_MILVUS_TEST") != "1", reason="设置GXD_MILVUS_TEST=1后验证真实Milvus")
async def test_real_milvus_collection_filter_and_delete(monkeypatch):
    """gxd-真实验证JSON过滤、幂等更新与指定删除，不使用项目业务集合。"""
    monkeypatch.setattr(settings, "MILVUS_ENABLED", True)
    name = "gxd_verify_" + uuid4().hex
    adapter = TalentVectorStore()
    created = False
    try:
        await adapter.create_collection(name, 2)
        created = True
        await adapter.create_collection(name, 2)          # 幂等
        await adapter.upsert(name, [1, 2], [[1.0, 0.0], [0.0, 1.0]],
                             [{"talent_id": 1, "version": 1}, {"talent_id": 2, "version": 1}])
        rows = await adapter.search(name, [1.0, 0.0], top_k=5, expr='talent_id == 1')
        assert len(rows) == 1 and rows[0]["id"] == 1
        await adapter.upsert(name, [1], [[1.0, 0.0]], [{"talent_id": 1, "version": 2}])
        rows = await adapter.search(name, [1.0, 0.0], top_k=5, expr='version == 2')
        assert len(rows) == 1
        await adapter.delete(name, ids=[1])
        assert not await adapter.search(name, [1.0, 0.0], top_k=5, expr='talent_id == 1')
    finally:
        if created:
            await adapter.drop(name)


# ---------------------------------------------------------------- 离线契约
class _FakeMilvus:
    """当前 TalentVectorStore 使用的 MilvusClient 快速建集合/动态字段接口替身。"""

    def __init__(self):
        self.collections, self.rows = {}, {}

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, **kwargs):
        self.collections[collection_name] = kwargs
        self.rows[collection_name] = {}

    def upsert(self, collection_name, data):
        for row in data:
            self.rows[collection_name][row["id"]] = row

    def search(self, collection_name, data, limit, filter, output_fields):
        import re
        conditions = re.findall(r"(\w+)\s*==\s*([\w.-]+)", filter or "")
        hits = []
        for pk, row in self.rows[collection_name].items():
            if all(str(row.get(key)) == value.strip("'\"") for key, value in conditions):
                entity = {key: row[key] for key in output_fields if key in row}
                hits.append({"id": pk, "distance": 0.5, "entity": entity})
        return [hits[:limit]]

    def delete(self, collection_name, id=None, filter=None):
        for pk in id or []:
            self.rows[collection_name].pop(pk, None)

    def drop_collection(self, collection_name):
        self.collections.pop(collection_name, None)
        self.rows.pop(collection_name, None)


async def test_talent_vector_store_offline_contract(monkeypatch):
    """离线验证当前整数主键、动态字段过滤、覆盖写和指定删除契约。"""
    import app.adapters.vector_store.talent_vector_store as module

    client = _FakeMilvus()
    monkeypatch.setattr(module, "_get_client", lambda: client)
    store = module.TalentVectorStore()
    await store.create_collection("talent_vectors", 2)
    await store.create_collection("talent_vectors", 2)
    assert "talent_vectors" in client.collections

    await store.upsert("talent_vectors", [1, 2], [[1.0, 0.0], [0.0, 1.0]],
                       [{"talent_id": 1, "version": 1}, {"talent_id": 2, "version": 1}])
    hits = await store.search("talent_vectors", [1.0, 0.0], top_k=5,
                              expr="talent_id == 1", output_fields=["version"])
    assert [hit["id"] for hit in hits] == [1] and hits[0]["version"] == 1

    await store.upsert("talent_vectors", [1], [[0.9, 0.1]], [{"talent_id": 1, "version": 2}])
    hits = await store.search("talent_vectors", [1.0, 0.0], top_k=5, expr="version == 2")
    assert [hit["id"] for hit in hits] == [1]

    await store.delete("talent_vectors", ids=[1])
    assert not await store.search("talent_vectors", [1.0, 0.0], top_k=5, expr="talent_id == 1")
    with pytest.raises(ValueError):
        await store.upsert("talent_vectors", [3], [[1.0, 0.0], [0.0, 1.0]])
    await store.drop("talent_vectors")
    assert "talent_vectors" not in client.collections
