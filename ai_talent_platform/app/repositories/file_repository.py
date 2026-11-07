"""
模块：app/repositories/file_repository.py
职责：文件数据访问层（files 表）。

标签：[架构组] [P1]（文件元数据）

设计要点：
- 继承 BaseRepository，只补充 files 表特有的查询，不重复实现通用 CRUD；
- 列表页需要「上传人用户名」，这里用一次 JOIN 批量取，避免逐条查 users（N+1）；
- 文件本体在磁盘 / 对象存储，表里只有元数据；
- file_hash 用于秒传与查重（同一 hash + 同一 biz_type 视为同一份业务文件）；
- 孤儿文件清理由定时任务调用，不放在请求链路里。

方法清单（已实现）：
    - [x] get_by_hash(db, file_hash, biz_type)      秒传 / 查重
    - [x] list_with_uploader(db, ...)               列表 + 上传人（单条 SQL）
    - [x] incr_download(db, file_id)                下载计数 +1（原子 UPDATE）
    - [x] sum_size_by_biz(db)                       存储占用统计（SQL 聚合）
    - [x] count_by_biz_type(db)                     业务类型计数（SQL 聚合）
    - [x] list_orphans(db, before)                  孤儿文件（无业务关联 + 超期）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams, build_page_result
from app.core.response import PageResult
from app.models.file import File
from app.models.user import User
from app.repositories.base import BaseRepository


class FileRepository(BaseRepository[File]):
    """文件仓库。"""

    model = File
    filterable_fields: set[str] = {
        "biz_type",
        "biz_id",
        "uploader_id",
        "is_public",
        "storage_type",
    }
    searchable_fields: set[str] = {"file_name", "file_hash"}
    sortable_fields: set[str] = {"id", "file_name", "file_size", "created_at", "updated_at"}
    default_order = "-id"

    # ------------------------------------------------------------ 查询
    async def get_by_hash(
        self, db: AsyncSession, file_hash: str, *, biz_type: str | None = None
    ) -> File | None:
        """按 SHA-256 查已有文件（秒传）。同一摘要不同业务类型不算同一份业务文件。"""
        if not file_hash:
            return None
        stmt = self._base_query().where(File.file_hash == file_hash.lower())
        if biz_type:
            stmt = stmt.where(File.biz_type == biz_type)
        return (await db.execute(stmt.order_by(File.id.asc()).limit(1))).scalar_one_or_none()

    def _list_stmt(
        self,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        content_type: str | None = None,
        organization_uploaders: Sequence[int] | None = None,
    ) -> Select:
        """构造「文件 + 上传人」联表查询（一次 SQL 取齐列表所需字段）。"""
        stmt = (
            select(File, User.username)
            .outerjoin(User, User.id == File.uploader_id)
            .where(File.is_deleted.is_(False))
        )
        stmt = self._apply_filters(stmt, filters)
        stmt = self._apply_keyword(stmt, keyword)
        if content_type:
            # MIME 前缀匹配（如 image/）供前端文件类型筛选，走 content_type 上的前缀条件
            stmt = stmt.where(File.content_type.like(f"{content_type}%"))
        if organization_uploaders is not None:
            ids = list(organization_uploaders)
            stmt = stmt.where(File.uploader_id.in_(ids)) if ids else stmt.where(File.id.is_(None))
        return stmt

    async def list_with_uploader(
        self,
        db: AsyncSession,
        page_params: PageParams,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        content_type: str | None = None,
        organization_uploaders: Sequence[int] | None = None,
    ) -> tuple[PageResult[File], dict[int, str]]:
        """分页查询文件，并批量返回 uploader_id -> username 映射。

        为什么一次返回两个值：调用方需要给每行补 uploader_name，
        如果先分页再逐行查 users 就是 N+1（20 行 = 21 次查询）。
        这里 JOIN 后一次拿齐，用户名直接来自结果集。
        """
        stmt = self._list_stmt(
            filters=filters,
            keyword=keyword,
            content_type=content_type,
            organization_uploaders=organization_uploaders,
        )
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await db.execute(count_stmt)).scalar() or 0)
        if total == 0:
            return build_page_result([], 0, page_params), {}

        stmt = self._apply_order(stmt, page_params.order_by, page_params.order)
        stmt = stmt.limit(page_params.limit).offset(page_params.skip)
        rows = (await db.execute(stmt)).all()
        names = {file.uploader_id: username for file, username in rows if file.uploader_id}
        return build_page_result([file for file, _ in rows], total, page_params), names

    async def incr_download(self, db: AsyncSession, file_id: int) -> None:
        """下载计数 +1（原子 UPDATE，避免读改写竞争）。"""
        await db.execute(
            update(File)
            .where(File.id == file_id)
            .values(download_count=func.coalesce(File.download_count, 0) + 1)
        )

    async def count_by_biz_type(self, db: AsyncSession) -> dict[str, int]:
        """按业务类型计数（SQL 聚合，不在 Python 里循环统计）。"""
        stmt = (
            select(File.biz_type, func.count(File.id))
            .where(File.is_deleted.is_(False))
            .group_by(File.biz_type)
        )
        return {biz: int(count) for biz, count in (await db.execute(stmt)).all()}

    async def sum_size_by_biz(self, db: AsyncSession) -> dict[str, int]:
        """按业务类型统计占用字节数。"""
        stmt = (
            select(File.biz_type, func.coalesce(func.sum(File.file_size), 0))
            .where(File.is_deleted.is_(False))
            .group_by(File.biz_type)
        )
        return {biz: int(size) for biz, size in (await db.execute(stmt)).all()}

    async def total_stats(self, db: AsyncSession) -> tuple[int, int]:
        """文件总数与总占用（一条 SQL 同时取两个聚合值）。"""
        stmt = select(func.count(File.id), func.coalesce(func.sum(File.file_size), 0)).where(
            File.is_deleted.is_(False)
        )
        count, size = (await db.execute(stmt)).one()
        return int(count or 0), int(size or 0)

    async def list_orphans(self, db: AsyncSession, before: datetime, *, limit: int = 500) -> Sequence[File]:
        """孤儿文件（无业务关联且创建时间早于 before），供定时清理任务使用。"""
        stmt = (
            self._base_query()
            .where(File.biz_id.is_(None), File.created_at < before)
            .order_by(File.id.asc())
            .limit(limit)
        )
        return (await db.execute(stmt)).scalars().all()
