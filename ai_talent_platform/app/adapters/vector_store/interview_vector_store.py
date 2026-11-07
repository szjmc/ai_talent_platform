"""
标签：[Agent组] [P1]
模块：app/adapters/vector_store/interview_vector_store.py
职责：面试记录表向量库适配（测评组自有，独立集合 + 本地降级）。

============================ 与 RAG 组的关系 ============================
- RAG 组的 milvus_adapter / question_bank_adapter 管理 knowledge_chunks、
  question_bank_vectors 等固定 Schema 集合；本模块只管理测评组自己的
  interview_record_vectors 集合（动态字段 + 本地降级），互不覆盖、互不影响；
- MILVUS_ENABLED=true 时优先写 Milvus；未启用 / 依赖缺失 / 初始化失败时
  自动降级 LocalInterviewVectorStore（JSON 持久化 + numpy 余弦相似度），
  保证开发与测试环境全链路可跑；
- 向量维度以 app.ai.rag.record_embedding.embed_dim() 为准（固定 128）。

对外 API：
    get_vector_store() -> InterviewVectorStoreBase
    new_vector_id()    -> str
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = get_logger(__name__)

_LOCAL_VECTOR_DIR = os.path.join(settings.UPLOAD_DIR, "..", "interview_vector_store")


# ================================================================ 抽象接口
class InterviewVectorStoreBase(ABC):
    """面试记录向量库统一接口（与 RAG 组 BaseVectorStore 隔离，签名自持）。"""

    @abstractmethod
    async def create_collection(self, name: str, dim: int) -> None: ...

    @abstractmethod
    async def upsert(self, collection: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict] | None = None,
                     docs: list[str] | None = None) -> None: ...

    @abstractmethod
    async def search(self, collection: str, vector: list[float], top_k: int = 10,
                     expr: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def delete(self, collection: str, ids: list[str] | None = None,
                     expr: str | None = None) -> None: ...

    @abstractmethod
    async def drop(self, collection: str) -> None: ...


# ================================================================ Milvus 实现
class MilvusInterviewStore(InterviewVectorStoreBase):
    """pymilvus 封装。MILVUS_ENABLED=false 时构造抛 ExternalServiceError。"""

    def __init__(self) -> None:
        if not settings.MILVUS_ENABLED:
            raise ExternalServiceError("Milvus 未启用")
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise ExternalServiceError(f"pymilvus 未安装：{exc}") from exc
        self._client = MilvusClient(uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN or None)

    async def create_collection(self, name: str, dim: int) -> None:
        from pymilvus import DataType
        if self._client.has_collection(name):
            return
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="HNSW",
                               metric_type="COSINE", params={"M": 16, "efConstruction": 200})
        self._client.create_collection(name, schema=schema, index_params=index_params)
        logger.info("Milvus 面试记录集合已创建：%s dim=%s", name, dim)

    async def upsert(self, collection: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict] | None = None,
                     docs: list[str] | None = None) -> None:
        rows = []
        for i, rid in enumerate(ids):
            row: dict[str, Any] = {"id": rid, "vector": vectors[i]}
            if metadata:
                row.update(metadata[i] or {})
            if docs:
                row["doc"] = docs[i][:8000]
            rows.append(row)
        self._client.upsert(collection, rows)

    async def search(self, collection: str, vector: list[float], top_k: int = 10,
                     expr: str | None = None) -> list[dict]:
        kwargs: dict[str, Any] = {
            "collection_name": collection, "data": [vector], "limit": top_k,
            "output_fields": ["*"], "anns_field": "vector",
        }
        if expr:
            kwargs["filter"] = expr
        res = self._client.search(**kwargs)
        hits = []
        for hit in (res[0] if res else []):
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            hits.append({
                "id": hit.get("id"),
                "score": float(hit.get("distance", 0.0)),
                "metadata": {k: v for k, v in entity.items() if k not in ("vector", "doc")},
                "doc": entity.get("doc", ""),
            })
        return hits

    async def delete(self, collection: str, ids: list[str] | None = None,
                     expr: str | None = None) -> None:
        if ids:
            self._client.delete(collection, ids=ids)
        elif expr:
            self._client.delete(collection, filter=expr)

    async def drop(self, collection: str) -> None:
        if self._client.has_collection(collection):
            self._client.drop_collection(collection)


# ================================================================ 本地降级实现
class LocalInterviewStore(InterviewVectorStoreBase):
    """无 Milvus 时的降级实现（JSON 持久化 + numpy 余弦相似度）。"""

    def __init__(self, base_dir: str | None = None) -> None:
        self._dir = base_dir or os.path.normpath(_LOCAL_VECTOR_DIR)
        os.makedirs(self._dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name)
        return os.path.join(self._dir, f"{safe}.json")

    def _load(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"dim": 0, "rows": {}}

    def _save(self, name: str, data: dict[str, Any]) -> None:
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    async def create_collection(self, name: str, dim: int) -> None:
        with self._lock:
            data = self._load(name)
            if data["dim"] and data["dim"] != dim:
                raise ValueError(f"集合 {name} 维度冲突：已有 {data['dim']}，请求 {dim}")
            data["dim"] = dim
            self._save(name, data)

    async def upsert(self, collection: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict] | None = None,
                     docs: list[str] | None = None) -> None:
        with self._lock:
            data = self._load(collection)
            data["dim"] = data["dim"] or len(vectors[0])
            for i, rid in enumerate(ids):
                data["rows"][str(rid)] = {
                    "vector": vectors[i],
                    "metadata": metadata[i] if metadata else {},
                    "doc": docs[i] if docs else "",
                }
            self._save(collection, data)

    async def search(self, collection: str, vector: list[float], top_k: int = 10,
                     expr: str | None = None) -> list[dict]:
        data = self._load(collection)
        if not data["rows"]:
            return []
        parsed = _parse_expr(expr or "")
        q = np.array(vector, dtype=np.float32)
        scored: list[tuple[float, dict]] = []
        for rid, row in data["rows"].items():
            meta = row.get("metadata") or {}
            if parsed and not all(meta.get(k) == v for k, v in parsed.items()):
                continue
            v = np.array(row["vector"], dtype=np.float32)
            denom = float(np.linalg.norm(q) * np.linalg.norm(v)) or 1.0
            scored.append((float(np.dot(q, v) / denom), {
                "id": rid, "metadata": meta, "doc": row.get("doc", ""),
            }))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"score": s, **item} for s, item in scored[:top_k]]

    async def delete(self, collection: str, ids: list[str] | None = None,
                     expr: str | None = None) -> None:
        with self._lock:
            data = self._load(collection)
            if ids:
                for rid in ids:
                    data["rows"].pop(str(rid), None)
            elif expr:
                parsed = _parse_expr(expr)
                data["rows"] = {rid: row for rid, row in data["rows"].items()
                                if not all((row.get("metadata") or {}).get(k) == v
                                           for k, v in parsed.items())}
            self._save(collection, data)

    async def drop(self, collection: str) -> None:
        with self._lock:
            path = self._path(collection)
            if os.path.exists(path):
                os.remove(path)


_EXPR_RE = re.compile(r"(\w+)\s*(?:==|=)\s*['\"]?([^,'\"]+)['\"]?")


def _parse_expr(expr: str) -> dict[str, Any]:
    """把 Milvus 风格 expr（talent_id == 3 and assessment_id == 9）解析成本地过滤条件。"""
    if not expr:
        return {}
    out: dict[str, Any] = {}
    for key, raw in _EXPR_RE.findall(expr):
        val: Any = raw.strip()
        if re.fullmatch(r"-?\d+", val):
            val = int(val)
        elif re.fullmatch(r"-?\d+\.\d+", val):
            val = float(val)
        out[key] = val
    return out


# ================================================================ 工厂
_store: InterviewVectorStoreBase | None = None


def get_vector_store() -> InterviewVectorStoreBase:
    """取面试记录向量库实例：Milvus 优先，未启用/失败降级 local。"""
    global _store
    if _store is not None:
        return _store
    if settings.MILVUS_ENABLED:
        try:
            _store = MilvusInterviewStore()
        except ExternalServiceError as exc:
            logger.warning("Milvus 初始化失败，降级本地向量库：%s", exc)
            _store = LocalInterviewStore()
    else:
        _store = LocalInterviewStore()
    logger.info("面试记录向量库实例：%s", type(_store).__name__)
    return _store


def new_vector_id() -> str:
    """生成向量主键（面试记录表向量 id）。"""
    return uuid.uuid4().hex


__all__ = [
    "InterviewVectorStoreBase",
    "MilvusInterviewStore",
    "LocalInterviewStore",
    "get_vector_store",
    "new_vector_id",
]
