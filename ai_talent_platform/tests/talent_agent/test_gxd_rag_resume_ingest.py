"""
gxd-RAG 上传 -> 人才链路自动入库：触发规则、入口校验与整链回查。

标签：[RAG组] [P2]（人才自动入库联调用例，外部服务均使用替身）

覆盖的验收点：
    1. 只有 category=resume 且原件已提交的版本才排人才处理，且只排一次；
    2. 人才入口接受 knowledge_revision 原件（storage_type=local 也可），复用同一解析实现；
    3. 后台任务只按 revision_id + user_id 重建可信身份，不信任请求体角色；
    4. 回查接口能给出 revision -> 原件 -> 简历 -> 人才 -> 画像/向量 的完整链路。
"""
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.ai.context import AIContext
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.services import resume_ingest
from app.services.talent_resume_service import ResumeService

CTX = AIContext(user_id=3, roles=["admin"], organization_id=1)


def admin():
    return NS(id=3, role_codes=["admin"], is_superuser=False, organization_id=1)


def knowledge_only_user():
    # manager 角色没有 resume:parse（HR/管理员才有）
    return NS(id=4, role_codes=["manager"], is_superuser=False, organization_id=1)


# ---------------------------------------------------------------- 触发规则
def test_plan_requires_resume_category_and_committed_revision():
    """gxd-非简历分类、无 revision_id 或已拒绝的版本都不触发人才处理。"""
    user = admin()
    assert resume_ingest.plan({"category": "training", "revision_id": 9}, user)["scheduled"] is False
    assert resume_ingest.plan({"category": "resume"}, user)["scheduled"] is False
    assert resume_ingest.plan({"category": "resume", "revision_id": 9, "status": "rejected"}, user)["scheduled"] is False
    planned = resume_ingest.plan(
        {"category": "resume", "revision_id": 9, "source_file_id": 4, "status": "draft"}, user)
    assert planned["scheduled"] is True and planned["source_file_id"] == 4


def test_plan_skips_without_resume_parse_permission():
    """gxd-没有简历解析权限的账号只保存原件，不自动建模（角色来自认证用户）。"""
    planned = resume_ingest.plan({"category": "resume", "revision_id": 9}, knowledge_only_user())
    assert planned["scheduled"] is False and planned["reason"] == "no_resume_parse_permission"


def test_plan_tolerates_unloaded_roles():
    """gxd-角色关系没加载时视为无权限，绝不把用户当成管理员。"""
    class Broken:
        id = 5

        @property
        def role_codes(self):
            raise RuntimeError("lazy load")

    assert resume_ingest.can_ingest(Broken()) is False


# ---------------------------------------------------------------- 人才入口
def rag_file(storage_type="local", uploader_id=3):
    return NS(id=11, file_name="简历.txt", file_size=32, file_path="knowledge_revision/resume/abc.txt",
              file_hash="a" * 64, biz_type="knowledge_revision", uploader_id=uploader_id,
              storage_type=storage_type, is_deleted=False)


def revision(category="resume", source_file_id=11):
    return NS(id=77, doc_id=5, category=category, source_file_id=source_file_id, status="draft")


@pytest.mark.parametrize("storage_type", ["local", None, "minio"])
async def test_process_rag_revision_accepts_knowledge_revision_original(monkeypatch, storage_type):
    """gxd-入口只校验版本分类与原件归属，biz_type=kKnowledge_revision、local 存储都能处理。"""
    service = ResumeService(NS(begin_nested=nullcontext, flush=AsyncMock()), context=CTX)
    service.revision_row = AsyncMock(return_value=revision())
    service.repo.get_file = AsyncMock(return_value=rag_file(storage_type=storage_type))
    service.ingest_file = AsyncMock(return_value={
        "file_id": 11, "resume_id": 21, "talent_id": 31, "processing_status": "succeeded",
        "reused": False, "duplicates": [], "refresh": {"status": "complete"}})
    service.record_ingest = AsyncMock()

    result = await service.process_rag_revision(77)
    assert result["revision_id"] == 77 and result["source_file_id"] == 11
    assert result["talent_id"] == 31
    service.ingest_file.assert_awaited_once()
    service.record_ingest.assert_awaited_once()


