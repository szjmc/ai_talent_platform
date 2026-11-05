"""
模块：app/adapters/storage/base_storage.py
职责：统一存储接口（本地 / 对象存储适配器的契约）。

标签：[架构组] [P1]

接口：
    save(data, filename, subdir="") -> str（相对路径 / 对象键）
    read(path) -> bytes
    delete(path) -> bool
    exists(path) -> bool
    url(path) -> str
    size(path) -> int
    presigned_url(path, expires_seconds=None) -> str   # 默认退回 url()，对象存储覆盖为临时签名地址

已内置后端（注册名）：
    local  本地磁盘（开发 / 私有化默认）
    minio  MinIO（S3 协议，path-style）
    oss    阿里云 OSS（Signature V1）
    s3     通用 S3 兼容存储（AWS S3 / 其他网关）

扩展方式：实现同一接口并 @storage_registry.register("xxx")，
业务侧通过 get_storage("xxx") 或 settings.STORAGE_BACKEND 切换，无需改动调用方。
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.config.settings import settings
from app.patterns.registry import Registry

logger = logging.getLogger(__name__)

# 存储适配器注册表（与 llm_registry / skill_registry 同一套机制）
storage_registry: Registry = Registry("storage")

# 注册名 -> 模块路径（延迟导入：未启用对象存储时不会 import 其依赖）
_ADAPTER_MODULES: dict[str, str] = {
    "local": "app.adapters.storage.local_storage_adapter",
    "minio": "app.adapters.storage.minio_adapter",
    "oss": "app.adapters.storage.oss_adapter",
    "s3": "app.adapters.storage.s3_storage_adapter",
}


class BaseStorage(ABC):
    """存储适配器基类。"""

    name: str = "base"

    @abstractmethod
    async def save(self, data: bytes, filename: str, subdir: str = "") -> str:
        """保存文件，返回相对路径 / 对象键（入库用）。"""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """读取文件内容。"""

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """删除文件。"""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """文件是否存在。"""

    @abstractmethod
    async def size(self, path: str) -> int:
        """文件大小（字节）。"""

    @abstractmethod
    def url(self, path: str) -> str:
        """对外访问地址。"""

    def presigned_url(self, path: str, expires_seconds: int | None = None) -> str:
        """临时签名访问地址（本地存储不支持签名，退回普通地址）。"""
        return self.url(path)

    async def health(self) -> dict[str, Any]:
        """后端连通性自检（默认简单返回，对象存储覆盖为真实探测）。"""
        return {"ok": True, "name": self.name, "type": self.__class__.__name__}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.__class__.__name__}


def _ensure_registered(name: str) -> bool:
    """按需导入适配器模块（导入即通过装饰器完成注册）。返回是否已注册。"""
    if name in storage_registry:
        return True
    module = _ADAPTER_MODULES.get(name)
    if not module:
        return False
    try:
        importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - 依赖缺失时才发生
        logger.warning("存储适配器导入失败：%s（%s）", module, exc)
        return False
    return name in storage_registry


def get_storage(name: str | None = None) -> BaseStorage:
    """按名称取存储适配器。

    :param name: local / minio / oss / s3；未指定时取 settings.STORAGE_BACKEND，
                 未知名称回退 local；已知后端导入失败或配置错误则显式报错（不静默降级）
    """
    wanted = str(name or settings.STORAGE_BACKEND or "local").strip().lower()
    registered = _ensure_registered(wanted)

    if not registered:
        if wanted in _ADAPTER_MODULES:
            # 已知后端但导入失败（例如缺少 httpx）：显式报错，避免静默降级到本地导致数据落错位置
            from app.core.exceptions import ExternalServiceError

            raise ExternalServiceError(
                f"存储后端 {wanted} 初始化失败（依赖缺失或模块异常），可用：{storage_registry.keys()}"
            )
        if wanted != "local":
            logger.warning(
                "存储后端 %s 未注册，可用：%s，已回退 local",
                wanted,
                storage_registry.keys(),
            )
        _ensure_registered("local")
        wanted = "local"

    storage_cls = storage_registry.try_get(wanted) or storage_registry.try_get("local")
    if storage_cls is None:  # pragma: no cover - local 模块缺失才会发生
        from app.core.exceptions import ExternalServiceError

        raise ExternalServiceError(
            f"未注册的存储适配器：{wanted}，可用：{storage_registry.keys()}"
        )
    return storage_cls()


def available_backends() -> list[str]:
    """已注册的存储后端清单（供自检 / 文档展示）。"""
    for name in _ADAPTER_MODULES:
        _ensure_registered(name)
    return storage_registry.keys()


__all__ = [
    "BaseStorage",
    "available_backends",
    "get_storage",
    "storage_registry",
]
