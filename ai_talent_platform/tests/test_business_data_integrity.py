"""真实业务数据链路回归（离线单元层）。

标签：[架构组] [P0]

这些用例验证业务编排、权限与数据事实来源，不替代 MySQL/PgSQL 集成验收。
外部传输与模型调用使用测试替身；被测服务逻辑、Schema 校验和状态规则真实执行。
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.agents import assessment_agent, assessment_dify_client, assessment_nodes, resume_parser
from app.ai.context import AIContext
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessRuleError, ExternalServiceError, ParamError
from app.models.analytics import DimOrganization, DimTalentWide, FactAssessmentHistory, FactTrainingTrack, FactTalentSnapshot
from app.schemas.question_bank import QuestionOption, QuestionSearchResponse, SelectedQuestion
from app.services.assessment_service import AssessmentService
from app.services.invite_notify import send_email as invite_email, send_sms
from scripts import sync_analytics


def _question(qid: int, kind: str = "single", *, similarity: float | None = 0.97) -> SelectedQuestion:
    return SelectedQuestion(
        question_id=qid, bank_id=1, external_id=f"source-{qid}", content_version=1,
        technology="python", difficulty="intermediate", question_type=kind,
        knowledge_points=["基础"], stem=f"数据库题目{qid}",
        options=[QuestionOption(key="A", text="选项A"), QuestionOption(key="B", text="选项B")]
        if kind != "text" else None,
        answer="A" if kind != "text" else "参考答案", score=similarity,
    )


def _response(items, shortage=None):
    return QuestionSearchResponse(
        mode="semantic", requested_count=sum((shortage or {}).values()) + len(items),
        returned_count=len(items), shortage=shortage or {}, items=items,
    )


@pytest.mark.asyncio
async def test_question_selection_uses_database_and_never_similarity_as_points(monkeypatch):
    search = AsyncMock(return_value=_response([_question(11), _question(12, "text")]))
    monkeypatch.setattr("app.services.question_bank_service.QuestionBankService.search", search)
    questions = await resume_parser.retrieve_questions_rag(
        object(), SimpleNamespace(id=1, is_superuser=True, role_codes=["admin"]),
        "中级", ["Python"], count=2,
    )
    assert [q["content"] for q in questions] == ["数据库题目11", "数据库题目12"]
    assert all(q["score"] == 10.0 for q in questions)
    request = search.await_args.args[0]
    assert request.include_answers is True
    assert request.technology == "python"


@pytest.mark.asyncio
async def test_question_selection_falls_back_only_to_authorized_random_database(monkeypatch):
    search = AsyncMock(side_effect=[AIError("vector unavailable"), _response([_question(21)])])
    monkeypatch.setattr("app.services.question_bank_service.QuestionBankService.search", search)
    questions = await resume_parser.retrieve_questions_rag(
        object(), SimpleNamespace(id=1, is_superuser=True, role_codes=["admin"]),
        "中级", ["Python"], count=1,
    )
    assert questions[0]["content"] == "数据库题目21"
    assert search.await_count == 2
    assert search.await_args_list[1].args[0].query is None
    assert search.await_args_list[1].args[0].include_answers is True


@pytest.mark.asyncio
async def test_question_shortage_is_not_filled_with_builtin_questions(monkeypatch):
    search = AsyncMock(side_effect=[
        _response([_question(31)], {"text": 1}),
        _response([], {"text": 1}),
    ])
    monkeypatch.setattr("app.services.question_bank_service.QuestionBankService.search", search)
    with pytest.raises(BusinessRuleError, match="题目不足"):
        await resume_parser.retrieve_questions_rag(
            object(), SimpleNamespace(id=1, is_superuser=True, role_codes=["admin"]),
            "中级", ["Python"], count=2,
        )
    assert search.await_args_list[1].args[0].exclude_question_ids == [31]


def test_legacy_static_question_entry_is_explicitly_unavailable():
    with pytest.raises(BusinessRuleError, match="数据库题库"):
        resume_parser.retrieve_questions("中级", ["Python"], count=5)


@pytest.mark.asyncio
async def test_unconfigured_dify_workflow_fails_instead_of_returning_fixture(monkeypatch):
    monkeypatch.setattr(settings, "DIFY_WF_SCORE", "")
    monkeypatch.setattr(settings, "DIFY_BASE_URL", "")
    monkeypatch.setattr(settings, "DIFY_API_KEY", "")
    with pytest.raises(ExternalServiceError, match="未完整配置"):
        await assessment_dify_client.call_score_workflow({"answers": []})


@pytest.mark.asyncio
async def test_objective_scoring_recomputes_from_standard_answer_not_payload_score():
    state = {"answers": [
        {"question_id": 1, "type": "single", "answer": "A", "standard_answer": "A",
         "score": 20, "dimension": "基础", "rule_score": 999},
        {"question_id": 2, "type": "single", "answer": "B", "standard_answer": "A",
         "score": 20, "dimension": "基础"},
        {"question_id": 3, "type": "text", "answer": "作答", "standard_answer": "参考",
         "score": 60, "dimension": "表达"},
    ]}
    result = await assessment_nodes.node_stat(state)
    assert result["rule_total_score"] == 20
    assert result["objective_possible"] == 40
    assert result["subjective_possible"] == 60
    assert result["dimension_rule_scores"] == {"基础": 20}


@pytest.mark.asyncio
async def test_subjective_score_rejects_missing_or_out_of_range_model_fields(monkeypatch):
    monkeypatch.setattr(assessment_nodes, "call_score_workflow", AsyncMock(return_value={"subjective_total": 80}))
    state = {"subjective_possible": 10, "answers": [{"type": "text", "answer": "作答"}]}
    with pytest.raises(AIError):
        await assessment_nodes.node_score(state)
    monkeypatch.setattr(assessment_nodes, "call_score_workflow", AsyncMock(return_value={
        "comprehensive": 80, "professional": 80, "position_fit": 101,
        "subjective_total": 80, "rationale": "依据作答",
    }))
    with pytest.raises(AIError):
        await assessment_nodes.node_score(state)


@pytest.mark.asyncio
async def test_assessment_agent_loads_answers_from_database_and_persists(monkeypatch):
    authoritative = {
        "assessment_id": 7, "talent_id": 8, "position_id": None,
        "answers": [{"question_id": 1, "type": "single", "answer": "A",
                     "standard_answer": "A", "score": 100, "dimension": "基础"}],
        "education": None, "work_years": None, "interview_score": None,
        "interview_records": [], "dimension_rule_scores": {},
    }
    loader = AsyncMock(return_value=authoritative)
    monkeypatch.setattr("app.services.assessment_service.AssessmentService.load_analysis_state", loader)
    seen = {}

    async def stat(state):
        seen["answers"] = state["answers"]
        return {"rule_total_score": 100, "objective_possible": 100, "subjective_possible": 0,
                "dimension_rule_scores": {"基础": 100}}

    async def passthrough(state):
        return {}

    async def report(state):
        return {"report": {"summary": "真实作答得分100"}, "total_score": 100,
                "dimension_scores": {"基础": 100}, "level": "优秀", "suggestions": []}

    monkeypatch.setattr(assessment_agent, "NODE_REGISTRY", {
        "stat": stat, "score": passthrough, "analyze": passthrough, "report": report,
    })
    agent = assessment_agent.AssessmentAgent(
        db=object(), user=SimpleNamespace(id=1, is_superuser=True, role_codes=["admin"], organization_id=None),
        context=AIContext(user_id=1, roles=["admin"]),
    )
    persist = AsyncMock(return_value={"assessment_id": 7, "persisted": True})
    monkeypatch.setattr(agent, "persist", persist)
    result = await agent.run({"assessment_id": 7, "answers": [{"question_id": 1, "answer": "B", "score": 0}]})
    assert result["ok"] is True
    assert seen["answers"] == authoritative["answers"]
    assert result["nodes_executed"] == ["stat", "score", "analyze", "report"]
    persist.assert_awaited_once()


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Db:
    def begin_nested(self):
        return _Nested()


@pytest.mark.asyncio
async def test_result_persistence_failure_is_not_reported_as_success(monkeypatch):
    service = AssessmentService(_Db())
    service.repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(return_value=SimpleNamespace(status="submitted")),
        save_result=AsyncMock(side_effect=RuntimeError("database unavailable")),
        update_status=AsyncMock(),
    )
    with pytest.raises(BusinessRuleError, match="保存失败"):
        await service.save_ai_evaluation(7, {"total_score": 80, "report": {}})
    service.repo.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_rejects_foreign_and_duplicate_questions():
    service = AssessmentService(object())
    question = SimpleNamespace(id=1, type="single", answer="A", score=10, dimension="基础")
    service.repo = SimpleNamespace(
        get_by_id_or_404=AsyncMock(return_value=SimpleNamespace(status="in_progress", deadline=None)),
        list_questions=AsyncMock(return_value=[question]),
        list_answers=AsyncMock(return_value=[]),
        save_answer=AsyncMock(), update_status=AsyncMock(),
    )
    with pytest.raises(ParamError, match="不属于"):
        await service.submit_answers(7, [{"question_id": 999, "answer": "A"}])
    with pytest.raises(ParamError, match="重复"):
        await service.submit_answers(7, [{"question_id": 1, "answer": "A"}, {"question_id": 1, "answer": "B"}])
    service.repo.save_answer.assert_not_awaited()
    result = await service.submit_answers(7, [{"question_id": 1, "answer": "A"}])
    assert result["rule_total_score"] == 10
    assert result["answered"] == 1


@pytest.mark.asyncio
async def test_notifications_never_claim_mock_delivery(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ENABLED", False)
    email = await invite_email("candidate@example.invalid", "subject", "body")
    sms = await send_sms("13800000000", "body")
    assert email["ok"] is False
    assert sms["ok"] is False
    assert sms["mode"] == "unconfigured"
    assert settings.SMTP_USE_SSL is not settings.SMTP_TLS


def test_etl_rows_match_current_analytics_schema_and_single_dimension_snapshots():
    talents = [{
        "id": 1, "name": "真实人才", "organization_id": 7, "education": "本科",
        "birth_date": date(1995, 1, 1), "work_years": 5, "skill_tags": ["Python"],
        "potential_level": "high", "status": "active", "created_at": date(2024, 1, 1),
    }, {
        "id": 2, "name": "已离职人才", "organization_id": 7, "education": "硕士",
        "status": "left", "created_at": date(2024, 1, 1), "updated_at": date(2026, 10, 1),
    }]
    snapshots = sync_analytics.build_snapshot_rows(talents, [date(2026, 9, 1)])
    assert all(set(row) <= set(FactTalentSnapshot.__table__.columns.keys()) for row in snapshots)
    assert all(not (row["education"] and row["age_group"]) for row in snapshots)
    assert all(row["talent_count"] == 1 for row in snapshots)
    wide = sync_analytics.build_dim_talent_wide_rows(talents, {7: "/1/7/"}, {1: 88.5})
    assert len(wide) == 1
    assert set(wide[0]) <= set(DimTalentWide.__table__.columns.keys())
    assert wide[0]["match_avg_score"] == 88.5
    org = sync_analytics.build_org_rows([{"id": 7, "code": "RD", "name": "研发", "level": 2, "path": "/1/", "parent_id": 1}])[0]
    assert set(org) <= set(DimOrganization.__table__.columns.keys())
    assert org["path"] == "/1/"


def test_etl_fact_rows_use_scored_results_and_real_training_fields():
    assessments = sync_analytics._assessment_rows([{
        "assessment_id": 9, "talent_id": 1, "position_id": 3,
        "scored_at": datetime(2026, 10, 1, 12), "total_score": 86,
        "level": "良好", "dimension_scores": {"基础": 90},
    }], date(2026, 10, 1), date(2026, 11, 1))
    assert set(assessments[0]) <= set(FactAssessmentHistory.__table__.columns.keys())
    assert assessments[0]["assessment_id"] == 9
    training = sync_analytics._training_rows([{
        "talent_id": 1, "course_id": 2, "plan_id": 3, "progress": 60,
        "event_at": datetime(2026, 10, 2, 12), "study_minutes": 45, "status": "in_progress",
    }], date(2026, 10, 1), date(2026, 11, 1))
    assert set(training[0]) <= set(FactTrainingTrack.__table__.columns.keys())
    assert training[0]["progress"] == 60
    assert training[0]["status"] == "in_progress"
