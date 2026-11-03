"""
模块：app/models/mapping.py
职责：全局 ORM 映射关系（ORM 类 <-> 表 <-> 字段 <-> 关系）的统一出口。

实现思路：
1. 直接从 SQLAlchemy registry（Base.registry / AnalyticsBase.registry）读取映射，
   不手工维护清单，保证「新增模型即自动出现在映射里」，不会出现文档与代码不同步；
2. 输出结构分两块：
     - mysql：业务主库 Base.metadata（25 张表）
     - pgsql：分析库 AnalyticsBase.metadata（6 张表，含分区信息）
3. 提供三种消费方式：
     - build_orm_mapping()：结构化 dict（给 API / 测试 / 程序用）
     - render_orm_mapping_markdown()：Markdown 文本（给文档与人看）
     - scripts/dump_orm_mapping.py：命令行输出/落盘

注意：本模块只读元数据，不连接数据库，不触发建表。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Mapper, registry as registry_type
from sqlalchemy.orm import registry as registry_type
from sqlalchemy.sql.schema import Table

from app.config.settings import settings
from app.models.analytics import DEFAULT_PARTITIONS, PARTITION_KEYS, AnalyticsBase
from app.models.base import Base

# MySQL -> PgSQL 分析表的字段来源说明（ETL 未实现，此处仅记录映射关系）
ANALYTICS_LINEAGE: dict[str, dict[str, str]] = {
    "fact_talent_snapshot": {
        "来源": "talents + organizations 按 (organization_id, education, 年龄段) 聚合",
        "分区键": "snapshot_month（RANGE，按月）",
    },
    "fact_assessment_history": {
        "来源": "assessments + assessment_results",
        "分区键": "scored_at（RANGE，按月）",
    },
    "fact_training_track": {
        "来源": "learning_records + training_plans + courses",
        "分区键": "event_at（RANGE）",
    },
    "fact_behavior_event": {
        "来源": "前端埋点 / 业务行为事件",
        "分区键": "event_at（RANGE，按天）",
    },
    "dim_organization": {"来源": "organizations 全量同步", "分区键": "无（维表）"},
    "dim_talent_wide": {
        "来源": "talents + talent_portraits + organizations + matching_records 打宽",
        "分区键": "无（宽表）",
    },
}


# --------------------------------------------------------------------- 基础工具
def _default_repr(column: Any) -> str:
    """取列的默认值（优先服务端默认值）。"""
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        text_value = getattr(arg, "text", arg)
        return f"服务器端 {text_value}"
    if column.default is not None and not getattr(column.default, "is_sequence", False):
        arg = getattr(column.default, "arg", None)
        return f"ORM {arg!r}"
    return "—"


def _fk_repr(column: Any) -> str | None:
    if not column.foreign_keys:
        return None
    parts = []
    for fk in sorted(column.foreign_keys, key=lambda item: item.target_fullname):
        ondelete = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
        parts.append(f"{fk.target_fullname}{ondelete}")
    return "; ".join(parts)


def _columns_of(table: Table) -> list[dict[str, Any]]:
    return [
        {
            "name": col.name,
            "type": str(col.type),
            "nullable": bool(col.nullable),
            "primary_key": bool(col.primary_key),
            "default": _default_repr(col),
            "foreign_key": _fk_repr(col),
            "comment": col.comment or "",
        }
        for col in table.columns
    ]


def _indexes_of(table: Table) -> list[dict[str, Any]]:
    return [
        {
            "name": idx.name,
            "columns": [col.name for col in idx.columns],
            "unique": bool(idx.unique),
        }
        for idx in sorted(table.indexes, key=lambda item: item.name or "")
    ]


def _uniques_of(table: Table) -> list[dict[str, Any]]:
    result = []
    for constraint in table.constraints:
        if constraint.__class__.__name__ != "UniqueConstraint":
            continue
        result.append(
            {
                "name": constraint.name,
                "columns": [col.name for col in constraint.columns],
            }
        )
    return sorted(result, key=lambda item: item["name"] or "")


def _relationships_of(mapper: Mapper) -> list[dict[str, Any]]:
    relationships = []
    for rel in sorted(mapper.relationships, key=lambda item: item.key):
        target_mapper = rel.mapper
        target = target_mapper.class_.__name__
        if rel.secondary is not None:
            kind = "多对多"
            target = f"{target}（经 {rel.secondary.name}）"
        elif rel.direction.name == "ONETOMANY":
            kind = "一对多" if rel.uselist else "一对一"
        else:
            kind = "多对一"
        relationships.append(
            {
                "attribute": rel.key,
                "kind": kind,
                "target": target,
                "target_table": target_mapper.local_table.name,
                "lazy": rel.lazy,
                "back_populates": rel.back_populates or "",
                "cascade": ",".join(sorted(rel.cascade)) if rel.cascade else "",
            }
        )
    return relationships


def _table_entry(mapper: Mapper, database: str, schema: str | None = None) -> dict[str, Any]:
    """把单个 mapper 转成映射字典。"""
    table: Table = mapper.local_table
    partition_by = table.dialect_options.get("postgresql", {}).get("partition_by")
    return {
        "database": database,
        "schema": (schema or settings.PGSQL_SCHEMA) if database == "pgsql" else None,
        "table": table.name,
        "model": mapper.class_.__name__,
        "module": mapper.class_.__module__,
        "comment": table.comment or "",
        "column_count": len(table.columns),
        "columns": _columns_of(table),
        "primary_key": [col.name for col in table.primary_key.columns],
        "indexes": _indexes_of(table),
        "unique_constraints": _uniques_of(table),
        "relationships": _relationships_of(mapper),
        "partition_by": partition_by,
        "default_partition": DEFAULT_PARTITIONS.get(table.name),
    }


def _mappers_of(registry: registry_type) -> list[Mapper]:
    """按表名排序返回 registry 中全部主 mapper（过滤继承映射）。"""
    mappers: dict[str, Mapper] = {}
    for mapper in registry.mappers:
        if mapper.local_table is None or mapper.class_.__mapper__ is not mapper:
            continue
        mappers[mapper.local_table.name] = mapper
    return [mappers[name] for name in sorted(mappers)]


# --------------------------------------------------------------------- 对外 API
def build_orm_mapping() -> dict[str, Any]:
    """构建全局 ORM 映射关系（不连库，纯元数据）。"""
    mysql_tables = [_table_entry(m, "mysql") for m in _mappers_of(Base.registry)]
    pgsql_tables = [
        _table_entry(m, "pgsql", settings.PGSQL_SCHEMA) for m in _mappers_of(AnalyticsBase.registry)
    ]
    return {
        "mysql": {
            "database": settings.DB_NAME,
            "dialect": "mysql+aiomysql",
            "orm_base": "app.models.base.Base",
            "table_count": len(mysql_tables),
            "column_count": sum(item["column_count"] for item in mysql_tables),
            "relationship_count": sum(len(item["relationships"]) for item in mysql_tables),
            "tables": mysql_tables,
        },
        "pgsql": {
            "database": settings.PGSQL_DB,
            "schema": settings.PGSQL_SCHEMA,
            "dialect": "postgresql+asyncpg",
            "orm_base": "app.models.analytics.AnalyticsBase",
            "table_count": len(pgsql_tables),
            "column_count": sum(item["column_count"] for item in pgsql_tables),
            "partition_parents": sorted(PARTITION_KEYS),
            "default_partitions": sorted(DEFAULT_PARTITIONS.values()),
            "lineage": ANALYTICS_LINEAGE,
            "tables": pgsql_tables,
        },
        "summary": {
            "mysql_tables": len(mysql_tables),
            "pgsql_tables": len(pgsql_tables),
            "total_tables": len(mysql_tables) + len(pgsql_tables),
            "total_columns": sum(item["column_count"] for item in mysql_tables + pgsql_tables),
        },
    }


def table_model_map(database: str = "mysql") -> dict[str, str]:
    """表名 -> ORM 类名 的扁平映射，便于按表名反查模型。"""
    mapping = build_orm_mapping()
    return {item["table"]: item["model"] for item in mapping[database]["tables"]}


def render_orm_mapping_markdown() -> str:
    """把全局 ORM 映射渲染成 Markdown（供 docs/orm_mapping.md 使用）。"""
    data = build_orm_mapping()
    lines: list[str] = ["# 全局 ORM 映射关系", ""]
    lines.append("> 本文由 `scripts/dump_orm_mapping.py` 从 SQLAlchemy 元数据自动生成，勿手工编辑。")
    lines.append("")
    lines.append(
        f"- MySQL 业务主库 `{data['mysql']['database']}`：{data['mysql']['table_count']} 张表 / "
        f"{data['mysql']['column_count']} 个字段 / {data['mysql']['relationship_count']} 条 ORM 关系"
    )
    lines.append(
        f"- PgSQL 分析库 `{data['pgsql']['database']}`（schema `{data['pgsql']['schema']}`）："
        f"{data['pgsql']['table_count']} 张表 / {data['pgsql']['column_count']} 个字段，"
        f"分区父表 {len(data['pgsql']['partition_parents'])} 张"
    )
    lines.append("")
    lines.append("| 库 | 表名 | ORM 类 | 定义文件 | 字段数 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for section in ("mysql", "pgsql"):
        for item in data[section]["tables"]:
            db_label = data[section]["database"]
            lines.append(
                f"| {db_label} | `{item['table']}` | `{item['model']}` | `{item['module']}` | {item['column_count']} |"
            )
    lines.append("")

    for section, title in (("mysql", "MySQL 业务主库"), ("pgsql", "PgSQL 分析库")):
        info = data[section]
        lines.append(f"## {title} `{info['database']}`")
        lines.append("")
        for item in info["tables"]:
            lines.append(f"### `{item['table']}` → `{item['model']}`")
            lines.append("")
            if item["comment"]:
                lines.append(f"{item['comment']}（定义：`{item['module']}`）")
                lines.append("")
            if item["partition_by"]:
                lines.append(f"- 分区：`PARTITION BY {item['partition_by']}`，默认分区 `{item['default_partition']}`")
            lines.append(f"- 主键：`{', '.join(item['primary_key']) if item['primary_key'] else '—'}`")
            lines.append("")
            lines.append("| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for col in item["columns"]:
                lines.append(
                    "| `{name}` | `{type}` | {null} | {default} | {pk} | {fk} | {comment} |".format(
                        name=col["name"],
                        type=col["type"].replace("|", "\\|"),
                        null="是" if col["nullable"] else "否",
                        default=col["default"],
                        pk="✅" if col["primary_key"] else "",
                        fk=col["foreign_key"] or "—",
                        comment=col["comment"] or "—",
                    )
                )
            lines.append("")
            if item["unique_constraints"]:
                lines.append("唯一约束：")
                lines.append("")
                lines.append("| 约束名 | 字段 |")
                lines.append("| --- | --- |")
                for uniq in item["unique_constraints"]:
                    lines.append(f"| `{uniq['name']}` | `{', '.join(uniq['columns'])}` |")
                lines.append("")
            if item["indexes"]:
                lines.append("索引：")
                lines.append("")
                lines.append("| 索引名 | 字段 | 唯一 |")
                lines.append("| --- | --- | --- |")
                for idx in item["indexes"]:
                    lines.append(
                        f"| `{idx['name']}` | `{', '.join(idx['columns'])}` | {'是' if idx['unique'] else '否'} |"
                    )
                lines.append("")
            if item["relationships"]:
                lines.append("ORM 关系：")
                lines.append("")
                lines.append("| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |")
                lines.append("| --- | --- | --- | --- | --- | --- |")
                for rel in item["relationships"]:
                    lines.append(
                        f"| `{rel['attribute']}` | {rel['kind']} | `{rel['target']}`（`{rel['target_table']}`） "
                        f"| {rel['lazy']} | {rel['back_populates'] or '—'} | {rel['cascade'] or '—'} |"
                    )
                lines.append("")

    lines.append("## MySQL → PgSQL 分析表血缘（ETL 未实现，仅记录对应关系）")
    lines.append("")
    lines.append("| PgSQL 表 | 来源 | 分区键 |")
    lines.append("| --- | --- | --- |")
    for table, info in ANALYTICS_LINEAGE.items():
        lines.append(f"| `{table}` | {info['来源']} | {info['分区键']} |")
    lines.append("")
    return "\n".join(lines)


def print_orm_mapping() -> None:
    """在控制台打印精简版全局映射（表 -> ORM 类 -> 字段数）。"""
    data = build_orm_mapping()
    section = data["mysql"]
    print(f"MySQL `{section['database']}`：{section['table_count']} 表 / {section['column_count']} 字段")
    for item in section["tables"]:
        print(f"  - {item['table']:<24} -> {item['model']:<22} ({item['column_count']} 字段) {item['module']}")
    section = data["pgsql"]
    print(
        f"PgSQL `{section['database']}`.`{section['schema']}`："
        f"{section['table_count']} 表 / {section['column_count']} 字段"
    )
    for item in section["tables"]:
        partition = f" [{item['partition_by']}]" if item["partition_by"] else ""
        print(f"  - {item['table']:<24} -> {item['model']:<22} ({item['column_count']} 字段){partition}")


__all__ = [
    "ANALYTICS_LINEAGE",
    "build_orm_mapping",
    "print_orm_mapping",
    "render_orm_mapping_markdown",
    "table_model_map",
]
