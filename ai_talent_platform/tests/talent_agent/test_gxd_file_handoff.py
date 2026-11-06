"""
gxd-仅替换数据库与外部服务，验证上传通知如何进入真实解析工作流。

标签：[RAG组] [P2]（文件交接联调用例，需外部服务）
"""
from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.context import AIContext
from app.ai.agents.resume_agent import ResumeAgent
from app.adapters.storage.base_storage import storage_registry
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.repositories.resume_repository import ResumeRepository as FileMetadataRepository
from app.repositories.talent_resume_repository import ResumeRepository
from app.services.file_service import FileService
from app.services.talent_resume_service import ResumeService
from app.services.talent_profile_service import TalentService
from app.services.talent_retrieval_service import TalentRetrievalService
from app.config.settings import settings


@pytest.mark.parametrize("candidate", [False, True])
async def test_notification_runs_chain_once(monkeypatch, candidate):
    """gxd-可读原件走完整工作流；候选暂停绑定；同一通知重放不重复解析或写索引。"""
    content = "林测试，Python工程师，3年工作经验。".encode()
    file = SimpleNamespace(id=5, file_name="简历.txt", file_size=len(content),
                           file_path="resumes/immutable-key", file_hash=sha256(content).hexdigest(),
                           biz_type="resume", uploader_id=3, storage_type="minio")
    ctx = AIContext(user_id=3, roles=["super_admin"])
    db = SimpleNamespace(begin_nested=nullcontext, flush=AsyncMock())
    service = ResumeService(db, context=ctx)
    rows = []
    events = []
    read = AsyncMock(return_value=content)
    monkeypatch.setattr(storage_registry, "try_get", lambda name: lambda: SimpleNamespace(read=read))
    monkeypatch.setattr(ResumeRepository, "get_file", AsyncMock(return_value=file))
    # 精简版仓储（resume_repository）可能没有 get_file：用 raising=False 兼容两种实现
    monkeypatch.setattr(FileMetadataRepository, "get_file", AsyncMock(return_value=file), raising=False)
    monkeypatch.setattr(settings, "MILVUS_ENABLED", True)

    async def find_hash(self, db, digest):
        """gxd-模拟当前测试事务可见的哈希唯一记录。"""
        return next((row for row in rows if row.file_hash == digest), None)

    async def save(self, db, row):
        """gxd-模拟新建简历并产生主键。"""
        row.id = 9
        rows.append(row)
        return row

    async def scoped(self, db, rid, context, lock=False):
        """gxd-按编号取回本用例的简历。"""
        return next((row for row in rows if row.id == rid), None)

    async def save_result(self, db, rid, parsed, accuracy=None):
        """gxd-模拟保存解析结果后的简历状态。"""
        row = rows[0]
        row.status, row.parsed_data = "success", parsed
        return row

    async def parse(self, payload):
        """gxd-校验真实TXT读取结果，用固定结构代替外部模型回复。"""
        assert "林测试" in payload["text"]
        events.append("parse")
        return {"ok": True, "result": {"basic_info": {"name": "林测试"}, "skills": ["Python"]}}

    async def dedupe(self, parsed):
        """gxd-分别模拟新人才与待确认的相似人才候选。"""
        events.append("dedupe")
        return [{"talent_id": 42}] if candidate else []

    talent = SimpleNamespace(id=15, version=1)

    async def create(self, row):
        """gxd-记录工作流的新建人才分支。"""
        events.append("create")
        return talent

    async def portrait(self, tid):
        """gxd-记录画像生成阶段。"""
        events.append("portrait")
        return {"talent_id": tid}

    async def index(self, tid):
        """gxd-记录人才集合的写入阶段。"""
        events.append("index")
        return {"talent_id": tid, "chunks": 1}

    for name, fn in (("get_by_hash", find_hash), ("save", save), ("get_scoped", scoped),
                     ("update_parse_result", save_result)):
        monkeypatch.setattr(ResumeRepository, name, fn)
    monkeypatch.setattr(ResumeAgent, "run", parse)
    monkeypatch.setattr(TalentService, "duplicate_candidates", dedupe)
    monkeypatch.setattr(TalentService, "create_from_resume", create)
    monkeypatch.setattr(TalentService, "get_talent", AsyncMock(return_value=talent))
    monkeypatch.setattr(TalentService, "generate_portrait", portrait)
    monkeypatch.setattr(TalentRetrievalService, "sync_index", index)

    first = await service.process_uploaded_file(file.id)
    second = await service.process_uploaded_file(file.id)
    assert len(rows) == 1 and second["reused"]
    read.assert_awaited_once_with(file.file_path)
    assert first["processing_status"] == second["processing_status"]
    if candidate:
        assert first["processing_status"] == "needs_review" and first["talent_id"] is None
        assert events == ["parse", "dedupe"]
    else:
        assert first["talent_id"] == second["talent_id"] == 15
        assert first["refresh"]["index"]["status"] == "success"
        assert events == ["parse", "dedupe", "create", "portrait", "index"]


async def test_minio_missing_reader(monkeypatch):
    """gxd-未接 MinIO 时显式报错，不把对象键交给本地磁盘读取器。"""
    monkeypatch.setattr(storage_registry, "try_get", lambda name: None)
    file = SimpleNamespace(file_name="resume.txt", file_size=10, storage_type="minio")
    with pytest.raises(ExternalServiceError, match="MinIO"):
        await FileService(None).read_original(file)


async def test_foreign_file_is_rejected():
    """gxd-即使有解析权限，普通用户也不能消费他人的上传通知。"""
    ctx = SimpleNamespace(user_id=3, is_super_admin=False, require_permission=lambda permission: None)
    service = ResumeService(object(), context=ctx)
    service.repo.get_file = AsyncMock(return_value=SimpleNamespace(biz_type="resume", uploader_id=4))
    with pytest.raises(NotFoundError):
        await service.process_uploaded_file(5)