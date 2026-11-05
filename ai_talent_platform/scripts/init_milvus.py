"""初始化 Milvus 向量集合（架构组 + RAG 组共用，可重复执行）。

标签：[架构组]+[RAG组] [P1]（4 个向量集合定义与幂等创建）

用法：
    python scripts/init_milvus.py                 # 连接并创建/校验 4 个集合
    python scripts/init_milvus.py --plan          # 只打印集合设计，不连接 Milvus
    python scripts/init_milvus.py --drop          # 危险：删除 4 个集合后重建

前置：pip install pymilvus>=2.4，并在 .env 设置 MILVUS_ENABLED=true / MILVUS_URI / MILVUS_TOKEN。
未安装 pymilvus 时会打印安装命令与设计清单后退出（退出码 1），不会静默成功。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.settings import settings  # noqa: E402

# 集合设计（与 docs/database_design.md 第八节一致）
COLLECTIONS: list[dict[str, Any]] = [
    {
        "name": "talent_vectors",
        "description": "人才向量（语义检索 / 简历查重）",
        "primary_field": "talent_id",
        "scalar_fields": [
            ("organization_id", "int64"),
            ("status", "varchar"),
            ("skill_tags", "json"),
        ],
    },
    {
        "name": "position_vectors",
        "description": "岗位向量（语义匹配）",
        "primary_field": "position_id",
        "scalar_fields": [
            ("organization_id", "int64"),
            ("status", "varchar"),
            ("level", "varchar"),
        ],
    },
    {
        "name": "knowledge_vectors",
        "description": "知识切片向量（RAG，带权限过滤字段）",
        "primary_field": "chunk_id",
        "scalar_fields": [
            ("doc_id", "int64"),
            ("organization_id", "int64"),
            ("allowed_roles", "json"),
            ("category", "varchar"),
        ],
    },
    {
        "name": "question_vectors",
        "description": "题库向量（相似题去重 / 智能抽题）",
        "primary_field": "question_id",
        "scalar_fields": [
            ("dimension", "varchar"),
            ("difficulty", "varchar"),
        ],
    },
]

DEFAULT_DIM = 1024          # text-embedding-v3 默认维度
DEFAULT_METRIC = "COSINE"
DEFAULT_INDEX = "IVF_FLAT"
DEFAULT_NLIST = 1024


def print_plan(dim: int = DEFAULT_DIM) -> None:
    print(f"Milvus：{settings.MILVUS_URI}（enabled={settings.MILVUS_ENABLED}）")
    print(f"向量维度：{dim}　度量：{DEFAULT_METRIC}　索引：{DEFAULT_INDEX}(nlist={DEFAULT_NLIST})")
    print(f"{'集合':<20}{'主键':<16}{'标量字段'}")
    for spec in COLLECTIONS:
        scalars = ", ".join(f"{name}:{type_}" for name, type_ in spec["scalar_fields"])
        print(f"{spec['name']:<20}{spec['primary_field']:<16}{scalars}")


def _connect():
    from pymilvus import connections  # type: ignore

    connections.connect(
        alias="default",
        uri=settings.MILVUS_URI,
        token=settings.MILVUS_TOKEN or None,
    )


def create_all(dim: int, drop: bool) -> int:
    from pymilvus import Collection, FieldSchema, CollectionSchema, DataType, utility  # type: ignore

    _connect()
    type_map = {
        "int64": DataType.INT64,
        "varchar": DataType.VARCHAR,
        "json": DataType.JSON,
        "bool": DataType.BOOL,
    }

    for spec in COLLECTIONS:
        name = spec["name"]
        if drop and utility.has_collection(name):
            Collection(name).drop()
            print(f"[drop] {name}")
        if utility.has_collection(name):
            print(f"[skip] {name} 已存在")
            continue
        fields = [
            FieldSchema(name=spec["primary_field"], dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        ]
        for field_name, field_type in spec["scalar_fields"]:
            kwargs: dict[str, Any] = {}
            if field_type == "varchar":
                kwargs["max_length"] = 128
            fields.append(FieldSchema(name=field_name, dtype=type_map[field_type], **kwargs))

        collection = Collection(name=name, schema=CollectionSchema(fields=fields, description=spec["description"]))
        collection.create_index(
            field_name="vector",
            params={"index_type": DEFAULT_INDEX, "metric_type": DEFAULT_METRIC, "params": {"nlist": DEFAULT_NLIST}},
        )
        print(f"[create] {name}（{spec['description']}）")

    print("Milvus 集合初始化完成，可重复执行")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 Milvus 向量集合")
    parser.add_argument("--plan", action="store_true", help="只打印设计，不连接")
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM, help=f"向量维度，默认 {DEFAULT_DIM}")
    parser.add_argument("--drop", action="store_true", help="先删除已有集合再创建（危险）")
    args = parser.parse_args()

    if args.plan:
        print_plan(dim=args.dim)
        return

    try:
        import pymilvus  # noqa: F401
    except ImportError:
        print_plan(dim=args.dim)
        print("\n未安装 pymilvus，无法创建集合。执行：pip install pymilvus>=2.4")
        raise SystemExit(1)

    if not settings.MILVUS_ENABLED:
        print("MILVUS_ENABLED=False，请先在 .env 中启用并配置 MILVUS_URI")
        raise SystemExit(1)

    try:
        raise SystemExit(create_all(dim=args.dim, drop=args.drop))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"Milvus 初始化失败：{exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
