"""创建 RAG 检索所需的 Milvus 集合（幂等，可重复执行）。

标签：[RAG组] [P1]（向量集合初始化工具）

背景：app/adapters/vector_store/milvus_adapter.py 的 ensure_collection 要求固定 Schema
    chunk_id(VARCHAR,主键,非自增) / file_id / category / content / source(VARCHAR)
    / page_number(INT64) / embedding(FLOAT_VECTOR)，
而 scripts/init_milvus.py 建的是 knowledge_vectors(chunk_id 为 INT64、向量字段名 vector)，
两者不兼容。本脚本按适配器契约建集合，集合名取 settings.MILVUS_COLLECTION_KNOWLEDGE。

用法：
    python scripts/init_rag_collection.py           # 建/校验集合
    python scripts/init_rag_collection.py --drop     # 危险：先删再建

"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.settings import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 RAG 知识向量集合")
    parser.add_argument("--drop", action="store_true", help="先删除已有集合再创建（危险）")
    args = parser.parse_args()

    from pymilvus import DataType, MilvusClient

    name = settings.MILVUS_COLLECTION_KNOWLEDGE
    dim = settings.EMBEDDING_DIM
    client = MilvusClient(uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN or "")
    print(f"Milvus {settings.MILVUS_URI} 集合={name} dim={dim}")

    if args.drop and client.has_collection(name):
        client.drop_collection(name)
        print(f"[drop] {name}")

    if client.has_collection(name):
        desc = client.describe_collection(name)
        fields = {f["name"]: f for f in desc.get("fields", [])}
        expected = {"chunk_id", "file_id", "category", "content", "source", "page_number", "embedding"}
        if expected.issubset(fields) and int(fields["embedding"].get("params", {}).get("dim", 0)) == dim:
            print(f"[skip] {name} 已存在且 Schema 兼容")
            client.load_collection(name)
            return 0
        print(f"[warn] {name} 已存在但 Schema 不兼容：{sorted(fields)}；请用 --drop 重建或换集合名")
        return 2

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=256)
    schema.add_field("file_id", DataType.VARCHAR, max_length=128)
    schema.add_field("category", DataType.VARCHAR, max_length=64)
    schema.add_field("content", DataType.VARCHAR, max_length=65535)
    schema.add_field("source", DataType.VARCHAR, max_length=512)
    schema.add_field("page_number", DataType.INT64)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="chunk_id", index_type="")

    client.create_collection(collection_name=name, schema=schema, index_params=index_params)
    client.load_collection(name)
    print(f"[create] {name} 完成并已 load")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
