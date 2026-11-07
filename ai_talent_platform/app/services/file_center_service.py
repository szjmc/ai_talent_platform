"""
模块：app/services/file_center_service.py
职责：文件中心业务服务（上传 / 列表 / 详情 / 下载 / 删除 / 批量 / 统计）。

标签：[架构组] [P1]（文件中心：元数据 + 存储 + 鉴权，接口 /files 的唯一业务实现）

为什么不复用 app/services/file_service.py：
    那个 FileService 是「简历场景」的服务（save_resume / read_original / upload 给知识库用），
    扩展名白名单、哈希校验都围绕简历与知识库，并且直接操作新会话外的对象；
    文件中心需要的是通用能力（业务类型白名单、秒传、归属鉴权、批量失败明细、统计）。
    两者共用同一套底层：BaseStorage 适配器 + files 表 + FileRepository。

关键约定：
1. 文件名重命名（uuid）落盘，防止路径穿越与覆盖；对外只返回 download_url，不暴露对象键；
2. 秒传：同一 biz_type 下 SHA-256 相同 → 直接返回已有记录（duplicated=True），不再落盘；
3. 鉴权：私有文件只允许「上传人 / 管理员 / 同业务关联可见者」下载，其余 403；
4. 删除是软删元数据，物理对象由定时任务清理（不在请求链路里删对象，避免不可回滚）；
5. 批量上传逐份校验，单份失败只记录原因，不中断其余文件。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.base_storage import get_storage
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, FileError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.file import File
from app.models.user import User
from app.repositories.file_repository import FileRepository
from app.schemas.file import (
    FileBatchUploadItem,
    FileBatchUploadResponse,
    FileResponse,
    FileStatsResponse,
)

logger = logging.getLogger(__name__)

# 业务类型白名单（与 app/schemas/file.py 的 FileBizType 保持一致）
BIZ_TYPES: set[str] = {"resume", "knowledge", "avatar", "export", "interview", "other"}

# 通用文件中心允许的扩展名（与「简历上传」的严格白名单分开：这里是平台级存储）
GENERIC_EXTENSIONS: set[str] = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".zip", ".rar", ".7z", ".mp4", ".mp3", ".wav",
}

# 可直接在浏览器内联预览的类型（预览地址给前端 iframe / img 用）
INLINE_PREVIEW_TYPES = ("image/", "application/pdf", "text/")

_SAFE_NAME_RE = re.compile(r"[\x00-\x1f<>:\"/\\|?*]")


def _download_url(file_id: int) -> str:
    """对外下载地址（与路由注册前缀保持一致，前端不再自己拼）。"""
    return f"{settings.API_PREFIX}/files/{file_id}/download"


class FileCenterService:
    """文件中心服务。"""

    def __init__(self, db: AsyncSession, repo: FileRepository | None = None) -> None:
        self.db = db
        self.repo = repo or FileRepository()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _clean_name(filename: str | None) -> str:
        """清洗原始文件名（去路径、去控制字符、限长），仅用于展示与扩展名判断。"""
        name = Path(str(filename or "file")).name.replace("\\", "_")
        name = _SAFE_NAME_RE.sub("_", name).strip() or "file"
        return name[:255]

    @staticmethod
    def _validate(biz_type: str, name: str, content: bytes) -> str:
        """校验业务类型 / 大小 / 扩展名，返回小写扩展名（含点）。"""
        if biz_type not in BIZ_TYPES:
            raise BusinessRuleError(f"不支持的业务类型：{biz_type}（可选：{'/'.join(sorted(BIZ_TYPES))}）")
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if not content:
            raise FileError("文件内容为空")
        if len(content) > max_bytes:
            raise FileError(f"文件超过大小限制（{settings.MAX_UPLOAD_SIZE_MB}MB）")
        extension = Path(name).suffix.lower()
        if extension not in GENERIC_EXTENSIONS:
            raise FileError(f"不支持的文件类型：{extension or '无扩展名'}")
        return extension

    def _to_response(
        self, record: File, uploader_name: str | None = None, *, duplicated: bool = False
    ) -> FileResponse:
        """ORM -> 对外视图（补下载地址；不暴露 file_path / stored_name 等内部字段）。"""
        content_type = record.content_type or ""
        return FileResponse(
            id=record.id,
            file_name=record.file_name,
            file_size=record.file_size or 0,
            content_type=record.content_type,
            extension=record.extension,
            file_hash=record.file_hash,
            storage_type=record.storage_type,
            biz_type=record.biz_type,
            biz_id=record.biz_id,
            uploader_id=record.uploader_id,
            uploader_name=uploader_name,
            is_public=bool(record.is_public),
            download_count=record.download_count or 0,
            download_url=_download_url(record.id),
            preview_url=_download_url(record.id) if content_type.startswith(INLINE_PREVIEW_TYPES) else None,
            duplicated=duplicated,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _can_access(record: File, user: User | None) -> bool:
        """下载 / 删除鉴权：公开文件人人可读；私有文件仅本人与管理员；删除只允许本人与管理员。"""
        if user is None:
            return False
        if user.is_superuser or "admin" in (user.role_codes or []):
            return True
        return record.uploader_id == user.id

    # ---------------------------------------------------------------- 上传
    async def upload(
        self,
        *,
        filename: str | None,
        content: bytes,
        content_type: str | None,
        biz_type: str | None,
        biz_id: int | None = None,
        is_public: bool = False,
        user: User | None = None,
        subdir: str | None = None,
    ) -> FileResponse:
        """上传单个文件（含秒传）。"""
        name = self._clean_name(filename)
        biz = (biz_type or "other").strip()
        extension = self._validate(biz, name, content)
        digest = hashlib.sha256(content).hexdigest()

        # 1) 秒传：同一业务类型下摘要相同直接复用记录，不再落盘
        existing = await self.repo.get_by_hash(self.db, digest, biz_type=biz)
        if existing is not None:
            return self._to_response(existing, duplicated=True)

        # 2) 落盘（uuid 名，防覆盖 / 路径穿越）
        storage = get_storage()
        stored_name = f"{uuid4().hex}{extension}"
        folder = subdir or f"{biz}/{datetime.now():%Y/%m}"
        path = await storage.save(content, stored_name, subdir=folder)

        # 3) 元数据入库；失败则回收已写入的对象（避免孤儿对象）
        record = File(
            file_name=name,
            stored_name=stored_name,
            file_path=path,
            file_size=len(content),
            content_type=content_type or "application/octet-stream",
            extension=extension,
            file_hash=digest,
            storage_type=storage.name,
            biz_type=biz,
            biz_id=biz_id,
            uploader_id=user.id if user else None,
            is_public=is_public,
            created_by=user.id if user else None,
        )
        try:
            await self.repo.create(self.db, record)
        except Exception:
            try:
                await storage.delete(path)
            except Exception:  # noqa: BLE001 - 回收失败不能掩盖原始错误
                logger.warning("上传失败后回收对象失败：%s", path, exc_info=True)
            raise
        return self._to_response(record, getattr(user, "username", None))

    async def batch_upload(
        self,
        items: Sequence[tuple[str | None, bytes, str | None]],
        *,
        biz_type: str | None,
        biz_id: int | None = None,
        is_public: bool = False,
        user: User | None = None,
    ) -> FileBatchUploadResponse:
        """批量上传：逐份处理，单份失败不影响其余（返回成功 / 失败明细）。"""
        results: list[FileBatchUploadItem] = []
        for filename, content, content_type in items:
            try:
                data = await self.upload(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    biz_type=biz_type,
                    biz_id=biz_id,
                    is_public=is_public,
                    user=user,
                )
                results.append(FileBatchUploadItem(file_name=data.file_name, ok=True, file=data))
            except (FileError, BusinessRuleError) as exc:
                results.append(
                    FileBatchUploadItem(
                        file_name=self._clean_name(filename), ok=False,
                        error=getattr(exc, "message", str(exc)), code=getattr(exc, "code", None),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 单份异常也要落明细，不中断整批
                logger.exception("批量上传单份失败：%s", filename)
                results.append(
                    FileBatchUploadItem(file_name=self._clean_name(filename), ok=False, error=str(exc))
                )
        success = sum(1 for item in results if item.ok)
        return FileBatchUploadResponse(
            total=len(results), success_count=success, failed_count=len(results) - success, items=results
        )

    # ---------------------------------------------------------------- 查询
    async def page_list(
        self,
        page: PageParams,
        *,
        biz_type: str | None = None,
        biz_id: int | None = None,
        uploader_id: int | None = None,
        content_type: str | None = None,
        keyword: str | None = None,
        user: User | None = None,
    ) -> PageResult[FileResponse]:
        """文件列表（分页）。

        数据范围：管理员看全部；普通用户只看「本人上传 + 公开文件」，
        通过 uploader 过滤实现，避免前端拿别人的私有文件元数据。
        """
        filters: dict[str, Any] = {"biz_type": biz_type, "biz_id": biz_id, "uploader_id": uploader_id}
        is_admin = bool(user and (user.is_superuser or "admin" in (user.role_codes or [])))
        uploaders: Sequence[int] | None = None
        if user is not None and not is_admin:
            filters["uploader_id"] = user.id
        result, names = await self.repo.list_with_uploader(
            self.db,
            page,
            filters=filters,
            keyword=keyword,
            content_type=content_type,
            organization_uploaders=uploaders,
        )
        items = [
            self._to_response(record, names.get(record.uploader_id or 0))
            for record in result.items
        ]
        return PageResult[FileResponse](
            items=items, total=result.total, page=result.page,
            page_size=result.page_size, pages=result.pages,
        )

    async def get(self, file_id: int, *, user: User | None = None) -> FileResponse:
        """文件详情（私有文件仅本人 / 管理员可见）。"""
        record = await self.repo.get_by_id(self.db, file_id)
        if record is None:
            raise NotFoundError("文件不存在")
        if not record.is_public and not self._can_access(record, user):
            raise PermissionDeniedError("无权查看该文件")
        return self._to_response(record, getattr(user, "username", None))

    async def stats(self, *, user: User | None = None) -> FileStatsResponse:
        """文件中心概览（总数 / 总占用 / 分类计数）。"""
        is_admin = bool(user and (user.is_superuser or "admin" in (user.role_codes or [])))
        if is_admin or user is None:
            total, size = await self.repo.total_stats(self.db)
            by_biz = await self.repo.count_by_biz_type(self.db)
        else:
            # 普通用户只看本人可见范围：复用列表查询的计数（不做全表聚合）
            page = PageParams()
            result, _ = await self.repo.list_with_uploader(self.db, page, filters={"uploader_id": user.id})
            total = result.total
            records = await self.repo.list(self.db, filters={"uploader_id": user.id})
            size = sum(int(item.file_size or 0) for item in records)
            by_biz: dict[str, int] = {}
            for item in records:
                by_biz[item.biz_type] = by_biz.get(item.biz_type, 0) + 1
        return FileStatsResponse(total_files=total, total_size=size, by_biz_type=by_biz)

    # ---------------------------------------------------------------- 下载 / 删除
    async def prepare_download(self, file_id: int, *, user: User | None = None) -> tuple[File, bytes]:
        """校验权限并读取文件内容（Router 负责包装成 FileResponse 流）。"""
        record = await self.repo.get_by_id(self.db, file_id)
        if record is None:
            raise NotFoundError("文件不存在")
        if not record.is_public and not self._can_access(record, user):
            raise PermissionDeniedError("无权下载该文件")
        storage = get_storage(record.storage_type)
        try:
            content = await storage.read(record.file_path)
        except FileNotFoundError as exc:
            raise NotFoundError("文件对象已丢失（请重新上传）") from exc
        await self.repo.incr_download(self.db, record.id)
        return record, content

    async def delete(self, file_id: int, *, user: User | None = None, hard: bool = False) -> None:
        """删除文件（默认软删元数据；hard=True 同时删除存储对象，供治理脚本使用）。"""
        record = await self.repo.get_by_id(self.db, file_id)
        if record is None:
            raise NotFoundError("文件不存在")
        if not self._can_access(record, user):
            raise PermissionDeniedError("无权删除该文件")
        await self.repo.delete(self.db, record, soft=not hard)
        if hard:
            try:
                await get_storage(record.storage_type).delete(record.file_path)
            except Exception:  # noqa: BLE001 - 对象已不存在不应导致接口 500
                logger.warning("物理删除对象失败（忽略）：%s", record.file_path, exc_info=True)

    async def cleanup_orphans(self, *, older_than_days: int = 30, limit: int = 500) -> dict[str, int]:
        """清理孤儿文件（无 biz_id 且超期）：软删元数据 + 删除对象。定时任务入口。"""
        before = datetime.now() - timedelta(days=max(1, older_than_days))
        records = await self.repo.list_orphans(self.db, before, limit=limit)
        deleted = 0
        for record in records:
            try:
                await get_storage(record.storage_type).delete(record.file_path)
            except Exception:  # noqa: BLE001
                logger.warning("孤儿清理：删除对象失败 %s", record.file_path, exc_info=True)
            await self.repo.delete(self.db, record, soft=True)
            deleted += 1
        return {"scanned": len(records), "deleted": deleted}
