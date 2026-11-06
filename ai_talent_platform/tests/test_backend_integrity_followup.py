"""后端数据完整性后续回归：外部契约、授权取数、ETL 与通知安全边界。

标签：[架构组] [P0]

全部离线执行；数据库/HTTP/SMTP 使用明确测试替身，不替代 MySQL/PgSQL 集成验收。
"""
from __future__ import annotations

import smtplib
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai.agents import assessment_agent, assessment_dify_client, interview_record_parser
from app.ai.context import AIContext
from app.ai.skills.matching_skill import CandidateListSkill, MatchingScoreSkill, PositionSearchSkill
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.infrastructure.notify.email_client import send_email
from app.repositories.organization_repository import OrganizationRepository
from app.services.assessment_service import AssessmentService
from scripts import sync_analytics


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Db:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    def begin_nested(self):
        return _Nested()


@pytest.mark.asyncio
async def test_dify_client_sends_configured_workflow_id_and_rejects_failed_run(monkeypatch):
    monkeypatch.setattr(settings, "DIFY_WF_SCORE", "wf-score")
    monkeypatch.setattr(settings, "DIFY_BASE_URL", "http://dify.test")
    monkeypatch.setattr(settings, "DIFY_API_KEY", "test-key")
    seen = {}

    def handler(request):
        seen.update(request.read() and __import__("json").loads(request.read()))
        return httpx.Response(200, json={"data": {"status": "succeeded", "outputs": {"ok": True}}})

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(assessment_dify_client.httpx, "AsyncClient", factory)
    assert await assessment_dify_client.call_score_workflow({"answers": []}) == {"ok": True}
    assert seen["workflow_id"] == "wf-score"

    def failed(request):
        return httpx.Response(200, json={"data": {"status": "failed", "outputs": {}}})

    monkeypatch.setattr(assessment_dify_client.httpx, "AsyncClient",
                        lambda *a, **k: original(*a, transport=httpx.MockTransport(failed), **k))
    with pytest.raises(ExternalServiceError):
        await assessment_dify_client.call_score_workflow({"answers": []})


@pytest.mark.asyncio
async def test_internal_create_assessment_uses_authorized_database_questions(monkeypatch):
    db = _Db()
    service = AssessmentService(db)
    user = SimpleNamespace(id=1)
    service.repo = SimpleNamespace(
        get_talent_keywords=AsyncMock(return_value={"skills": ["Python"]}),
        create=AsyncMock(return_value=SimpleNamespace(id=5)),
    )
    questions = [{"type": "single", "content": "数据库题目", "options": None,
                  "answer": "A", "score": 10.0, "dimension": "python"}]
    retrieve = AsyncMock(return_value=questions)
    monkeypatch.setattr("app.ai.agents.resume_parser.retrieve_questions_rag", retrieve)
    assessment = await service.create_assessment(talent_id=8, position_id=None, title="真实组卷", user=user)
    assert assessment.id == 5
    retrieve.assert_awaited_once_with(db, user, "中级", ["Python"], count=5)
    assert db.added[0].content == "数据库题目"
    assert db.added[0].sort == 0

    service.repo.get_talent_keywords = AsyncMock(return_value={})
    with pytest.raises(NotFoundError):
        await service.create_assessment(talent_id=9, position_id=None, title="不存在", user=user)


@pytest.mark.asyncio
async def test_submit_locks_assessment_before_state_transition():
    service = AssessmentService(object())
    question = SimpleNamespace(id=1, type="single", answer="A", score=10, dimension="基础")
    get = AsyncMock(return_value=SimpleNamespace(status="in_progress", deadline=None))
    service.repo = SimpleNamespace(
        get_by_id_or_404=get, list_questions=AsyncMock(return_value=[question]),
        save_answer=AsyncMock(), update_status=AsyncMock(),
    )
    await service.submit_answers(7, [{"question_id": 1, "answer": "A"}])
    assert get.await_args.kwargs["lock"] is True


@pytest.mark.asyncio
async def test_assign_agent_reports_email_failure_without_claiming_success(monkeypatch):
    row = SimpleNamespace(talent_id=8, position_id=None,
                          talent=SimpleNamespace(email="candidate@example.invalid", name="候选人"))
    monkeypatch.setattr("app.services.assessment_service.AssessmentService.start_assessment",
                        AsyncMock(return_value={"questions": [{"question_id": 1}]}))
    monkeypatch.setattr("app.repositories.assessment_repository.AssessmentRepository.get_by_id_or_404",
                        AsyncMock(return_value=row))

    async def assign(state):
        return {"paper_id": "7", "questions": state["questions"], "status": "assigned"}

    async def dispatch(state):
        return {"status": "assign_failed", "email_result": {"ok": False, "simulated": False}}

    monkeypatch.setattr(assessment_agent, "NODE_REGISTRY", {"assign": assign, "dispatch": dispatch})
    agent = assessment_agent.AssessmentAgent(
        db=object(), user=SimpleNamespace(id=1, is_superuser=True, role_codes=["admin"], organization_id=None),
        context=AIContext(user_id=1, roles=["admin"]),
    )
    result = await agent.run({"assessment_id": 7, "stage": "assign"})
    assert result["ok"] is False
    assert result["result"]["email_result"]["ok"] is False
    assert result["error_code"] == 5001


