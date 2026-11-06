"""
模块：app/services/report_store.py
职责：报告文件落盘与元数据（同名 .json）读写，供报告列表 / 下载使用。

标签：[Vanna组] [P0]（报告列表 / 下载的持久化层）

============================ 实现思路与代码要点 ============================
1. 目录约定：{UPLOAD_DIR}/report/{yyyy}/{mm}/{report_id}.{pdf|docx}，
   同名 .json 保存元数据（标题、格式、大小、生成人、时间、数据范围）；
2. report_id 为 32 位十六进制，读写前必须过正则校验，杜绝路径穿越；
3. 没有新增数据库表：元数据与文件放一起，列表用目录扫描实现——
   适合当前单机 / 私有化部署；将来要多实例或大数据量，再换成数据库表；
4. 所有 IO 走 infrastructure/storage/file_storage，复用其原子写与路径校验。
=========================================================================
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from app.core.exceptions import NotFoundError
from app.infrastructure.storage import file_storage

# 报告在 UPLOAD_DIR 下的子目录
_REPORT_BIZ = "report"
# 报告 ID 只允许 32 位小写十六进制，避免 ../ 之类的路径穿越
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def new_report_id() -> str:
    """生成新的报告 ID。"""
    return uuid.uuid4().hex


def save_report(data: bytes, fmt: str, meta: dict) -> dict:
    """保存报告文件与元数据，返回补全后的元数据（含 path / size）。"""
    report_id = meta["report_id"]
    if not _ID_PATTERN.match(report_id):
        raise NotFoundError("报告 ID 不合法")
    path = _file_path(report_id, fmt)
    file_storage.write_bytes(path, data)
    meta = dict(meta)
    meta["path"] = file_storage.relative_path(path)
    meta["size"] = len(data)
    file_storage.write_bytes(_meta_path(report_id, fmt),
                             json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    return meta


def load_meta(report_id: str) -> dict:
    """按报告 ID 读取元数据；文件或元数据缺失时抛 NotFoundError。"""
    if not _ID_PATTERN.match(report_id or ""):
        raise NotFoundError("报告不存在")
    for meta_path in _report_root().rglob(f"{report_id}.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("report_id") == report_id and meta.get("path"):
            return meta
    raise NotFoundError("报告不存在或已被清理")


def list_metas(user_id: int | None = None) -> list[dict]:
    """列出报告元数据（按生成时间倒序）；user_id 为空表示不过滤。"""
    root = _report_root()
    if not root.exists():
        return []
    metas: list[dict] = []
    for meta_path in root.rglob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not meta.get("report_id") or not meta.get("path"):
            continue
        if user_id is not None and meta.get("user_id") != user_id:
            continue
        metas.append(meta)
    metas.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return metas


def report_file(meta: dict) -> Path:
    """把元数据里的相对路径还原成绝对路径，并校验仍然位于存储根目录内。"""
    return file_storage.safe_resolve(meta["path"])


def build_meta(*, report_id: str, title: str, fmt: str, question: str, user,
               scope: str, author: str) -> dict:
    """组装元数据字典；时间统一用 ISO 字符串，方便排序与序列化。"""
    return {
        "report_id": report_id,
        "title": title,
        "format": fmt,
        "question": question,
        "user_id": getattr(user, "id", None),
        "organization_id": getattr(user, "organization_id", None),
        "author": author,
        "scope": scope,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _report_root() -> Path:
    """报告根目录（不存在时创建）。"""
    root = file_storage.storage_root() / _REPORT_BIZ
    root.mkdir(parents=True, exist_ok=True)
    return root


def _file_path(report_id: str, fmt: str) -> Path:
    """报告文件路径：report/yyyy/mm/{id}.{fmt}。"""
    now = datetime.now()
    directory = _report_root() / f"{now.year:04d}" / f"{now.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{report_id}.{fmt}"


def _meta_path(report_id: str, fmt: str) -> Path:
    """元数据与报告文件同目录同名，仅扩展名不同。"""
    return _file_path(report_id, fmt).with_suffix(".json")


__all__ = [
    "build_meta",
    "list_metas",
    "load_meta",
    "new_report_id",
    "report_file",
    "save_report",
]