async def test_process_rag_revision_rejects_other_category_and_foreign_original():
    """gxd-非简历分类不进入人才链路；他人原件按「不存在」处理，不泄露存在性。"""
    service = ResumeService(NS(begin_nested=nullcontext, flush=AsyncMock()), context=CTX)
    service.revision_row = AsyncMock(return_value=revision(category="training"))
    with pytest.raises(BusinessRuleError):
        await service.process_rag_revision(77)

    service.revision_row = AsyncMock(return_value=revision())
    service.repo.get_file = AsyncMock(return_value=rag_file(uploader_id=99))
    with pytest.raises(NotFoundError):
        await service.process_rag_revision(77)


async def test_ingest_file_reuses_archive_for_identical_bytes(monkeypatch):
    """gxd-同一内容哈希复用既有简历与人才，只补缺失的派生阶段，不重复解析。"""
    service = ResumeService(NS(begin_nested=nullcontext, flush=AsyncMock()), context=CTX)
    existing = NS(id=21, status="success", talent_id=31, parsed_at=None, parse_error=None,
                  parsed_data={"parsed": {"basic_info": {"name": "甲"}}, "duplicates": []})
    service.repo.get_by_hash = AsyncMock(return_value=existing)
    service.get_resume = AsyncMock(return_value=existing)
    service.ensure_derived = AsyncMock(return_value={"status": "complete"})
    service.parse_resume = AsyncMock(side_effect=AssertionError("不应重新解析"))

    result = await service.ingest_file(rag_file())
    assert result["reused"] is True and result["talent_id"] == 31
    assert result["processing_status"] == "succeeded"
    service.ensure_derived.assert_awaited_once_with(31)
    service.parse_resume.assert_not_awaited()


# ---------------------------------------------------------------- 后台任务
async def test_run_revision_ingest_rebuilds_identity_from_user_id(monkeypatch):
    """gxd-后台任务用 user_id 从数据库重建身份，再按 revision_id 执行人才链路。"""
    class Session:
        async def __aenter__(self):
            return NS()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.infrastructure.database.session.session_scope", lambda: Session())
    built = AIContext(user_id=3, roles=["admin"])
    monkeypatch.setattr(resume_ingest, "load_context", AsyncMock(return_value=built))
    processed = AsyncMock(return_value={"revision_id": 77, "source_file_id": 11, "resume_id": 21,
                                        "talent_id": 31, "processing_status": "succeeded", "reused": False})
    monkeypatch.setattr(ResumeService, "process_rag_revision", processed)

    result = await resume_ingest.run_revision_ingest(77, 3, "trace-1")
    assert result["talent_id"] == 31
    assert processed.await_args.args[0] == 77


async def test_run_revision_ingest_swallows_failures(monkeypatch):
    """gxd-后台任务失败只写日志并返回失败结果，不影响已返回的上传响应。"""
    class Session:
        async def __aenter__(self):
            return NS()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.infrastructure.database.session.session_scope", lambda: Session())
    monkeypatch.setattr(resume_ingest, "load_context", AsyncMock(return_value=AIContext(user_id=3, roles=["admin"])))
    monkeypatch.setattr(ResumeService, "process_rag_revision", AsyncMock(side_effect=RuntimeError("boom")))
    result = await resume_ingest.run_revision_ingest(77, 3)
    assert result["status"] == "failed" and "revision_id" in result


# ---------------------------------------------------------------- 路由定序
async def _upload_route(monkeypatch, category):
    """gxd-用替身服务调用 /files/upload，记录「提交 -> 排任务」的先后顺序。"""
    from app.routers import file_router
    from app.routers.knowledge_router import get_revision_service

    order = []

    async def commit():
        order.append("commit")

    async def run(revision_id, user_id, trace):
        order.append(("run", revision_id, user_id))

    monkeypatch.setattr(resume_ingest, "run_revision_ingest", run)
    service = NS(
        upload=AsyncMock(return_value={"revision_id": 77, "source_file_id": 11, "status": "draft",
                                       "category": category, "doc_id": 5}),
        commit_upload=commit,
        upload_and_publish=AsyncMock(),
    )

    async def context():
        yield service, admin()

    app = FastAPI()
    app.include_router(file_router.router, prefix="/api/v1")
    app.dependency_overrides[get_revision_service] = context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/files/upload", data={"category": category},
                                     files={"file": ("简历.txt", b"resume-bytes", "text/plain")})
    return response, order, service


