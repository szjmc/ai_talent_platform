"""
模块：app/models/knowledge.py
职责：知识库文档元数据 + 切片元数据（向量本体存 Milvus）。

============================ 实现思路与代码要点 ============================
表设计：
1. knowledge_docs
   - title、category、source_type(file/text)、file_id、status(indexing/indexed/failed)、
     chunk_count、permission_scope(public/dept/role)、allowed_roles(JSON)、
     organization_id、embedding_model、index_error、indexed_at
2. knowledge_chunks
   - doc_id、chunk_index、content、token_count、milvus_pk(向量库主键)、page_no
关键思路：
- 关系库存「元数据 + 切片原文」，Milvus 只存「向量 + milvus_pk」；
  检索时先查 Milvus 拿 pk，再回表取原文，保证数据一致性；
- 权限字段必须落到元数据层，检索时按当前用户角色过滤（RAG 的权限隔离）。
已完成：
- [x] 模型字段与索引（doc_id + chunk_index 唯一）
待实现清单：
- [ ] KnowledgeRepository：list_by_permission、save_chunks、update_index_status
- [ ] KnowledgeService：上传-切片-向量化-入库流程（配合 ai/rag/*）
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class KnowledgeDoc(BaseModel):
    """知识文档（元数据 + 索引状态）。"""

    __tablename__ = "knowledge_docs"
    __table_args__ = {"comment": "知识文档表"}

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="文档标题")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="分类")
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text", server_default=text("'text'"),
        comment="来源类型：file/text",
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
        comment="文件 ID（FK -> files.id，SET NULL，source_type=text 时为空）",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="indexing",
        server_default=text("'indexing'"),
        index=True,
        comment="索引状态：indexing/indexed/failed",
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="切片数量"
    )
    permission_scope: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="public",
        server_default=text("'public'"),
        comment="权限范围：public/dept/role",
    )
    allowed_roles: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="允许访问的角色编码（permission_scope=role 时生效）"
    )
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="部门范围（permission_scope=dept 时生效）"
    )
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="向量模型")
    index_error: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="索引失败原因")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="索引完成时间")

    # ------------------------------------------------------------------ 关系
    file: Mapped["File | None"] = relationship(  # noqa: F821
        "File", back_populates="knowledge_docs", foreign_keys=[file_id], lazy="selectin"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk", back_populates="doc", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeDoc id={self.id} title={self.title} status={self.status}>"


class KnowledgeChunk(BaseModel):
    """知识切片（原文在 MySQL，向量在 Milvus，靠 milvus_pk 关联）。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_knowledge_chunks_doc_index"),
        {"comment": "知识切片表"},
    )

    doc_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="文档 ID（FK -> knowledge_docs.id，CASCADE）",
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="切片序号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片原文")
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="token 数")
    milvus_pk: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="Milvus 主键（回表用）"
    )
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="页码（引用溯源）")

    doc: Mapped["KnowledgeDoc"] = relationship("KnowledgeDoc", back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeChunk id={self.id} doc_id={self.doc_id} index={self.chunk_index}>"
