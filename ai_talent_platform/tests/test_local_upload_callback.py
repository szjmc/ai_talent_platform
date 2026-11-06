"""本地上传的写入前登记与失败阻断回归测试。
标签：[RAG组] [P1]。
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.adapters.storage.local_storage_adapter import LocalStorageAdapter
from app.config.settings import settings
from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_file_service_local_upload_records_before_write(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    keys = []

    async def before_write(key):
        assert not (tmp_path / key).exists()
        keys.append(key)

    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    record = await FileService(db).upload(
        b"hello", "demo.txt", "text/plain", 1, "general", before_write=before_write
    )
    assert keys == [record.file_path]
    assert record.storage_type == "local"
    assert (tmp_path / record.file_path).read_bytes() == b"hello"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_callback_prevents_local_file_write(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    async def before_write(key):
        raise RuntimeError("intent persistence failed")

    with pytest.raises(RuntimeError, match="intent persistence failed"):
        await LocalStorageAdapter().save(b"hello", "demo.txt", before_write=before_write)
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