async def test_upload_schedules_resume_ingest_once_after_commit(monkeypatch):
    """gxd-简历分类上传：先提交草稿，再排一次人才处理，并返回调度说明。"""
    response, order, service = await _upload_route(monkeypatch, "resume")
    assert response.status_code == 200
    assert order == ["commit", ("run", 77, 3)]
    payload = response.json()["data"]
    assert payload["resume_ingest"]["scheduled"] is True
    assert payload["revision_id"] == 77
    service.upload_and_publish.assert_not_awaited()


async def test_upload_other_category_does_not_touch_talent(monkeypatch):
    """gxd-非简历分类不排人才任务，返回值也不追加字段（保持既有契约）。"""
    response, order, _ = await _upload_route(monkeypatch, "training")
    assert response.status_code == 200
    assert order == ["commit"]
    assert "resume_ingest" not in response.json()["data"]


def test_preview_and_publish_routes_never_trigger_talent():
    """gxd-预览/发布/恢复不是上传入口，源码里不应引用人才调度。"""
    import inspect

    from app.routers import file_router

    for name in ("publish_revision", "restore_revision", "edit_revision", "preview_revision"):
        handler = getattr(file_router, name, None)
        if handler is None:
            continue
        source = inspect.getsource(handler)
        assert "resume_ingest" not in source and "run_revision_ingest" not in source, name


# ---------------------------------------------------------------- 整链回查
async def test_revision_ingest_status_reports_full_chain(monkeypatch):
    """gxd-按 revision_id 返回 原件 -> 简历 -> 人才 -> 画像/向量 的完整链路。"""
    from app.services.talent_profile_service import TalentService

    service = ResumeService(NS(flush=AsyncMock()), context=CTX)
    service.revision_row = AsyncMock(return_value=revision())
    service.repo.get_file = AsyncMock(return_value=rag_file())
    service.repo.list = AsyncMock(return_value=[NS(
        id=21, status="success", talent_id=31,
        parsed_data={"ingest": {"status": "succeeded", "processed_at": "2026-11-08T10:00:00"},
                     "duplicates": []})])
    monkeypatch.setattr(TalentService, "detail", AsyncMock(return_value={
        "portrait": {"status": "ready", "version": 1},
        "index": {"status": "current", "version": 2},
        "complete": True}))

    chain = await service.revision_ingest_status(77)
    assert (chain["source_file_id"], chain["resume_id"], chain["talent_id"]) == (11, 21, 31)
    assert chain["processing_status"] == "succeeded"
    assert chain["complete"] is True and chain["index"]["status"] == "current"


async def test_revision_ingest_status_marks_pending_review(monkeypatch):
    """gxd-疑似重复未确认时回查结果是 needs_review，不显示成已建档案。"""
    from app.services.talent_profile_service import TalentService

    service = ResumeService(NS(flush=AsyncMock()), context=CTX)
    service.revision_row = AsyncMock(return_value=revision())
    service.repo.get_file = AsyncMock(return_value=rag_file())
    service.repo.list = AsyncMock(return_value=[NS(
        id=21, status="success", talent_id=None,
        parsed_data={"ingest": {"status": "needs_review"}, "duplicates": [{"talent_id": 42, "score": 0.95}]})])
    monkeypatch.setattr(TalentService, "detail", AsyncMock())  # 没有 talent_id 时不应调用

    chain = await service.revision_ingest_status(77)
    assert chain["processing_status"] == "needs_review"
    assert chain["talent_id"] is None and chain["complete"] is False
    assert chain["duplicates"][0]["talent_id"] == 42
    TalentService.detail.assert_not_awaited()
