"""
模块：app/adapters/storage/s3_storage_adapter.py
职责：通用 S3 兼容存储适配器（AWS S3 / 其他 S3 网关；也可直接配置成 MinIO）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
- 底层复用 infrastructure/storage/s3_client.S3Client（SigV4 签名 + httpx）；
- 配置来自 settings.S3_*：S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET /
  S3_REGION / S3_SECURE / S3_PATH_STYLE；
- 注册名 "s3"；业务侧 get_storage("s3") 或 STORAGE_BACKEND=s3 切换；
- 对象键与本地存储同构（{biz_type}/{yyyy}/{mm}/{uuid}.{ext}），历史数据语义不变。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.storage.base_storage import storage_registry
from app.adapters.storage.object_storage_adapter import ObjectStorageAdapter
from app.config.settings import settings
from app.infrastructure.storage.s3_client import S3Client

logger = logging.getLogger(__name__)


@storage_registry.register("s3")
class S3StorageAdapter(ObjectStorageAdapter):
    """S3 兼容对象存储适配器。"""

    name = "s3"
    backend = "s3"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        region: str | None = None,
        secure: bool | None = None,
        path_style: bool | None = None,
        public_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        conf = self.storage_config()
        self.public_url = str(
            public_url if public_url is not None else conf.get("public_url", "")
        ).rstrip("/")
        self.client = S3Client(
            endpoint=endpoint if endpoint is not None else conf["endpoint"],
            access_key=access_key if access_key is not None else conf["access_key"],
            secret_key=secret_key if secret_key is not None else conf["secret_key"],
            bucket=bucket if bucket is not None else conf["bucket"],
            region=region if region is not None else conf["region"],
            secure=secure if secure is not None else conf["secure"],
            path_style=path_style if path_style is not None else conf["path_style"],
            timeout=timeout if timeout is not None else settings.STORAGE_TIMEOUT,
            transport=transport,
        )

    # ---------------------------------------------------------------- 配置
    def storage_config(self) -> dict[str, Any]:
        """S3 后端配置（子类可覆盖为其他配置项）。"""
        return {
            "endpoint": settings.S3_ENDPOINT,
            "access_key": settings.S3_ACCESS_KEY,
            "secret_key": settings.S3_SECRET_KEY,
            "bucket": settings.S3_BUCKET,
            "region": settings.S3_REGION,
            "secure": settings.S3_SECURE,
            "path_style": settings.S3_PATH_STYLE,
            "public_url": settings.S3_PUBLIC_URL or settings.STORAGE_PUBLIC_URL,
        }

    # ---------------------------------------------------------------- 扩展
    async def ensure_bucket(self) -> bool:
        """确保存储桶存在（部署初始化 / 自检脚本使用）。"""
        info = await self.client.bucket_info()
        if info.get("ok"):
            return True
        return await self.client.create_bucket()

    def details(self) -> dict[str, Any]:
        return {
            "endpoint": self.client.endpoint,
            "bucket": self.client.bucket,
            "region": self.client.region,
            "path_style": self.client.path_style,
        }


__all__ = ["S3StorageAdapter"]
