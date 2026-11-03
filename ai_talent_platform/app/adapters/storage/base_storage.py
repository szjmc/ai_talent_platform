"""
模块：app/adapters/storage/base_storage.py
职责：统一存储接口（本地 / 对象存储适配器的契约）。

标签：[架构组] [P1]

接口：
    save(data, filename, subdir="") -> str（相对路径）
    read(path) -> bytes
    delete(path) -> bool
    exists(path) -> bool
    url(path) -> str
    size(path) -> int

接入对象存储（OSS / MinIO / S3）时：实现同一接口并 @storage_registry.register("oss") 即可，
业务侧通过 get_storage(name) 取用，无需改动调用方。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.patterns.registry import Registry

# 存储适配器注册表（与 llm_registry / skill_registry 同一套机制）
storage_registry: Registry = Registry("storage")


class BaseStorage(ABC):
    """存储适配器基类。"""

    name: str = "base"

    @abstractmethod
    async def save(self, data: bytes, filename: str, subdir: str = "") -> str:
        """保存文件，返回相对路径（入库用）。"""

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

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.__class__.__name__}


def get_storage(name: str | None = None) -> BaseStorage:
    """按名称取存储适配器；未指定时用默认 local（导入即注册）。"""
    if not storage_registry.keys():
        import app.adapters.storage.local_storage_adapter  # noqa: F401

    wanted = name or "local"
    storage_cls = storage_registry.try_get(wanted) or storage_registry.try_get("local")
    if storage_cls is None:
        from app.core.exceptions import ExternalServiceError

        raise ExternalServiceError(f"未注册的存储适配器：{wanted}，可用：{storage_registry.keys()}")
    return storage_cls()


__all__ = ["BaseStorage", "get_storage", "storage_registry"]
