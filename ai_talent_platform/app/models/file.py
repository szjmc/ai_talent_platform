"""
模块：app/models/file.py
职责：文件记录表（统一管理上传文件元数据）。

标签：[业务组] [P1]（文件元数据表）

============================ 实现思路与代码要点 ============================
表设计：files
- file_name(原始名)、stored_name(磁盘名/UUID)、file_path、file_size、content_type、
  extension、file_hash(sha256，秒传与去重)、storage_type(local/oss)、
  biz_type(resume/knowledge/avatar/export)、biz_id(关联业务 ID)、
  uploader_id、is_public、download_count
关键思路：
- 文件本体存磁盘（adapters/storage/local_storage_adapter.py），表里只存元数据；
- file_hash 唯一（可选）实现秒传：相同文件不重复落盘；
- 下载接口必须校验权限（biz_type + 归属人 + 角色）。
已完成：
- [x] 模型字段与索引（file_hash、biz_type + biz_id）
待实现清单：
- [ ] FileRepository：get_by_hash、list_by_biz、incr_download
- [ ] FileService：保存、校验、下载鉴权、清理孤儿文件
=========================================================================
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class File(BaseModel):
    """文件元数据（文件本体存磁盘/对象存储）。"""

    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_biz_type_biz_id", "biz_type", "biz_id"),
        {"comment": "文件元数据表"},
    )

    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    stored_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="存储名（uuid + 扩展名）")
    file_path: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="相对路径：{biz_type}/{yyyy}/{mm}/xxx"
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="字节数"
    )
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="MIME 类型")
    extension: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="扩展名（含点）")
    file_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="sha256，秒传与查重"
    )
    storage_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default=text("'local'"),
        comment="存储方式：local/oss",
    )
    biz_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="业务类型：resume/knowledge/avatar/export"
    )
    biz_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="关联业务 ID（多态）")
    uploader_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="上传人（FK -> users.id，SET NULL）",
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"), comment="是否公开访问"
    )
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="下载次数"
    )

    # ------------------------------------------------------------------ 关系
    uploader: Mapped["User | None"] = relationship("User", lazy="selectin")  # noqa: F821
    resumes: Mapped[list["Resume"]] = relationship(  # noqa: F821
        "Resume", back_populates="file", foreign_keys="Resume.file_id", lazy="select"
    )
    knowledge_docs: Mapped[list["KnowledgeDoc"]] = relationship(  # noqa: F821
        "KnowledgeDoc", back_populates="file", foreign_keys="KnowledgeDoc.file_id", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<File id={self.id} file_name={self.file_name}>"
