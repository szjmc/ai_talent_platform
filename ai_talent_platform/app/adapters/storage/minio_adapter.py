"""
模块：app/adapters/storage/minio_adapter.py
职责：MinIO 对象存储适配器（自建 / 私有化部署首选方案，S3 协议 path-style）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
- MinIO 完全兼容 S3 协议，因此复用 S3StorageAdapter + S3Client（SigV4），
  只覆盖配置来源：MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET /
  MINIO_REGION / MINIO_SECURE / MINIO_PUBLIC_URL；
- MinIO 默认使用 path-style（http://host:9000/bucket/key），不启用虚拟主机风格；
- 注册名 "minio"；STORAGE_BACKEND=minio 即可全局切换，业务代码零改动；
- 部署提示：`docker run -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address :9001`，
  桶不存在时可调用 ensure_bucket()（或业务首次上传前由运维预建）。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.storage.base_storage import storage_registry
from app.adapters.storage.s3_storage_adapter import S3StorageAdapter
from app.config.settings import settings

logger = logging.getLogger(__name__)


@storage_registry.register("minio")
class MinioStorageAdapter(S3StorageAdapter):
    """MinIO 适配器（S3 协议 + path-style + MINIO_* 配置）。"""

    name = "minio"
    backend = "minio"

    def storage_config(self) -> dict[str, Any]:
        return {
            "endpoint": settings.MINIO_ENDPOINT,
            "access_key": settings.MINIO_ACCESS_KEY,
            "secret_key": settings.MINIO_SECRET_KEY,
            "bucket": settings.MINIO_BUCKET,
            "region": settings.MINIO_REGION,
            "secure": settings.MINIO_SECURE,
            "path_style": True,  # MinIO 默认路径风格
            "public_url": settings.MINIO_PUBLIC_URL or settings.STORAGE_PUBLIC_URL,
        }

    def details(self) -> dict[str, Any]:
        return {
            "endpoint": self.client.endpoint,
            "bucket": self.client.bucket,
            "region": self.client.region,
            "console": "MinIO Console 默认 http://127.0.0.1:9001",
        }


__all__ = ["MinioStorageAdapter"]
