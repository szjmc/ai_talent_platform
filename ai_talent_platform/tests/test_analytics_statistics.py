"""确定性分析专项：聚合SQL边界、筛选语义、权限、时间与API契约。

标签：[Vanna组] [P1]（分布/测评/趋势/显式对比回归）

使用假仓库/会话验证业务与查询形状，不连接业务数据库、不调用模型；
SQL按真实PostgreSQL方言编译。真实分析库执行仍需部署联调验收。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.exceptions import ParamError, PermissionDeniedError
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics_statistics import AnalyticsFilters, ComparisonRequest, DistributionQuery, TrendQuery
from app.services.analytics_statistics_service import AnalyticsStatisticsService

ADMIN = {"user_id": 1, "roles": ["admin"], "data_scope": "all", "organization_id": 1}
MANAGER = {"user_id": 2, "roles": ["manager"], "data_scope": "dept_sub", "organization_id": 3}


class Rows:
    def __init__(self, values=None, scalar=None):
        self.values = values or []
        self.scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self.values

    def one(self):
        assert len(self.values) == 1
        return self.values[0]

    def scalar_one(self):
        return self.scalar


class RecordingSession:
    """保留实际ORM语句用于检查，结果为显式测试夹具。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [Rows()])
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.responses.pop(0)


class FakeRepository:
    def __init__(self):
        self.month = date(2026, 10, 1)
        self.buckets = {self.month: [{"value": 3, "label": "技术部", "count": 20}]}
        self.skills = [{"value": "Python", "label": "Python", "count": 8},
                       {"value": None, "label": "未知", "count": 2}]
        self.trends = [{"month": date(2026, 10, 1), "value": 20}]
        self.calls = []

    async def latest_month(self, scope, filters):
        self.calls.append(("latest", scope, filters))
        return self.month

    async def snapshot_distribution(self, type_, month, scope, filters):
        self.calls.append(("snapshot", scope, filters))
        return self.buckets.get(month, [])

    async def skill_distribution(self, filters):
        self.calls.append(("skills", filters))
        return self.skills

    async def assessment_summary(self, filters):
        self.calls.append(("assessment", filters))
        return {"record_count": 4, "scored_count": 3, "talent_count": 2,
                "average_score": 80, "minimum_score": 60, "maximum_score": 100}

    async def snapshot_trend(self, scope, filters, start, end):
        self.calls.append(("trend", scope, filters, start, end))
        return self.trends


def service(scope=None, repository=None):
    return AnalyticsStatisticsService(None, scope or ADMIN, repository=repository or FakeRepository())


