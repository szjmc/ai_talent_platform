"""
模块：app/adapters/storage/storage_paths.py
职责：对象存储键规划与上传数据校验（本地 / MinIO / OSS / S3 共用）。

标签：[架构组] [P1]（存储适配器公共工具，禁止业务代码自行拼接对象键）

============================ 实现思路与代码要点 ============================
- 对象键统一为 `{biz_type}/{yyyy}/{mm}/{uuid}{ext}`，与本地存储布局保持一致，
  换成对象存储后历史数据路径语义不变；
- 文件名一律重命名（uuid），禁止使用用户原始文件名，杜绝路径穿越与覆盖；
- 扩展名只保留安全字符并限制长度；内容类型由 mimetypes 推断，供浏览器正确预览；
- 大小校验与 LocalStorageAdapter 保持一致（settings.MAX_UPLOAD_SIZE_MB）。
=========================================================================
"""

from __future__ import annotations

import mimetypes
import os
import re
import uuid
from datetime import datetime

from app.config.settings import settings
from app.core.exceptions import FileError, ParamError

_SAFE_BIZ_RE = re.compile(r"[^0-9a-zA-Z_-]")
_SAFE_EXT_RE = re.compile(r"[^0-9a-z.]")
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def safe_segment(value: str | None, default: str = "common") -> str:
    """目录分段清洗：只保留字母数字与 -_，防目录穿越。"""
    cleaned = _SAFE_BIZ_RE.sub("", str(value or ""))
    return cleaned or default


def safe_extension(filename: str | None) -> str:
    """提取并清洗扩展名（含点，最长 16 字符）。"""
    ext = os.path.splitext(str(filename or ""))[1].lower()
    ext = _SAFE_EXT_RE.sub("", ext)[:16]
    return ext if ext != "." else ""


def build_object_key(
    filename: str | None,
    subdir: str | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """生成对象键：`{biz_type}/{yyyy}/{mm}/{uuid}{ext}`（不含前导斜杠）。"""
    moment = now or datetime.now()
    ext = safe_extension(filename)
    biz_type = safe_segment(subdir or "common")
    return f"{biz_type}/{moment.year:04d}/{moment.month:02d}/{uuid.uuid4().hex}{ext}"


def normalize_key(path: str | None) -> str:
    """对象键归一化：去掉前导斜杠，拒绝路径穿越（.. / .）。"""
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise FileError("文件路径不能为空")
    segments = [segment for segment in raw.split("/") if segment]
    if any(segment in (".", "..") for segment in segments):
        raise FileError(f"非法的文件路径：{path}")
    if not segments:
        raise FileError("文件路径不能为空")
    return "/".join(segments)


def validate_payload(data: bytes | bytearray | None) -> bytes:
    """校验上传内容：必须是 bytes 且不超过 settings.MAX_UPLOAD_SIZE_MB。"""
    if not isinstance(data, (bytes, bytearray)):
        raise ParamError("文件内容必须是 bytes")
    payload = bytes(data)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(payload) > max_bytes:
        raise ParamError(f"文件超过大小上限（{settings.MAX_UPLOAD_SIZE_MB}MB）")
    return payload


def guess_content_type(filename: str | None, default: str = DEFAULT_CONTENT_TYPE) -> str:
    """按文件名推断 MIME 类型。"""
    guessed, _ = mimetypes.guess_type(str(filename or ""))
    return guessed or default


__all__ = [
    "DEFAULT_CONTENT_TYPE",
    "build_object_key",
    "guess_content_type",
    "normalize_key",
    "safe_extension",
    "safe_segment",
    "validate_payload",
]
