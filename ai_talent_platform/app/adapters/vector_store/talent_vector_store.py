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
    create_collection(name, dim, primary_field) 建集合（COSINE 相似度，主键可指定）
    has_collection(name) / stats(name)          存在性 / 行数
    primary_key_of(name) / describe(name)       主键探测（校验与团队 Schema 是否一致）
    upsert(collection, ids, vectors, metadata)  写入 / 更新（按 1000 条分批，主键可指定）
    query(collection, expr, output_fields)      按主键 / 过滤条件读回已写入的行
    search(collection, vector, top_k, expr)     相似度检索（expr 为权限过滤条件）
    delete(collection, ids | expr)              删除
    drop(name)                                  删除集合
    health()                                    探活

主键约定（2026-11 对齐）：
    talent_vectors 的主键是 talent_id（与 MySQL talents.id 同值、同名），一名人才一条
    复合画像向量。历史上这里写死 "id" 并接受字符串切片 ID，导致：
        - 字符串 chunk_id（t{id}:v{ver}:{n}:{hash}）写入 INT64 主键 → 整批写入失败；
        - 读回条件却用 metadata["talent_id"]，与写入结构不一致 → 永远查不到。
    现在主键字段名由调用方传入（默认 "id" 保持其他集合适配器不变）。

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
    async def create_collection(
        self,
        name: str,
        dim: int,
        *,
        primary_field: str = "id",
        metadata_fields: Sequence[str] = (),
    ) -> None:
        """建集合（存在则校验），保证「写入端能写、读取端能读」。

        校验三件事，任一不满足都直接报错而不是静默写坏数据：
            1. 主键必须是 primary_field（talent_vectors 固定 talent_id）；
            2. 向量维度必须与当前 embedding 模型一致（换模型后旧集合必须重建）；
            3. 集合要么开启动态字段，要么已声明我们要写的全部 metadata_fields
               （scripts/init_milvus.py 早期版本建的是严格静态 Schema：既没开动态
               字段，又要求 organization_id/status/skill_tags 必填，直接 upsert
               version 会失败——这里能补的字段就地补齐，补不了给出可执行的修复命令）。
        """
        client = self._client()
        if not await _run(client.has_collection, name):
            await _run(
                client.create_collection,
                collection_name=name,
                dimension=int(dim),
                primary_field_name=primary_field,
                id_type="int",
                vector_field_name="vector",
                metric_type=DEFAULT_METRIC,
                auto_id=False,
                # 动态字段让 name/kind/updated_at 这类展示型元数据无需改 Schema；
                # 与 scripts/init_milvus.py 的新定义保持一致。
                enable_dynamic_field=True,
            )
            logger.info(
                "Milvus 集合创建完成：%s dim=%s metric=%s primary=%s",
                name, dim, DEFAULT_METRIC, primary_field,
            )
            return

        info = await self.describe(name)
        current = _primary_of(info)
        if current and current != primary_field:
            raise TalentVectorUnavailableError(
                f"集合 {name} 的主键是 {current}，与当前实现要求的 {primary_field} 不一致；"
                f"请先清理该集合后由同步任务重建（历史写入本身也不可用）"
            )
        actual_dim = _dim_of(info)
        if actual_dim and int(actual_dim) != int(dim):
            raise TalentVectorUnavailableError(
                f"集合 {name} 的向量维度是 {actual_dim}，与当前 embedding 的 {dim} 维不一致；"
                f"换模型后必须重建集合：python scripts/init_milvus.py --replace {name} --dim {int(dim)}"
            )
        await self._ensure_metadata_fields(name, info, metadata_fields)
        logger.info("Milvus 集合已存在且结构匹配，跳过创建：%s", name)

    async def _ensure_metadata_fields(self, name: str, info: dict[str, Any], fields: Sequence[str]) -> None:
        """校验 / 补齐写入端需要的 metadata 字段（只补可空字段，不动已有数据）。"""
        declared = _field_map(info)
        dynamic = bool(info.get("enable_dynamic_field"))
        missing = [f for f in fields if f not in declared]
        if not missing:
            return
        if dynamic:
            # 动态字段模式下未知字段直接写入，无需改 Schema。
            return
        added, failed = [], []
        add = getattr(self._client(), "add_collection_field", None)
        if add is None:
            failed = list(missing)
        else:
            # 先试一个字段探能力：Milvus Lite / 老版本服务端不支持在线加字段，
            # 逐个试只会在日志里刷满四条 UNIMPLEMENTED 堆栈。
            for index, field in enumerate(missing):
                try:
                    await _run(
                        add, collection_name=name, field_name=field,
                        data_type=_metadata_dtype(field), nullable=True,
                    )
                    added.append(field)
                except Exception as exc:  # noqa: BLE001 - 老版本 Milvus / Lite 不支持加字段
                    logger.warning("Milvus 集合 %s 不支持在线补字段（%s），剩余字段需要重建集合",
                                   name, type(exc).__name__)
                    failed = list(missing[index:])
                    break
        if failed:
            raise TalentVectorUnavailableError(
                f"集合 {name} 未开启动态字段，且缺少写入需要的字段 {failed}"
                f"（服务端不支持在线加字段）；"
                f"请执行 python scripts/init_milvus.py --replace {name} 重建为该集合的当前定义"
            )
        # 严格 Schema 下还要确认：必填但没人写的字段会让 upsert 直接失败。
        refreshed = await self.describe(name)
        unknown_required = [
            fname for fname, fp in _field_map(refreshed).items()
            if not fp.get("is_primary") and fname != "vector"
            and not fp.get("nullable") and not fp.get("default_value")
            and fname not in set(fields)
        ]
        if unknown_required:
            raise TalentVectorUnavailableError(
                f"集合 {name} 存在必填且写入端未提供的字段 {unknown_required}；"
                f"请执行 python scripts/init_milvus.py --replace {name} 重建"
            )
        logger.info("Milvus 集合 %s 已补齐 metadata 字段：%s", name, added)

    async def has_collection(self, name: str) -> bool:
        return bool(await _run(self._client().has_collection, name))

    async def describe(self, name: str) -> dict[str, Any]:
        """集合结构（主键、向量维度、动态字段开关）。"""
        return dict(await _run(self._client().describe_collection, name) or {})

    async def primary_key_of(self, name: str) -> str | None:
        """读回集合主键字段名；集合不存在时返回 None。"""
        try:
            info = await self.describe(name)
        except Exception:  # noqa: BLE001 - 探测失败按「未知」处理，由上层决定降级
            return None
        for field in info.get("fields") or []:
            if field.get("is_primary"):
                return field.get("name")
        return None

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
        *,
        primary_field: str = "id",
    ) -> int:
        """写入 / 覆盖向量（按 1000 条分批）。返回写入条数。

        primary_field 决定主键列名：talent_vectors 传 "talent_id"，其余集合沿用默认 "id"。
        ids 必须是 int（Milvus INT64 主键）；字符串切片 ID 会被拒绝，避免静默写成错误数据。
        """
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
            rows = []
            for id_, vec, meta in zip(batch_ids, batch_vectors, batch_metas):
                key = int(id_)
                info = {
                    k: v for k, v in (meta or {}).items()
                    # 主键/向量/同名 id 不能出现在 metadata；None 写入动态字段会报类型错误。
                    if k not in (primary_field, "vector", "id") and v is not None
                }
                rows.append({primary_field: key, "vector": list(vec), **info})
            await _run(client.upsert, collection_name=collection, data=rows)
            count += len(rows)
        return count

    # ---------------------------------------------------------------- 读回
    async def query(
        self,
        collection: str,
        expr: str,
        *,
        output_fields: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """按过滤表达式读回已写入的行（不计算相似度）。

        expr 里的主键名必须与写入时一致，例如 talent_id == 12。
        """
        client = self._client()
        raw = await _run(
            client.query,
            collection_name=collection,
            filter=expr,
            output_fields=list(output_fields or ["*"]),
            limit=max(1, int(limit)),
        )
        rows: list[dict[str, Any]] = []
        for item in list(raw or []):
            rows.append(dict(item) if isinstance(item, dict) else item)
        return rows

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
            # MilvusClient.delete 的参数名是 ids；写成 id=... 会被当成 filter 表达式
            # （实测 pymilvus 3.x 抛 DataTypeNotMatchException），删除静默失效。
            await _run(client.delete, collection_name=collection, ids=id_list)
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


def _field_map(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """describe_collection 结果 -> {字段名: 字段定义}。"""
    return {f.get("name"): f for f in (info.get("fields") or []) if f.get("name")}


def _primary_of(info: dict[str, Any]) -> str | None:
    for field in info.get("fields") or []:
        if field.get("is_primary"):
            return field.get("name")
    return None


def _dim_of(info: dict[str, Any]) -> int | None:
    for field in info.get("fields") or []:
        if field.get("name") == "vector":
            dim = (field.get("params") or {}).get("dim")
            return int(dim) if dim is not None else None
    return None


def _metadata_dtype(field: str) -> Any:
    """metadata 字段对应的 Milvus 类型（仅用于在线补字段）。"""
    try:
        from pymilvus import DataType
    except ImportError:  # pragma: no cover - 未安装 pymilvus 时上层已降级
        return None
    if field in ("version", "organization_id"):
        return DataType.INT64
    if field == "skill_tags":
        return DataType.JSON
    return DataType.VARCHAR


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