@pytest.mark.parametrize("payload", [
    {"organization_ids": [0]}, {"month": "2026-10-02"},
    {"min_work_years": 5, "max_work_years": 3}, {"min_score": 90, "max_score": 20},
    {"start_at": "2026-01-01T00:00:00"},
    {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-01-01T00:00:00Z"},
    {"skills": [""]}, {"min_score": -1}, {"limit": 1001}, {"sql": "SELECT 1"},
])
def test_filters_reject_invalid_or_forged_input(payload):
    with pytest.raises(ValidationError):
        AnalyticsFilters.model_validate(payload)


def test_snapshot_layer_never_sums_summary_and_detail_together():
    db = RecordingSession()
    asyncio.run(AnalyticsRepository(db).snapshot_distribution("education", date(2026, 10, 1),
                                                              MANAGER, AnalyticsFilters(organization_ids=[30])))
    sql = str(db.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "age_group = ''" in sql and "education != ''" in sql
    assert "organization_id > 0" in sql
    assert "organization_id IN (30)" in sql
    assert "org_id = 3" in sql and "3/%" in sql
    assert "dim_organization" in sql
    assert "sum(" in sql and "GROUP BY" in sql


def test_total_snapshot_layer_excludes_education_and_age_subtotals():
    db = RecordingSession()
    asyncio.run(AnalyticsRepository(db).snapshot_trend(
        ADMIN, AnalyticsFilters(), date(2026, 1, 1), date(2026, 10, 1),
    ))
    sql = str(db.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "education = ''" in sql and "age_group = ''" in sql
    assert "snapshot_month >= '2026-01-01'" in sql
    assert "snapshot_month <= '2026-10-01'" in sql


def test_natural_language_skill_expansion_keeps_sql_safety_boundary():
    from app.adapters.nl2sql.sql_validator import SQLValidationError, inject_data_scope, validate_select_sql
    from scripts.train_vanna import EXAMPLE_QUERIES

    examples = [item for item in EXAMPLE_QUERIES if "unnest(" in item["sql"]]
    assert len(examples) >= 2
    for item in examples:
        validated = validate_select_sql(item["sql"])
        assert "UNNEST" in validated and "public.dim_talent_wide" in validated
        with pytest.raises(PermissionDeniedError):
            inject_data_scope(validated, MANAGER)
    with pytest.raises(SQLValidationError):
        validate_select_sql("SELECT unnest(skill_tags), pg_sleep(5) FROM dim_talent_wide")


@pytest.mark.parametrize("mode", ["all", "any"])
def test_skills_use_per_person_deduplication_and_bound_combination_filters(mode):
    db = RecordingSession()
    malicious = "Python'); DROP TABLE talents; --"
    filters = AnalyticsFilters(educations=["本科"], skills=[malicious], skill_mode=mode,
                               min_work_years=3, potential_levels=["A"])
    asyncio.run(AnalyticsRepository(db).skill_distribution(filters))
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert malicious not in sql
    assert any(malicious in value for value in compiled.params.values() if isinstance(value, list))
    assert "unnest(" in sql and "SELECT DISTINCT" in sql
    assert "btrim(filter_skills.tag) IN" in sql
    assert ("HAVING count(DISTINCT btrim(filter_skills.tag))" in sql) == (mode == "all")
    assert "NOT (EXISTS" in sql  # 没有有效技能的人员保留未知桶。
    assert "work_years >=" in sql and "education IN" in sql and "potential_level IN" in sql


def test_skill_all_filter_deduplicates_requested_tags_and_uses_display_normalization():
    db = RecordingSession()
    filters = AnalyticsFilters(skills=[" Python ", "Python", "SQL"], skill_mode="all")
    asyncio.run(AnalyticsRepository(db).skill_distribution(filters))
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "btrim(filter_skills.tag) IN" in sql
    assert "HAVING count(DISTINCT btrim(filter_skills.tag))" in sql
    assert any(value == ["Python", "SQL"] for value in compiled.params.values() if isinstance(value, list))
    # 重复的请求标签不会把all模式的达标数量错误增加为3。
    assert 2 in compiled.params.values()


def test_assessment_aggregates_records_and_valid_scores_without_join_multiplication():
    aggregate = {"record_count": 4, "scored_count": 3, "talent_count": 2,
                 "average_score": 80, "minimum_score": 60, "maximum_score": 100}
    db = RecordingSession([Rows([aggregate])])
    filters = AnalyticsFilters(educations=["本科"], min_score=0, max_score=100, assessment_ids=[1],
                               start_at="2026-01-01T00:00:00+08:00", end_at="2027-01-01T00:00:00+08:00")
    assert asyncio.run(AnalyticsRepository(db).assessment_summary(filters)) == aggregate
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "count(*)" in sql and "count(public.fact_assessment_history.total_score)" in sql
    assert "count(DISTINCT" in sql and "avg(" in sql
    assert "talent_id IN (SELECT" in sql and "JOIN" not in sql
    assert "scored_at >=" in sql and "scored_at <" in sql


def test_department_distribution_passes_scope_without_widening():
    repo = FakeRepository()
    result = asyncio.run(service(MANAGER, repo).distribution("org", AnalyticsFilters(organization_ids=[30])))
    assert result.items[0].count == 20
    assert repo.calls[0][1] is MANAGER and repo.calls[1][1] is MANAGER
    assert repo.calls[1][2].organization_ids == [30]


@pytest.mark.parametrize("method", ["skills", "assessment"])
def test_unmapped_department_data_is_denied_before_query(method):
    repo = FakeRepository()
    with pytest.raises(PermissionDeniedError):
        if method == "skills":
            asyncio.run(service(MANAGER, repo).distribution("skill", AnalyticsFilters()))
        else:
            asyncio.run(service(MANAGER, repo).assessment_summary(AnalyticsFilters()))
    assert repo.calls == []


@pytest.mark.parametrize("type_,filters", [
    ("age", {"educations": ["本科"]}), ("education", {"age_groups": ["30-39"]}),
    ("org", {"educations": ["本科"], "age_groups": ["30-39"]}),
    ("org", {"skills": ["Python"]}), ("skill", {"organization_ids": [1]}),
    ("skill", {"month": "2026-10-01"}),
])
def test_unsupported_filters_are_not_silently_ignored(type_, filters):
    repo = FakeRepository()
    with pytest.raises(ParamError):
        asyncio.run(service(repository=repo).distribution(type_, AnalyticsFilters(**filters)))
    assert repo.calls == []


def test_missing_snapshot_does_not_claim_zero_population():
    repo = FakeRepository()
    repo.month = None
    result = asyncio.run(service(repository=repo).distribution("education", AnalyticsFilters()))
    assert result.items == [] and result.month is None
    assert any("不能据此认定" in warning for warning in result.warnings)
    assert len(repo.calls) == 1


def test_distribution_limit_is_visible_and_skills_preserve_unknown():
    result = asyncio.run(service().distribution("skill", AnalyticsFilters(limit=1)))
    assert result.truncated and len(result.items) == 1
    assert any("截断" in warning for warning in result.warnings)
    complete = asyncio.run(service().distribution("skill", AnalyticsFilters()))
    assert complete.items[1].value is None and complete.items[1].label == "未知"


def test_distribution_respects_server_category_limit(monkeypatch):
    from app.config.settings import settings
    repo = FakeRepository()
    monkeypatch.setattr(settings, "NL2SQL_MAX_ROWS", 1)
    result = asyncio.run(service(repository=repo).distribution("skill", AnalyticsFilters(limit=100)))
    assert len(result.items) == 1 and result.truncated
    assert repo.calls[0][1].limit == 1
    assert any("服务端" in warning for warning in result.warnings)


def test_monthly_trend_keeps_real_zero_and_missing_period_distinct():
    repo = FakeRepository()
    repo.trends = [{"month": date(2026, 10, 1), "value": 0}, {"month": date(2026, 12, 1), "value": 5}]
    result = asyncio.run(service(repository=repo).trend(TrendQuery(start_month="2026-10-01", end_month="2026-12-01")))
    assert [item["value"] for item in result["items"]] == [0, None, 5]
    assert [item["missing"] for item in result["items"]] == [False, True, False]


@pytest.mark.parametrize("field", ["min_score", "max_score"])
def test_trend_does_not_accept_assessment_score_fields(field):
    """分数字段不能从通用筛选泄漏到趋势请求；测评入口仍支持分数筛选。"""
    with pytest.raises(ValidationError):
        TrendQuery(start_month="2026-10-01", end_month="2026-10-01", **{field: 60})
    assert getattr(AnalyticsFilters(**{field: 60}), field) == 60


def test_yoy_and_mom_use_same_authorized_filter_and_natural_calendar():
    repo = FakeRepository()
    repo.trends = [{"month": date(2025, 1, 1), "value": 10},
                   {"month": date(2025, 12, 1), "value": 15},
                   {"month": date(2026, 1, 1), "value": 20}]
    data = TrendQuery(start_month="2026-01-01", end_month="2026-01-01", comparison="both", organization_ids=[3])
    result = asyncio.run(service(MANAGER, repo).trend(data))
    item = result["items"][0]
    assert item["mom"]["baseline_month"] == date(2025, 12, 1)
    assert item["mom"]["rate"] == pytest.approx(1 / 3)
    assert item["yoy"]["baseline_month"] == date(2025, 1, 1)
    assert item["yoy"]["rate"] == 1 and item["yoy"]["status"] == "已计算"
    assert len(repo.calls) == 1 and repo.calls[0][1] is MANAGER
    assert repo.calls[0][2].organization_ids == [3]
    assert repo.calls[0][3] == date(2025, 1, 1)


@pytest.mark.parametrize("rows,reason", [
    ([{"month": date(2026, 1, 1), "value": 20}], "基期缺少"),
    ([{"month": date(2025, 12, 1), "value": 0}, {"month": date(2026, 1, 1), "value": 20}], "分母为0"),
    ([{"month": date(2025, 12, 1), "value": 10}], "本期缺少"),
])
def test_growth_missing_or_zero_denominator_is_not_reported_as_zero(rows, reason):
    repo = FakeRepository()
    repo.trends = rows
    result = asyncio.run(service(repository=repo).trend(
        TrendQuery(start_month="2026-01-01", end_month="2026-01-01", comparison="mom")))
    growth = result["items"][0]["mom"]
    assert growth["rate"] is None and growth["status"] == "无法计算"
    assert reason in growth["reason"]


def test_explicit_comparison_preserves_missing_and_zero_denominator():
    repo = FakeRepository()
    repo.buckets = {
        date(2026, 10, 1): [{"value": 3, "label": "技术部", "count": 20},
                             {"value": 4, "label": "市场部", "count": 2}],
        date(2026, 9, 1): [{"value": 3, "label": "技术部", "count": 0},
                            {"value": 5, "label": "缺本期", "count": 7}],
    }
    data = ComparisonRequest(type="org", current={"month": "2026-10-01"}, baseline={"month": "2026-09-01"})
    result = asyncio.run(service(repository=repo).compare(data))
    assert result["items"][0]["difference"] == 20 and result["items"][0]["change_rate"] is None
    assert result["items"][1]["baseline"] is None and result["items"][1]["difference"] is None
    assert result["items"][2]["current"] is None


def test_statistics_api_contract_and_authentication(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.core.dependencies import get_current_user
    from app.core.exception_handlers import register_exception_handlers
    from app.routers.analytics_router import get_service, router
    from app.services.analytics_service import AnalyticsService

    class Facade(AnalyticsService):
        @asynccontextmanager
        async def _statistics(self, current_user, *, validate=None):
            instance = service()
            if validate is not None:
                validate(instance)
            yield instance

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_service] = lambda: Facade()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=True, role_codes=["admin"])

    # Swagger/Apifox导入来源不应再将趋势误标为支持测评分数条件。
    paths = app.openapi()["paths"]
    trend_parameters = {item["name"] for item in paths["/api/v1/analytics/trend"]["get"]["parameters"]}
    assessment_parameters = {item["name"] for item in paths["/api/v1/analytics/assessment/summary"]["get"]["parameters"]}
    assert not {"min_score", "max_score"} & trend_parameters
    assert {"min_score", "max_score"} <= assessment_parameters

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/analytics/distribution", params={"type": "org", "month": "2026-10-01"})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["items"][0]["count"] == 20
            score = await client.get("/api/v1/analytics/assessment/summary", params=[("skills", "Python"), ("educations", "本科")])
            assert score.status_code == 200, score.text
            assert score.json()["data"]["average_score"] == 80
            bounded_score = await client.get("/api/v1/analytics/assessment/summary",
                                             params={"min_score": 60, "max_score": 70})
            assert bounded_score.status_code == 200, bounded_score.text
            trend_params = {"start_month": "2026-10-01", "end_month": "2026-10-01"}
            trend = await client.get("/api/v1/analytics/trend", params=trend_params)
            assert trend.status_code == 200, trend.text
            assert trend.json()["data"]["items"][0]["value"] == 20
            for field in ("min_score", "max_score"):
                rejected = await client.get("/api/v1/analytics/trend", params={**trend_params, field: 60})
                assert rejected.status_code == 422, rejected.text
                assert any(error["field"].endswith(field)
                           for error in rejected.json()["data"]["errors"])
            invalid = await client.get("/api/v1/analytics/distribution", params={"type": "org", "roles": "admin"})
            assert invalid.status_code == 422
            reverse = await client.get("/api/v1/analytics/trend", params={"start_month": "2026-11-01", "end_month": "2026-10-01"})
            assert reverse.status_code == 422
            comparison = await client.post("/api/v1/analytics/compare", json={"type": "org",
                "current": {"month": "2026-10-01"}, "baseline": {"month": "2026-09-01"}})
            assert comparison.status_code == 200
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=False, role_codes=["employee"])
            denied = await client.get("/api/v1/analytics/distribution", params={"type": "org"})
            assert denied.status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("fail", [False, True])
def test_statistics_session_uses_readonly_account_and_cleans_up(fail, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from app.config.settings import settings
    from app.core.exceptions import AIError
    from app.infrastructure.database import analytics_query

    commands, engines = [], []

    class Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *args):
            return False

    class Connection:
        def begin(self):
            return Context(self)

        async def execute(self, statement, parameters=None):
            commands.append((str(statement), parameters))

    class Engine:
        def __init__(self):
            self.dispose = AsyncMock()

        def connect(self):
            return Context(Connection())

    def engine_factory(url, **kwargs):
        assert url.username == "stats_readonly"
        assert url.password == "test-only-password"
        engine = Engine()
        engines.append(engine)
        return engine

    def session_factory(**kwargs):
        assert kwargs["autoflush"] is False
        return Context("test_session")

    monkeypatch.setattr(settings, "PGSQL_ENABLED", True)
    monkeypatch.setattr(settings, "NL2SQL_PGSQL_USER", "stats_readonly")
    monkeypatch.setattr(settings, "NL2SQL_PGSQL_PASSWORD", "test-only-password")
    monkeypatch.setattr(analytics_query, "create_async_engine", engine_factory)
    monkeypatch.setattr(analytics_query, "AsyncSession", session_factory)

    async def scenario():
        async with analytics_query.readonly_analytics_session() as session:
            assert session == "test_session"
            if fail:
                raise SQLAlchemyError("test query failed")

    if fail:
        with pytest.raises(AIError):
            asyncio.run(scenario())
    else:
        asyncio.run(scenario())
    assert "REPEATABLE READ, READ ONLY" in commands[0][0]
    assert commands[1][1]["timeout"]
    engines[0].dispose.assert_awaited_once()


def test_statistics_facade_rebuilds_scope_and_restores_context(monkeypatch):
    from app.ai.context import get_context
    from app.services import analytics_service

    opened = []
    repo = FakeRepository()

    @asynccontextmanager
    async def read_session():
        opened.append(get_context().user_id)
        yield None

    monkeypatch.setattr(analytics_service, "readonly_analytics_session", read_session)
    monkeypatch.setattr(analytics_service, "AnalyticsStatisticsService",
                        lambda db, scope: AnalyticsStatisticsService(db, scope, repository=repo))
    original = get_context()
    current_user = SimpleNamespace(id=5, role_codes=[], is_superuser=True, organization_id=8)
    result = asyncio.run(analytics_service.AnalyticsService().distribution("org", AnalyticsFilters(), current_user))
    assert result.items and opened == [5]
    assert repo.calls[0][1]["roles"] == ["super_admin"]
    assert repo.calls[0][1]["data_scope"] == "all"
    assert get_context() is original
    with pytest.raises(PermissionDeniedError):
        asyncio.run(analytics_service.AnalyticsService().distribution("org", AnalyticsFilters(),
            SimpleNamespace(id=6, role_codes=["employee"], is_superuser=False, organization_id=8)))
    assert opened == [5]
    with pytest.raises(PermissionDeniedError):
        asyncio.run(analytics_service.AnalyticsService().assessment_summary(AnalyticsFilters(),
            SimpleNamespace(id=7, role_codes=["manager"], is_superuser=False, organization_id=8)))
    assert opened == [5]  # 部门用户访问无可靠映射的测评数据时，不建立数据库连接。


def test_statistics_session_rejects_schema_mismatch_before_connect(monkeypatch):
    from app.config.settings import settings
    from app.core.exceptions import AIError
    from app.infrastructure.database import analytics_query

    monkeypatch.setattr(settings, "PGSQL_ENABLED", True)
    monkeypatch.setattr(settings, "NL2SQL_PGSQL_USER", "stats_readonly")
    monkeypatch.setattr(settings, "NL2SQL_ALLOWED_SCHEMA", "different_schema")
    monkeypatch.setattr(analytics_query, "create_async_engine", lambda *a, **k: pytest.fail("不得建立连接"))

    async def scenario():
        async with analytics_query.readonly_analytics_session():
            pytest.fail("不能进入统计会话")

    with pytest.raises(AIError, match="schema"):
        asyncio.run(scenario())
