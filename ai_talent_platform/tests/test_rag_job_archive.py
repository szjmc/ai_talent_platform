"""已完成补偿任务归档、失败重试与扫描收敛回归。
标签：[RAG组] [P1]。
"""
import json
from unittest.mock import AsyncMock

import pytest

from app.repositories import rag_ingest_job as jobs
from app.services import rag_recovery as recovery


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.client = self

    async def put_object(self, key, data, content_type):
        self.objects[key] = data

    async def delete(self, key):
        self.objects.pop(key, None)
        return True

    async def list_keys(self, prefix):
        return [key for key in self.objects if key.startswith(prefix)]

    async def read(self, key):
        self.reads.append(key)
        return self.objects[key]


@pytest.mark.asyncio
async def test_done_job_is_archived_and_not_scanned_again(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr(jobs, 'get_storage', lambda *_: storage)
    monkeypatch.setattr(recovery, 'get_storage', lambda *_: storage)
    job = jobs.IngestJob(None, 'lock', 'test')
    await job.record(doc_id=42)
    assert job.path in storage.objects
    await job.record(state='done')
    assert job.path not in storage.objects
    archived = json.loads(storage.objects[jobs.ARCHIVE_PREFIX + 'test.json'])
    assert archived['state'] == 'done' and archived['doc_id'] == 42
    await recovery.recover()
    assert storage.reads == []


@pytest.mark.asyncio
async def test_legacy_done_job_is_read_only_once(monkeypatch):
    storage = MemoryStorage()
    path = jobs.PREFIX + 'legacy.json'
    storage.objects[path] = b'{"state":"done","id":"legacy"}'
    monkeypatch.setattr(recovery, 'get_storage', lambda *_: storage)
    await recovery.recover()
    await recovery.recover()
    assert storage.reads == [path]
    assert jobs.ARCHIVE_PREFIX + 'legacy.json' in storage.objects


@pytest.mark.asyncio
async def test_archive_write_failure_keeps_active_record(monkeypatch):
    storage = MemoryStorage()
    path = jobs.PREFIX + 'retry.json'
    storage.objects[path] = b'{"state":"done"}'
    storage.put_object = AsyncMock(side_effect=OSError('offline'))
    storage.delete = AsyncMock()
    with pytest.raises(OSError):
        await jobs.archive_completed_job(storage, path, {'state': 'done'})
    assert path in storage.objects
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_delete_failure_is_retryable(monkeypatch):
    storage = MemoryStorage()
    path = jobs.PREFIX + 'retry.json'
    storage.objects[path] = b'{"state":"done"}'
    delete = storage.delete
    storage.delete = AsyncMock(side_effect=OSError('offline'))
    with pytest.raises(OSError):
        await jobs.archive_completed_job(storage, path, {'state': 'done'})
    assert path in storage.objects
    assert jobs.ARCHIVE_PREFIX + 'retry.json' in storage.objects
    storage.delete = delete
    await jobs.archive_completed_job(storage, path, {'state': 'done'})
    assert path not in storage.objects


@pytest.mark.asyncio
async def test_pending_job_is_never_archived():
    storage = MemoryStorage()
    path = jobs.PREFIX + 'pending.json'
    storage.objects[path] = b'{"state":"pending"}'
    await jobs.archive_completed_job(storage, path, {'state': 'pending'})
    assert list(storage.objects) == [path]
