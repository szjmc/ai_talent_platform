"""
模块：app/adapters/vector_store/talent_vector_store.py
职责：人才 / 岗位向量集合的适配器（lz_agent 分支能力，合并 origin/lz 后保留）。
标签：[Agent组] [P1]（依赖：[RAG组] infrastructure/vector/milvus.py 连接）

============================ 合并说明（必读） ============================
合并 origin/lz 后，milvus_adapter.py 采用 lz 线的「knowledge_chunks 固定 Schema
+ 分类路由」实现，服务知识库 / RAG / 简历解析链路，本文件对它不做任何修改。

本文件保留 lz_agent 线原有的「通用多集合 + metadata 过滤」能力，当前接入方：
    app/services/matching_pipeline.py         岗位画像 → 人才向量召回

注：app/services/talent_retrieval_service.py（origin/lz 侧 gxd 的模块）与本文件接口
    尚未对接，该模块目前也未挂到任何已注册路由上（talents 前缀仍为 501 占位），
    待其负责人重接后可直接复用本类：TalentVectorStore()。

两套适配器并存、职责分离：
    milvus_adapter.MilvusAdapter  -> 知识库固定 Schema（业务：知识库 / 简历解析 / RAG）
    talent_vector_store.TalentVectorStore -> 通用多集合（业务：人才 / 岗位匹配）

接口（与 BaseVectorStore 建议签名一致）：
    create_collection(name, dim)               建集合（COSINE 相似度）
    has_collection(name) / stats(name)         存在性 / 行数
    upsert(collection, ids, vectors, metadata) 写入 / 更新（按 1000 条分批）
    search(collection, vector, top_k, expr)    相似度检索（expr 为权限过滤条件）
    delete(collection, ids | expr)             删除
    drop(name)                                 删除集合
    health()                                   探活

降级约定：
    未启用 Milvus 或 pymilvus 缺失时抛 TalentVectorUnavailableError，
    调用方（matching_pipeline）捕获后回退 SQL 通道，绝不伪造向量结果。
=========================================================================
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Sequence

from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

# 默认相似度：COSINE 对 1024 维归一化向量（bge-m3 / text-embedding-v3）最稳
DEFAULT_METRIC = "COSINE"
UPSERT_BATCH_SIZE = 1000

_client: Any | None = None
_lock = threading.Lock()


class TalentVectorUnavailableError(ExternalServiceError):
    """人才向量通道不可用（未启用 / 未安装 pymilvus / 连接失败）。"""


def _build_client() -> Any:
    """复用 lz 线 create_client 建连，避免重复实现连接参数。"""
    from app.adapters.vector_store.milvus_adapter import create_client

    return create_client(settings.MILVUS_URI, settings.MILVUS_TOKEN)


def _get_client() -> Any:
    """取进程内单例客户端；未启用时抛异常由上层降级。"""
    global _client
    if not settings.MILVUS_ENABLED:
        raise TalentVectorUnavailableError("MILVUS_ENABLED=false，向量能力未启用")
    if _client is None:
        with _lock:
            if _client is None:
                _client = _build_client()
    return _client


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """把同步 Milvus 调用放到线程池执行（避免阻塞事件循环）。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


def reset_client() -> None:
    """重置单例（测试与配置变更后使用）。"""
    global _client
    _client = None


