"""
模块：app/utils/file_utils.py
职责：文件处理工具（哈希、大小、安全文件名、类型校验）。

标签：[架构组] [P1]（上传校验：safe_filename / sha256 / 扩展名白名单）

实现思路：
- 上传文件名一律重命名为 uuid，杜绝路径穿越与覆盖；
- 用 sha256 计算内容哈希，实现秒传与去重；
- 类型校验采用「扩展名白名单 + 内容头校验」双保险（MIME 可伪造）。

待实现清单：
    - [ ] safe_filename(original: str) -> str
    - [ ] get_file_hash(data: bytes | BinaryIO) -> str
    - [ ] human_size(size_bytes: int) -> str
    - [ ] is_allowed_extension(filename, allowed: set[str]) -> bool
    - [ ] ensure_dir(path: str) -> str
"""

from __future__ import annotations

import hashlib
import os
import uuid

from app.config.settings import settings


def safe_filename(original: str) -> str:
    """生成安全的存储文件名：uuid + 原扩展名。"""
    ext = os.path.splitext(original)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def get_file_hash(data: bytes) -> str:
    """计算内容 sha256（秒传 / 去重）。"""
    return hashlib.sha256(data).hexdigest()


def human_size(size_bytes: int) -> str:
    """字节数转可读大小。"""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def is_allowed_extension(filename: str, allowed: set[str] | None = None) -> bool:
    """扩展名白名单校验。"""
    allowed = allowed or settings.allowed_upload_extensions
    return os.path.splitext(filename)[1].lower() in allowed


def ensure_dir(path: str) -> str:
    """确保目录存在并返回该路径。"""
    os.makedirs(path, exist_ok=True)
    return path
