# 版本记录关联原件、正文、预览和发布清单。业务版本序号与并发控制用的 version 各自独立。
"""知识文档版本：只新增本表，复用现有文档、文件与用户。
标签：[RAG组] [P0]
"""
from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, ForeignKey, UniqueConstraint, Computed, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import BaseModel


class KnowledgeRevision(BaseModel):
    """每条记录对应一个草稿/历史版本；version仍用于并发控制。"""
    __tablename__ = 'knowledge_revisions'
    __table_args__ = (
        UniqueConstraint('doc_id', 'revision_no', name='uq_knowledge_revision_number'),
        UniqueConstraint('doc_id', 'active_marker', name='uq_knowledge_revision_active'),
        CheckConstraint('revision_no > 0', name='knowledge_revision_positive'),
        CheckConstraint("status in ('draft','previewed','publishing','published','archived','failed')", name='knowledge_revision_status'),
        {'comment': '知识文档草稿及历史版本，复用原有文件表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )
    doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('knowledge_docs.id', ondelete='CASCADE'), index=True, comment='稳定业务文档ID')
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, comment='业务版本号，与乐观锁version不同')
    parent_revision_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('knowledge_revisions.id', ondelete='SET NULL'), comment='来源版本；Service必须校验属于同一文档')
    source_file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('files.id', ondelete='SET NULL'), comment='上传原件')
    file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('files.id', ondelete='SET NULL'), comment='该版本下载文件；修改后生成Word')
    body_file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('files.id', ondelete='SET NULL'), comment='完整正文文件，复用files与MinIO')
    preview_file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('files.id', ondelete='SET NULL'), comment='切片预览及来源快照文件')
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment='版本标题快照')
    category: Mapped[str] = mapped_column(String(64), nullable=False, comment='版本分类快照')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='draft', server_default='draft', index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), comment='正文SHA256')
    preview_hash: Mapped[str | None] = mapped_column(String(64), comment='正文及分块配置摘要；编辑后必须失效')
    index_manifest: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('files.id', ondelete='SET NULL'), comment='索引发布清单文件：切片ID、集合、模型及配置')
    published_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='SET NULL'))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(String(512), comment='发布失败原因，不保存密钥或原始个人数据')
    # 联合唯一键(doc_id,1)限制单个生效版本，草稿为NULL。
    # 生成表达式不引用外键doc_id，避免MySQL对生成列基础字段CASCADE的限制。
    active_marker: Mapped[int | None] = mapped_column(Integer, Computed("CASE WHEN status = 'published' AND is_deleted = 0 THEN 1 ELSE NULL END", persisted=True))

    # 关联只定义在新模型，不给原表增加列或反向关系。
    document = relationship('KnowledgeDoc', foreign_keys=[doc_id])
    parent = relationship('KnowledgeRevision', remote_side='KnowledgeRevision.id', foreign_keys=[parent_revision_id])
    source_file = relationship('File', foreign_keys=[source_file_id])
    revision_file = relationship('File', foreign_keys=[file_id])
    body_file = relationship('File', foreign_keys=[body_file_id])
    preview_file = relationship('File', foreign_keys=[preview_file_id])
    manifest_file = relationship('File', foreign_keys=[index_manifest])
    publisher = relationship('User', foreign_keys=[published_by])
