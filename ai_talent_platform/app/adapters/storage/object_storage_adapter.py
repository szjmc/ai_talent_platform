"""
模块：app/adapters/storage/object_storage_adapter.py
职责：对象存储适配器公共实现（MinIO / 阿里云 OSS / S3 共用一套 CRUD 语义）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
- 统一对象键规划（storage_paths.build_object_key）与上传校验（大小 / bytes）；
- 统一 BaseStorage 语义：save 返回对象键、read/delete/exists/size/url/presigned_url；
- 子类只负责「构造哪个底层客户端」与「对外地址如何拼接」：
      S3StorageAdapter    -> infrastructure/storage/s3_client.S3Client（SigV4）
      MinioStorageAdapter -> 同上，读取 MINIO_* 配置、强制 path-style
      OSSStorageAdapter   -> infrastructure/storage/oss_client.OSSClient（Signature V1）
- 密钥一律不进入 describe() / 日志，避免泄漏。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from app.adapters.storage.base_storage import BaseStorage
from app.adapters.storage.storage_paths import (
    build_object_key,
    guess_content_type,
    normalize_key,
    validate_payload,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)


class ObjectStorageAdapter(BaseStorage):
    """对象存储适配器基类（子类提供 self.client 与访问地址配置）。"""

    backend: str = "object"

    # 子类需在 __init__ 中赋值：底层客户端（S3Client / OSSClient）
    client: Any
    # 公共访问基址（CDN / 反向代理），为空则使用客户端直连地址
    public_url: str = ""

    # ---------------------------------------------------------------- 子类扩展点
    def client_url(self, key: str) -> str:
        """底层客户端生成的对象直链。"""
        return self.client.object_url(key)

    def details(self) -> dict[str, Any]:
        """describe() 的补充字段（不得包含密钥）。"""
        return {}

    # ---------------------------------------------------------------- 接口实现
    async def save(self, data: bytes, filename: str, subdir: str = "", *, before_write=None) -> str:
        payload = validate_payload(data)
        key = build_object_key(filename, subdir)
        if before_write is not None:
            await before_write(key)
        content_type = guess_content_type(filename)
        result = await self.client.put_object(key, payload, content_type)
        logger.info(
            "对象存储写入成功：%s/%s（%s 字节，%s）",
            self.backend,
            key,
            len(payload),
            content_type,
        )
        return str(result.get("key") or key)

    async def read(self, path: str) -> bytes:
        return await self.client.get_object(normalize_key(path))

    async def delete(self, path: str) -> bool:
        return await self.client.delete_object(normalize_key(path))

    async def exists(self, path: str) -> bool:
        info = await self.client.head_object(normalize_key(path))
        return info is not None

    async def size(self, path: str) -> int:
        info = await self.client.head_object(normalize_key(path))
        return int(info.get("size") or 0) if info else 0

    def url(self, path: str) -> str:
        key = normalize_key(path)
        if self.public_url:
            return f"{self.public_url.rstrip('/')}/{quote(key, safe='/-_.~')}"
        return self.client_url(key)

    def presigned_url(self, path: str, expires_seconds: int | None = None) -> str:
        expires = expires_seconds or settings.STORAGE_PRESIGN_EXPIRE_SECONDS
        return self.client.presigned_url(normalize_key(path), expires_seconds=expires)

    async def health(self) -> dict[str, Any]:
        info = await self.client.health()
        # 底层客户端只知道自己说的是哪种协议，这里以适配器后端名为准（minio / oss / s3）
        info["backend"] = self.backend
        info.setdefault("name", self.name)
        info.setdefault("type", self.__class__.__name__)
        return info

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "backend": self.backend,
            "public_url": self.public_url or "",
            "max_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "allowed_extensions": sorted(settings.allowed_upload_extensions),
            **self.details(),
        }


__all__ = ["ObjectStorageAdapter"]
