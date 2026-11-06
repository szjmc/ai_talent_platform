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
        await adapter.upsert(name, ["a", "b"], [[1.0, 0.0], [0.0, 1.0]],
                             [{"talent_id": 1, "version": 1}, {"talent_id": 2, "version": 1}])
        rows = await adapter.search(name, [1.0, 0.0], 5, expr='metadata["talent_id"] == 1')
        assert len(rows) == 1 and rows[0]["id"] == "a"
        await adapter.upsert(name, ["a"], [[1.0, 0.0]], [{"talent_id": 1, "version": 2}])
        rows = await adapter.search(name, [1.0, 0.0], 5, expr='metadata["version"] == 2')
        assert len(rows) == 1
        await adapter.delete(name, ids=["a"])
        assert not await adapter.search(name, [1.0, 0.0], 5, expr='metadata["talent_id"] == 1')
    finally:
        if created:
            await adapter.drop(name)


# ---------------------------------------------------------------- 离线契约
class _Schema:
    def __init__(self):
        self.fields = []

    def add_field(self, name, dtype, **kwargs):
        self.fields.append({"name": name, "type": int(dtype), **kwargs})


class _Indexes:
    def add_index(self, **kwargs):
        return None


class _FakeMilvus:
    """最小 MilvusClient 替身：覆盖人才适配器用到的 6 个调用。"""

    def __init__(self):
        self.collections, self.rows = {}, {}

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_schema(self, **kwargs):
        return _Schema()

    def prepare_index_params(self):
        return _Indexes()

    def create_collection(self, collection_name, schema, index_params):
        self.collections[collection_name] = schema
        self.rows[collection_name] = {}

    def describe_collection(self, collection_name):
        schema = self.collections[collection_name]
        return {"auto_id": False, "fields": [
            {"name": f["name"], "is_primary": f.get("is_primary", False),
             "type": f["type"], "params": {"dim": f.get("dim", 0)}}
            for f in schema.fields]}

    def upsert(self, collection_name, data):
        for row in data:
            self.rows[collection_name][row["chunk_id"]] = row

    def search(self, collection_name, data, anns_field, filter, limit, output_fields, search_params):
        import re
        conditions = re.findall(r'metadata\["(\w+)"\]\s*==\s*([\w.-]+)', filter or "")
        hits = []
        for pk, row in self.rows[collection_name].items():
            meta = row["metadata"]
            if all(str(meta.get(k)) == v.strip("'\"") for k, v in conditions):
                hits.append({"id": pk, "distance": 0.5, "entity": {"metadata": meta}})
        return [hits[:limit]]

    def delete(self, collection_name, ids=None, filter=None):
        for pk in ids or []:
            self.rows[collection_name].pop(pk, None)

    def drop_collection(self, collection_name):
        self.collections.pop(collection_name, None)
        self.rows.pop(collection_name, None)


async def test_talent_vector_store_offline_contract(monkeypatch):
    """gxd-离线验证人才集合契约：建集合幂等、JSON 过滤、覆盖写、指定删除、维度校验。"""
    from app.core.exceptions import AIError

    monkeypatch.setattr("app.config.talent_config.talent_settings.embedding_dim", 2)
    client = _FakeMilvus()
    store = TalentVectorStore(client=client)
    await store.create_collection("talent_vectors", 2)
    await store.create_collection("talent_vectors", 2)          # 幂等
    assert "talent_vectors" in client.collections

    await store.upsert("talent_vectors", ["t1", "t2"], [[1.0, 0.0], [0.0, 1.0]],
                       [{"talent_id": 1, "version": 1}, {"talent_id": 2, "version": 1}])
    hits = await store.search("talent_vectors", [1.0, 0.0], 5, expr='metadata["talent_id"] == 1')
    assert [h["id"] for h in hits] == ["t1"] and hits[0]["metadata"]["version"] == 1

    await store.upsert("talent_vectors", ["t1"], [[0.9, 0.1]], [{"talent_id": 1, "version": 2}])
    hits = await store.search("talent_vectors", [1.0, 0.0], 5, expr='metadata["version"] == 2')
    assert [h["id"] for h in hits] == ["t1"]

    await store.delete("talent_vectors", ids=["t1"])
    assert not await store.search("talent_vectors", [1.0, 0.0], 5, expr='metadata["talent_id"] == 1')

    with pytest.raises(AIError):
        await store.upsert("talent_vectors", ["bad"], [[1.0, 0.0, 0.0]], [{"talent_id": 3}])

    await store.drop("talent_vectors")
    assert "talent_vectors" not in client.collections
