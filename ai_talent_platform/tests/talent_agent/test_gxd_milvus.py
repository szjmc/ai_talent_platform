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

    def describe_collection(self, collection_name):
        options = self.collections[collection_name]
        primary = options.get("primary_field_name") or "id"
        return {"fields": [{"name": primary, "is_primary": True},
                           {"name": "vector", "params": {"dim": options.get("dimension")}}],
                "enable_dynamic_field": bool(options.get("enable_dynamic_field", True))}

    def add_collection_field(self, collection_name, field_name, data_type, nullable=True):
        # Milvus Lite / 老版本 Milvus 不支持在线加字段（UNIMPLEMENTED）
        raise NotImplementedError("add_collection_field")

    def _primary(self, collection_name):
        return self.collections[collection_name].get("primary_field_name") or "id"

    def upsert(self, collection_name, data):
        primary = self._primary(collection_name)
        for row in data:
            self.rows[collection_name][row[primary]] = row

    def query(self, collection_name, filter, output_fields, limit):
        import re
        primary = self._primary(collection_name)
        rows = list(self.rows[collection_name].values())
        equals = re.findall(r"(\w+)\s*==\s*([\w.-]+)", filter or "")
        if " in [" in (filter or ""):
            match = re.search(r"(\w+) in \[([^\]]*)\]", filter)
            if match:
                allowed = {int(x) for x in match.group(2).split(",") if x.strip()}
                rows = [r for r in rows if r.get(match.group(1)) in allowed]
        rows = [r for r in rows if all(str(r.get(k)) == v.strip("'\"") for k, v in equals)]
        return [{k: r.get(k) for k in output_fields if k in r} for r in rows[:limit]]

    def search(self, collection_name, data, limit, filter, output_fields):
        import re
        conditions = re.findall(r"(\w+)\s*==\s*([\w.-]+)", filter or "")
        hits = []
        for pk, row in self.rows[collection_name].items():
            if all(str(row.get(key)) == value.strip("'\"") for key, value in conditions):
                entity = {key: row[key] for key in output_fields if key in row}
                hits.append({"id": pk, "distance": 0.5, "entity": entity})
        return [hits[:limit]]

    def delete(self, collection_name, ids=None, filter=None):
        # MilvusClient 的参数名是 ids；写成 id= 会被当作 filter 表达式（实测直接抛错）
        for pk in ids or []:
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


async def test_talent_vectors_primary_field_talent_id(monkeypatch):
    """gxd-一人一条向量：主键 talent_id，重复同步覆盖同一行，可按主键读回。"""
    import app.adapters.vector_store.talent_vector_store as module

    client = _FakeMilvus()
    monkeypatch.setattr(module, "_get_client", lambda: client)
    store = module.TalentVectorStore()
    await store.create_collection("talent_vectors", 2, primary_field="talent_id")
    assert await store.primary_key_of("talent_vectors") == "talent_id"

    await store.upsert("talent_vectors", [7], [[1.0, 0.0]],
                       [{"version": 3, "kind": "profile", "name": "甲"}], primary_field="talent_id")
    await store.upsert("talent_vectors", [7], [[0.5, 0.5]],
                       [{"version": 4, "kind": "profile", "name": "甲"}], primary_field="talent_id")
    rows = await store.query("talent_vectors", "talent_id == 7",
                             output_fields=["talent_id", "version"], limit=10)
    assert len(rows) == 1 and rows[0]["version"] == 4 and rows[0]["talent_id"] == 7

    # 主键名不一致的既有集合必须报错，不能静默写入错误的 Schema
    client.collections["legacy_collection"] = {"primary_field_name": "id"}
    client.rows["legacy_collection"] = {}
    with pytest.raises(module.TalentVectorUnavailableError):
        await store.create_collection("legacy_collection", 2, primary_field="talent_id")


# ---------------------------------------------------------------- 本轮修复的回归
class _DeleteRecorder:
    """记录 delete 调用参数的替身（校验删档/合并后真的按 talent_id 删了向量）。"""

    def __init__(self, rows=None):
        self.deleted = []
        self._rows = rows or {}

    def has_collection(self, name):
        return True

    def delete(self, collection_name, ids=None, filter=None):
        self.deleted.append({"collection": collection_name, "ids": ids, "filter": filter})
        return list(ids or [])

    def query(self, collection_name, filter, output_fields, limit):
        return []


async def test_drop_index_deletes_by_talent_id(monkeypatch):
    """gxd-删档 / 合并后必须按 talent_id 删除向量，不能只软删 MySQL。"""
    from app.config.settings import settings
    from app.services.talent_retrieval_service import TalentRetrievalService

    from app.ai.context import AIContext
    from app.config.talent_config import talent_settings

    monkeypatch.setattr(settings, "MILVUS_ENABLED", True)
    client = _DeleteRecorder()
    store = TalentVectorStore()
    monkeypatch.setattr(store, "_client", lambda: client)
    service = TalentRetrievalService(None, context=AIContext(user_id=1, roles=["admin"]), vector=store)

    assert await service.drop_index(7) is True
    # MilvusClient.delete 的关键字是 ids；写成 id= 会被当成 filter 表达式（实测抛错）
    assert client.deleted == [{"collection": talent_settings.collection, "ids": [7], "filter": None}]


async def test_create_collection_enables_dynamic_field(monkeypatch):
    """gxd-新建集合必须开启动态字段：画像向量会带 version/kind/name/updated_at。

    回归：脚本旧定义建的是严格静态 Schema，写 version 直接报
    DataNotMatchException「unexpected field version」，整条索引链路失败。
    """
    import app.adapters.vector_store.talent_vector_store as module

    client = _FakeMilvus()
    monkeypatch.setattr(module, "_get_client", lambda: client)
    store = module.TalentVectorStore()
    await store.create_collection("talent_vectors", 4, primary_field="talent_id",
                                  metadata_fields=("version", "name", "kind", "updated_at"))
    assert client.collections["talent_vectors"]["enable_dynamic_field"] is True
    assert client.collections["talent_vectors"]["primary_field_name"] == "talent_id"


async def test_create_collection_rejects_static_schema_and_dim_mismatch(monkeypatch):
    """gxd-旧静态集合 / 维度不符时明确报错并给出修复命令，而不是等到 upsert 才失败。"""
    import app.adapters.vector_store.talent_vector_store as module

    class StaticClient(_FakeMilvus):
        def create_collection(self, collection_name, **kwargs):
            # 模拟脚本旧定义：没有开启动态字段
            self.collections[collection_name] = {**kwargs, "enable_dynamic_field": False}
            self.rows[collection_name] = {}

        def add_collection_field(self, *args, **kwargs):
            raise NotImplementedError("Milvus Lite 不支持在线加字段")

    client = StaticClient()
    monkeypatch.setattr(module, "_get_client", lambda: client)
    store = module.TalentVectorStore()
    await store.create_collection("legacy", 4, primary_field="talent_id")
    with pytest.raises(module.TalentVectorUnavailableError) as excinfo:
        await store.create_collection("legacy", 4, primary_field="talent_id",
                                      metadata_fields=("version",))
    assert "--replace" in str(excinfo.value)
    with pytest.raises(module.TalentVectorUnavailableError) as excinfo:
        await store.create_collection("legacy", 8, primary_field="talent_id")
    assert "维度" in str(excinfo.value)
