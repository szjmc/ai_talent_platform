"""
模块：app/adapters/storage/local_storage_adapter.py
职责：本地文件存储适配器（开发 / 私有化部署默认方案）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
统一存储接口（app/adapters/storage/base_storage.py 的 BaseStorage）：
    save(data: bytes, filename: str, subdir: str = "") -> str   返回相对路径
    read(path: str) -> bytes
    delete(path: str) -> bool
    exists(path: str) -> bool
    url(path: str) -> str                                      对外访问地址
实现要点：
- 落盘目录来自 settings.UPLOAD_DIR，按 `biz_type/yyyy/mm/` 分目录，避免单目录文件过多；
- 存储文件名 = uuid + 扩展名（utils/file_utils.safe_filename），防路径穿越；
- 删除采用「先标记后清理」，避免事务回滚后文件已丢；
- 对象存储（MinIO / 阿里云 OSS / S3）已提供同接口实现，见 minio_adapter.py / oss_adapter.py。
=========================================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.adapters.storage.base_storage import BaseStorage, storage_registry
from app.config.settings import settings
from app.infrastructure.storage import file_storage

logger = logging.getLogger(__name__)


@storage_registry.register("local")
class LocalStorageAdapter(BaseStorage):
    """本地磁盘存储适配器。"""

    name = "local"

    async def save(self, data: bytes, filename: str, subdir: str = "", *, before_write=None) -> str:
        """写入文件，返回相对存储根目录的路径。"""
        if not isinstance(data, (bytes, bytearray)):
            from app.core.exceptions import ParamError

            raise ParamError("文件内容必须是 bytes")
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if len(data) > max_bytes:
            from app.core.exceptions import ParamError

            raise ParamError(f"文件超过大小上限（{settings.MAX_UPLOAD_SIZE_MB}MB）")

        target = file_storage.build_path(subdir or "common", filename)
        relative = file_storage.relative_path(target)
        if before_write is not None:
            await before_write(relative)
        file_storage.write_bytes(target, bytes(data))
        logger.info("文件保存成功：%s（%s 字节）", relative, len(data))
        return relative

    async def read(self, path: str) -> bytes:
        return file_storage.read_bytes(path)

    async def delete(self, path: str) -> bool:
        """物理删除（业务侧请先做元数据逻辑删除，再延迟调用本方法）。"""
        return file_storage.remove(path)

    async def exists(self, path: str) -> bool:
        return file_storage.exists(path)

    async def size(self, path: str) -> int:
        return file_storage.get_size(path)

    def url(self, path: str) -> str:
        """对外访问地址（下载路由待实现：当前由 /files 占位提供，后续落 routers/file_router.py）。"""
        return f"{settings.API_PREFIX}/files/raw/{str(path).lstrip('/')}"

    def absolute_path(self, path: str) -> Path:
        """相对路径 -> 绝对路径（带路径穿越防护）。"""
        return file_storage.safe_resolve(path)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "root": str(file_storage.storage_root()),
            "max_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "allowed_extensions": sorted(settings.allowed_upload_extensions),
        }


__all__ = ["LocalStorageAdapter"]
