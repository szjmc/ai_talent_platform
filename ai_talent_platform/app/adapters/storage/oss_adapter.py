"""
模块：app/adapters/storage/oss_adapter.py
职责：阿里云 OSS 对象存储适配器（公有云 / 混合云场景）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
- 底层复用 infrastructure/storage/oss_client.OSSClient（Signature V1 + httpx，无需 oss2 SDK）；
- 配置来自 settings.OSS_*：OSS_ENDPOINT / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET /
  OSS_BUCKET / OSS_SECURE / OSS_PUBLIC_URL；
- 采用 virtual-hosted 风格：https://{bucket}.{endpoint}/{key}；
- 注册名 "oss"；STORAGE_BACKEND=oss 即可全局切换，业务代码零改动；
- 对象键与本地 / MinIO 同构（{biz_type}/{yyyy}/{mm}/{uuid}.{ext}），便于跨后端迁移；
- 密钥缺失时在构造阶段抛 ExternalServiceError（启动自检可提前暴露配置问题）。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.storage.base_storage import storage_registry
from app.adapters.storage.object_storage_adapter import ObjectStorageAdapter
from app.config.settings import settings
from app.infrastructure.storage.oss_client import OSSClient

logger = logging.getLogger(__name__)


@storage_registry.register("oss")
class OSSStorageAdapter(ObjectStorageAdapter):
    """阿里云 OSS 适配器。"""

    name = "oss"
    backend = "oss"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
        public_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.public_url = str(
            public_url if public_url is not None else (settings.OSS_PUBLIC_URL or settings.STORAGE_PUBLIC_URL)
        ).rstrip("/")
        self.client = OSSClient(
            endpoint=endpoint if endpoint is not None else settings.OSS_ENDPOINT,
            access_key_id=(
                access_key_id if access_key_id is not None else settings.OSS_ACCESS_KEY_ID
            ),
            access_key_secret=(
                access_key_secret
                if access_key_secret is not None
                else settings.OSS_ACCESS_KEY_SECRET
            ),
            bucket=bucket if bucket is not None else settings.OSS_BUCKET,
            secure=secure if secure is not None else settings.OSS_SECURE,
            timeout=timeout if timeout is not None else settings.STORAGE_TIMEOUT,
            transport=transport,
        )

    def details(self) -> dict[str, Any]:
        return {
            "endpoint": self.client.endpoint,
            "bucket": self.client.bucket,
            "scheme": self.client.scheme,
        }


__all__ = ["OSSStorageAdapter"]