def test_etl_uses_real_source_columns_and_ancestor_org_path():
    wide = sync_analytics.build_dim_talent_wide_rows(
        [{"id": 1, "name": "真实人才", "organization_id": 7, "status": "active"}], {}, {},
    )
    assert wide[0]["org_path"] == ""
    assert wide[0]["match_avg_score"] is None

    class Result:
        def all(self):
            return []

    class FakeMySQL:
        def __init__(self):
            self.sql = []

        async def execute(self, statement):
            self.sql.append(str(statement))
            return Result()

    import asyncio

    db = FakeMySQL()
    asyncio.run(sync_analytics._load_mysql_payload(db))
    assert all("talent_no" not in sql for sql in db.sql)
    assert any("r.is_deleted = 0" in sql for sql in db.sql)
    assert any("matching_records WHERE is_deleted = 0" in sql for sql in db.sql)


@pytest.mark.asyncio
async def test_etl_empty_source_clears_dimension_and_window_uses_aware_bounds():
    class FakePG:
        def __init__(self):
            self.calls = []
            self.rowcount = 2

        async def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            return SimpleNamespace(rowcount=self.rowcount)

    pg = FakePG()
    assert await sync_analytics._delete_missing(pg, "dim_talent_wide", "talent_id", []) == 2
    assert "DELETE FROM dim_talent_wide" in pg.calls[-1][0]

    pg = FakePG()
    await sync_analytics._replace_window(
        pg, "fact_training_track", "INSERT INTO fact_training_track (talent_id) VALUES (:talent_id)",
        date(2026, 10, 1), date(2026, 11, 1), [], "event_at", False,
    )
    params = pg.calls[0][1]
    assert params["start"].tzinfo is not None and params["end"].tzinfo is not None


def test_organization_subtree_uses_full_self_prefix():
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeDB:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return Result()

    import asyncio

    db = FakeDB()
    asyncio.run(OrganizationRepository().list_subtree(db, "/1/3/"))
    sql = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "LIKE '/1/3/%'" in sql
    assert "path = '/1/3/'" not in sql


@pytest.mark.asyncio
async def test_matching_skills_read_persisted_service_results(monkeypatch):
    item = SimpleNamespace(talent_id=1, score=88.0, dimension_scores={"skill": 90},
                           highlights=["命中"], gaps=[], suggestion=None)
    report = SimpleNamespace(items=[item])

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def get_latest(self, position_id):
            assert position_id == 2
            return report

    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr("app.infrastructure.database.session.session_scope", fake_scope)
    monkeypatch.setattr(MatchingScoreSkill, "_load_service_cls", staticmethod(lambda: FakeService))
    monkeypatch.setattr("app.ai.skills.matching_skill.import_dependency", lambda *a, **k: FakeService)
    scored = await MatchingScoreSkill().execute(talent_id=1, position_id=2)
    assert scored["total_score"] == 88.0 and scored["source"] == "matching_service"
    candidates = await CandidateListSkill().execute(position_id=2, top_k=1)
    assert candidates[0]["talent_id"] == 1 and candidates[0]["score"] == 88.0
    with pytest.raises(NotFoundError):
        await MatchingScoreSkill().execute(talent_id=99, position_id=2)


@pytest.mark.asyncio
async def test_position_search_respects_scope_and_uses_repository_pagination(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr("app.infrastructure.database.session.session_scope", fake_scope)
    repository = SimpleNamespace(paginate=AsyncMock(return_value=SimpleNamespace(items=[{"id": 2, "title": "真实岗位"}])))
    context = SimpleNamespace(roles=["admin"], organization_id=1)
    rows = await PositionSearchSkill()._query(repository, "工程师", "open", 10, context)
    assert rows == [{"id": 2, "title": "真实岗位"}]
    assert repository.paginate.await_args.kwargs["filters"] == {"status": "open"}
    assert repository.paginate.await_args.kwargs["keyword"] == "工程师"

    repository.paginate.reset_mock()
    rows = await PositionSearchSkill()._query(repository, "工程师", "open", 10,
                                              SimpleNamespace(roles=["employee"], organization_id=1))
    assert rows == []
    repository.paginate.assert_not_awaited()


@pytest.mark.asyncio
async def test_smtp_refuses_plaintext_credentials(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_USER", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(settings, "SMTP_TLS", False)
    state = {"login": False}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, **kwargs):
            raise AssertionError("TLS disabled must not starttls")

        def login(self, *args):
            state["login"] = True

        def send_message(self, msg):
            raise AssertionError("must not send")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result = await send_email("candidate@example.invalid", template="assessment_invite",
                              params={"name": "候选人", "paper": "岗位", "url": "http://exam.test"})
    assert result["ok"] is False and state["login"] is False


def test_interview_score_range_is_not_fabricated():
    with pytest.raises(ValueError):
        interview_record_parser._normalize({"interview_score": 101})
    assert interview_record_parser._normalize({"interview_score": 0})["interview_score"] is None
    parsed = interview_record_parser._parse_fallback_text("面试得分：101\n岗位：工程师")
    assert parsed["interview_score"] is None
    assert parsed["position"] == "工程师"
