"""
模块：app/repositories/knowledge_repository.py
职责：知识库数据访问层。

标签：[RAG组] [P0]（list_by_permission / save_chunks / update_index_status，被 knowledge_service 依赖）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 向量检索：Milvus 返回 chunk 主键 -> search_chunks_by_ids 回表取原文；
- 权限过滤必须在 SQL 层生效（allowed_roles JSON 包含判断）；
- 删除文档时同步删除 Milvus 向量（Service 编排，仓库只删元数据）。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] list_by_permission(db, roles, org_id)         按角色/部门过滤可访问文档
    - [ ] save_chunks(db, chunks)                        批量保存切片
    - [ ] get_chunks_by_doc(db, doc_id)                  文档切片列表
    - [ ] update_index_status(db, doc_id, status, error) 索引状态机
    - [ ] search_chunks_by_ids(db, chunk_ids)            按 Milvus 召回 id 回表

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
=========================================================================
"""

from __future__ import annotations

from app.models.base import Base
from app.repositories.base import BaseRepository

# 模型实现后取消注释：
# from app.models.knowledge import KnowledgeDoc

# TODO(负责人)：按上述方法清单实现。


class KnowledgeRepository(BaseRepository):
    """知识库仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