class TalentVectorStore:
    """人才 / 岗位向量集合适配器（通用多集合，metadata 过滤）。"""

    name = "milvus_talent"

    # ---------------------------------------------------------------- 基础
    def _client(self) -> Any:
        return _get_client()

    # ---------------------------------------------------------------- 集合管理
    async def create_collection(self, name: str, dim: int) -> None:
        """建集合（存在则跳过）。id 为主键（int），vector 为 dim 维向量。"""
        client = self._client()
        if await _run(client.has_collection, name):
            logger.info("Milvus 集合已存在，跳过创建：%s", name)
            return
        await _run(
            client.create_collection,
            collection_name=name,
            dimension=int(dim),
            primary_field_name="id",
            id_type="int",
            vector_field_name="vector",
            metric_type=DEFAULT_METRIC,
            auto_id=False,
        )
        logger.info("Milvus 集合创建完成：%s dim=%s metric=%s", name, dim, DEFAULT_METRIC)

    async def has_collection(self, name: str) -> bool:
        return bool(await _run(self._client().has_collection, name))

    async def stats(self, name: str) -> dict[str, Any]:
        """集合行数等统计（用于「集合为空则降级」判断）。"""
        return dict(await _run(self._client().get_collection_stats, name) or {})

    async def drop(self, name: str) -> None:
        await _run(self._client().drop_collection, name)
        logger.warning("Milvus 集合已删除：%s", name)

    # ---------------------------------------------------------------- 写入
    async def upsert(
        self,
        collection: str,
        ids: Sequence[int],
        vectors: Sequence[Sequence[float]],
        metadata: Sequence[dict[str, Any]] | None = None,
    ) -> int:
        """写入 / 覆盖向量（按 1000 条分批）。返回写入条数。"""
        client = self._client()
        if len(ids) != len(vectors):
            raise ValueError(f"ids 与 vectors 数量不一致：{len(ids)} vs {len(vectors)}")
        metas = list(metadata or [])
        if metas and len(metas) != len(ids):
            raise ValueError(f"metadata 与 ids 数量不一致：{len(metas)} vs {len(ids)}")

        count = 0
        for start, batch_ids in enumerate_ids(ids, UPSERT_BATCH_SIZE):
            batch_vectors = list(vectors)[start : start + UPSERT_BATCH_SIZE]
            batch_metas = metas[start : start + UPSERT_BATCH_SIZE] if metas else [{}] * len(batch_ids)
            rows = [
                {"id": int(id_), "vector": list(vec), **(meta or {})}
                for id_, vec, meta in zip(batch_ids, batch_vectors, batch_metas)
            ]
            await _run(client.upsert, collection_name=collection, data=rows)
            count += len(rows)
        return count

    async def delete(
        self,
        collection: str,
        *,
        ids: Sequence[int] | None = None,
        expr: str | None = None,
    ) -> None:
        """按主键或过滤表达式删除（二选一，都传以 expr 为准）。"""
        client = self._client()
        if expr:
            await _run(client.delete, collection_name=collection, filter=expr)
        elif ids:
            id_list = [int(item) for item in ids]
            await _run(client.delete, collection_name=collection, id=id_list)
        else:
            raise ValueError("delete 需要 ids 或 expr 之一")

    # ---------------------------------------------------------------- 检索
    async def search(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        top_k: int = 10,
        expr: str | None = None,
        output_fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """相似度检索，返回 [{id, score, ...output_fields}]（按相似度倒序）。

        score 为相似度（COSINE，越大越相似），非距离。
        """
        client = self._client()
        raw = await _run(
            client.search,
            collection_name=collection,
            data=[list(vector)],
            limit=max(1, int(top_k)),
            filter=expr or "",
            output_fields=list(output_fields or []),
        )
        hits: list[dict[str, Any]] = []
        # MilvusClient.search 返回 [[{id, distance, entity}, ...]]
        for hit in (raw[0] if raw else []) or []:
            entity = hit.get("entity") or {}
            item: dict[str, Any] = {"id": hit.get("id"), "score": float(hit.get("distance") or 0)}
            item.update(entity if isinstance(entity, dict) else {})
            hits.append(item)
        return hits

    # ---------------------------------------------------------------- 探活
    async def health(self) -> dict[str, Any]:
        """探活：列出集合；未启用或连接失败时返回 ok=False，不抛异常。"""
        try:
            client = self._client()
            names = await _run(client.list_collections)
            return {"ok": True, "collections": list(names or [])}
        except Exception as exc:  # noqa: BLE001 - 探活失败要给可读原因
            return {"ok": False, "reason": str(exc)}


def enumerate_ids(ids: Sequence[int], size: int):
    """把 ids 切成 (start_offset, batch) 序列（配合 vectors 同步切片）。"""
    id_list = list(ids)
    for start in range(0, len(id_list), size):
        yield start, id_list[start : start + size]


__all__ = [
    "DEFAULT_METRIC",
    "TalentVectorStore",
    "TalentVectorUnavailableError",
    "UPSERT_BATCH_SIZE",
    "enumerate_ids",
    "reset_client",
]
