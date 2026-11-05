"""
模块：app/infrastructure/storage/file_storage.py
职责：文件存储底层能力（目录规划、落盘、读取、删除）。

标签：[架构组] [P1]

============================ 实现思路与代码要点 ============================
职责边界：
    infrastructure/storage/file_storage.py -> 与「本机文件系统」打交道（读写字节、路径规划）
    adapters/storage/local_storage_adapter.py -> 把上述能力包装成统一的存储接口
    services/file_service.py -> 业务逻辑（校验、元数据、权限）

本地存储约定：
    {UPLOAD_DIR}/{biz_type}/{yyyy}/{mm}/{uuid}.{ext}
    - 文件名必须重命名，禁止使用用户原始文件名落盘；
    - 删除采用「逻辑删除 + 延迟物理删除」，避免事务回滚导致文件丢失。
=========================================================================
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from app.config.settings import settings
from app.core.exceptions import FileError
from app.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)


def storage_root() -> Path:
    """存储根目录（settings.UPLOAD_DIR），不存在时自动创建。"""
    root = Path(settings.UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_path(biz_type: str = "common", filename: str | None = None) -> Path:
    """按 {root}/{biz_type}/{yyyy}/{mm}/{safe_name} 规划路径并创建目录。"""
    now = datetime.now()
    safe_biz = _safe_segment(biz_type or "common")
    directory = storage_root() / safe_biz / f"{now.year:04d}" / f"{now.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename) if filename else f"{safe_filename('file')}"
    return directory / name


def safe_resolve(relative_path: str) -> Path:
    """把相对路径解析为绝对路径，并阻断路径穿越（../ 与绝对路径）。"""
    if not relative_path:
        raise FileError("文件路径不能为空")
    root = storage_root()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise FileError(f"非法的文件路径：{relative_path}")
    return candidate


def write_bytes(path: str | Path, data: bytes) -> int:
    """写入字节并返回写入大小。"""
    target = safe_resolve(str(path)) if not os.path.isabs(str(path)) else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)  # 原子替换，避免半截文件
    logger.debug("写入文件：%s（%s 字节）", target, len(data))
    return len(data)


def read_bytes(path: str | Path) -> bytes:
    """读取文件内容；不存在抛 FileError。"""
    target = safe_resolve(str(path)) if not os.path.isabs(str(path)) else Path(path)
    if not target.exists():
        raise FileError(f"文件不存在：{target.name}")
    return target.read_bytes()


def remove(path: str | Path) -> bool:
    """删除文件（不存在返回 False，不抛异常）。"""
    target = safe_resolve(str(path)) if not os.path.isabs(str(path)) else Path(path)
    if not target.exists():
        return False
    target.unlink()
    logger.debug("删除文件：%s", target)
    return True


def get_size(path: str | Path) -> int:
    target = safe_resolve(str(path)) if not os.path.isabs(str(path)) else Path(path)
    return target.stat().st_size if target.exists() else 0


def exists(path: str | Path) -> bool:
    target = safe_resolve(str(path)) if not os.path.isabs(str(path)) else Path(path)
    return target.exists()


def relative_path(path: str | Path) -> str:
    """绝对路径 -> 相对存储根目录的路径（用于入库）。"""
    return str(Path(path).resolve().relative_to(storage_root()))


def prune_empty_dirs(base: str | Path | None = None) -> int:
    """清理空目录（延迟物理删除后调用），返回清理数量。"""
    root = Path(base).resolve() if base else storage_root()
    removed = 0
    for directory in sorted(root.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            removed += 1
    return removed


def usage() -> dict[str, int]:
    """存储用量概览（文件数 / 总字节，供运维巡检）。"""
    root = storage_root()
    total = 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            count += 1
            total += path.stat().st_size
    return {"file_count": count, "total_bytes": total, "root": str(root)}


def _safe_segment(value: str) -> str:
    """目录分段清洗：只保留字母数字与 -_，防目录穿越。"""
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")
    return cleaned or "common"


__all__ = [
    "build_path",
    "exists",
    "get_size",
    "prune_empty_dirs",
    "read_bytes",
    "relative_path",
    "remove",
    "safe_resolve",
    "storage_root",
    "usage",
    "write_bytes",
]
