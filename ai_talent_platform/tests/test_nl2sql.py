"""Vanna MVP 离线验收：AST安全、权限隔离、Adapter/训练、API与真实本地记忆。

标签：[Vanna组] [P0]（SQL安全、权限、适配器、训练记忆及API综合回归）
不连接 MySQL/PgSQL，不调用真实 LLM，不写业务数据。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.adapters.nl2sql.sql_validator import (
    SQLValidationError, checked_scope, inject_data_scope, inject_limit, validate_select_sql,
)
from app.adapters.nl2sql.vanna_adapter import LocalTextEmbedding, VannaAdapter, _vanna_engine
from app.config.settings import settings
from app.core.exceptions import AIError, PermissionDeniedError
from app.schemas.analytics import NL2SQLResponse

ADMIN = {"user_id": 1, "roles": ["admin"], "organization_id": 1}
MANAGER = {"user_id": 2, "roles": ["manager"], "organization_id": 3}


@pytest.mark.parametrize("sql", [
    "DROP TABLE dim_talent_wide", "DELETE FROM dim_talent_wide", "UPDATE dim_talent_wide SET name='x'",
    "INSERT INTO dim_talent_wide(name) VALUES('x')", "TRUNCATE dim_talent_wide",
    "CREATE TABLE x(id int)", "ALTER TABLE dim_talent_wide ADD x int", "GRANT ALL ON dim_talent_wide TO public",
    "SELECT * FROM dim_talent_wide; DELETE FROM dim_talent_wide", "SELECT * FROM dim_talent_wide -- comment",
    "SELECT /* comment */ * FROM dim_talent_wide", "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.tables", "SELECT * FROM mysql.user", "SELECT * FROM talents",
    "SELECT * FROM other.dim_talent_wide", "SELECT * FROM other.public.dim_talent_wide",
    "SELECT * FROM dim_talent_wide WHERE talent_id IN (SELECT id FROM users)",
    "WITH t AS (SELECT * FROM users) SELECT * FROM t",
    "SELECT name FROM dim_talent_wide UNION SELECT username FROM users",
    "SELECT pg_sleep(30) FROM dim_talent_wide", "SELECT pg_read_file('/etc/passwd') FROM dim_talent_wide",
    "SELECT set_config('search_path','x',false) FROM dim_talent_wide",
    "SELECT public.count(*) FROM dim_talent_wide", "SELECT * INTO temp_copy FROM dim_talent_wide",
    "SELECT * FROM dim_talent_wide FOR UPDATE", "SELECT * FROM dim_talent_wide FOR SHARE",
    "WITH RECURSIVE t AS (SELECT * FROM dim_talent_wide) SELECT * FROM t",
    "SELECT name::regclass FROM dim_talent_wide", "SELECT * FROM dim_talent_wide WHERE talent_id = :user_id",
    "SELECT * FROM generate_series(1,1000000)", "SELECT 1", "SELECT * FROM dim_talent_wide; SELECT 1;",
])
def test_rejects_unsafe_sql(sql):
    with pytest.raises(SQLValidationError):
        validate_select_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT education, count(*) FROM dim_talent_wide GROUP BY education;",
    "WITH t AS (SELECT * FROM dim_talent_wide) SELECT count(*) FROM t",
    "SELECT org_id FROM dim_organization UNION SELECT organization_id FROM fact_talent_snapshot",
    "SELECT * FROM fact_talent_snapshot WHERE organization_id>0 AND education=''",
    "SELECT date_trunc('month', scored_at), avg(total_score) FROM fact_assessment_history GROUP BY 1",
])
def test_accepts_readonly_queries(sql):
    assert "public." in validate_select_sql(sql)


def test_limit_clamps_union_and_existing_limit():
    from sqlglot import parse_one
    sql = inject_limit("SELECT * FROM dim_talent_wide LIMIT 1000000", 23)
    assert parse_one(sql).args["limit"].expression.to_py() == 23
    assert inject_limit("SELECT org_id FROM dim_organization UNION SELECT org_id FROM dim_organization", 10).endswith("LIMIT 10")
    for bad in (0, 1001, -1, True):
        with pytest.raises(SQLValidationError):
            inject_limit("SELECT * FROM dim_talent_wide", bad)


def test_scope_covers_join_cte_union_and_nested_queries():
    from sqlglot import exp, parse_one
    sql = """WITH f AS (SELECT * FROM fact_talent_snapshot)
        SELECT o.org_id FROM f JOIN dim_organization o ON o.org_id=f.organization_id
        WHERE f.organization_id IN (SELECT org_id FROM dim_organization)
        UNION SELECT org_id FROM dim_organization"""
    scoped = inject_data_scope(sql, MANAGER)
    tree = parse_one(scoped, read="postgres")
    physical = [table for table in tree.find_all(exp.Table) if table.db == "public"]
    # 每处原始物理表都在带 WHERE 的派生表内；注入的部门子查询也带过滤。
    assert len(physical) >= 4
    assert "3/%" in scoped
    # 每个原始表出现处都应被独立包裹，不能只给最外层追加一次过滤。
    wrappers = [sub for sub in tree.find_all(exp.Subquery) if sub.alias and
                isinstance(sub.this, exp.Select) and sub.this.args.get("where")]
    assert len(wrappers) == 4
    for sub in wrappers:
        assert "org_id = 3" in sub.this.args["where"].sql(dialect="postgres")
    validate_select_sql(scoped)


def test_scope_does_not_trust_supplied_all_and_denies_unmapped_tables():
    with pytest.raises(PermissionDeniedError):
        inject_data_scope("SELECT * FROM dim_talent_wide", {**MANAGER, "data_scope": "all"})
    for value in (None, {"user_id": 1, "roles": ["employee"]}, {**MANAGER, "organization_id": None},
                  {**ADMIN, "user_id": True}):
        with pytest.raises(PermissionDeniedError):
            checked_scope(value)


def test_cte_named_like_real_table_cannot_hide_external_table():
    with pytest.raises(SQLValidationError):
        validate_select_sql("WITH dim_talent_wide AS (SELECT * FROM users) SELECT * FROM dim_talent_wide")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "PGSQL_ENABLED", True)
    monkeypatch.setattr(settings, "NL2SQL_PGSQL_USER", "test_readonly")
    monkeypatch.setattr(settings, "NL2SQL_PGSQL_PASSWORD", "")
    return monkeypatch


def adapter_for(sql="SELECT education, count(*) AS n FROM dim_talent_wide GROUP BY education", rows=None):
    executor = AsyncMock(return_value=(["education", "n"], rows if rows is not None else [["本科", 2]]))
    adapter = VannaAdapter(executor=executor)
    adapter._generate = AsyncMock(return_value=sql)
    return adapter, executor


@pytest.mark.asyncio
async def test_adapter_returns_real_contract_and_clamps_server_limit(configured):
    configured.setattr(settings, "NL2SQL_MAX_ROWS", 20)
    adapter, executor = adapter_for()
    result = await adapter.ask("学历分布", user_scope=ADMIN, max_rows=100, with_insight=False)
    assert isinstance(result, NL2SQLResponse)
    assert result.chart.type == "bar" and result.rows == [["本科", 2]]
    assert result.sql.endswith("LIMIT 20") and result.elapsed_ms >= 0 and not result.cached
    executor.assert_awaited_once()
    assert executor.call_args.args[1] == 20


@pytest.mark.asyncio
async def test_adapter_invalid_sql_never_executes(configured):
    adapter, executor = adapter_for("SELECT pg_sleep(3) FROM dim_talent_wide")
    with pytest.raises(SQLValidationError):
        await adapter.ask("测试", user_scope=ADMIN)
    executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_denies_missing_auth_before_generation(configured):
    adapter, executor = adapter_for()
    with pytest.raises(PermissionDeniedError):
        await adapter.ask("测试", user_scope=None)
    adapter._generate.assert_not_awaited()
    executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_disabled_pgsql_never_generates_or_falls_back(configured):
    configured.setattr(settings, "PGSQL_ENABLED", False)
    adapter, executor = adapter_for()
    with pytest.raises(AIError):
        await adapter.ask("测试", user_scope=ADMIN)
    adapter._generate.assert_not_awaited()
    executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_requires_separate_query_credentials(configured):
    configured.setattr(settings, "NL2SQL_PGSQL_USER", "")
    adapter, executor = adapter_for()
    with pytest.raises(AIError, match="只读账号"):
        await adapter.ask("测试", user_scope=ADMIN)
    adapter._generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_manager_sql_contains_department_restrictions(configured):
    adapter, executor = adapter_for("SELECT organization_id, talent_count FROM fact_talent_snapshot")
    await adapter.ask("部门人数", user_scope=MANAGER, with_insight=False)
    assert "organization_id IN" in executor.call_args.args[0]
    assert "org_id = 3" in executor.call_args.args[0]


@pytest.mark.asyncio
async def test_adapter_insight_failure_preserves_table(configured):
    adapter, executor = adapter_for()
    adapter._summarize = AsyncMock(side_effect=AIError("模型不可用"))
    result = await adapter.ask("学历分布", user_scope=ADMIN)
    assert result.rows and result.warnings and result.insight is None


@pytest.mark.asyncio
async def test_adapter_empty_result_and_insight(configured):
    adapter, _ = adapter_for(rows=[])
    result = await adapter.ask("学历分布", user_scope=ADMIN)
    assert result.chart is None and "没有" in result.insight


@pytest.mark.asyncio
async def test_adapter_timeout_is_structured(configured):
    adapter, executor = adapter_for()
    adapter._generate = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(AIError, match="超时"):
        await adapter.ask("学历分布", user_scope=ADMIN)
    executor.assert_not_awaited()


@pytest.mark.parametrize("question,cols,rows,kind", [
    ("人数", ["month", "n"], [[date(2026, 1, 1), Decimal("2")], [date(2026, 2, 1), Decimal("3")]], "line"),
    ("人数", ["month", "n"], [["2026-01", 2], ["2026-02", 3]], "line"),
    ("学历占比", ["education", "n"], [["本科", 2]], "pie"),
    ("分布", ["education", "n"], [["本科", 2]], "bar"),
    ("人数", ["n"], [[2]], None),
    ("分布", ["name", "value"], [["x", "text"]], None),
])
def test_chart_configs(question, cols, rows, kind):
    result, _warnings = VannaAdapter._infer_chart(question, cols, rows)
    assert (result.type if result else None) == kind


def test_corpus_has_40_valid_examples_and_metric_constraints():
    from scripts.train_vanna import build_corpus
    corpus = build_corpus()
    assert len(corpus["tables"]) == 6
    assert len(corpus["examples"]) >= 40
    for item in corpus["examples"]:
        validate_select_sql(item["sql"])
    assert any("匹配率" in doc for doc in corpus["documentation"])


def test_invalid_training_cannot_reset_memory():
    engine = SimpleNamespace(train=lambda **kw: pytest.fail("不可写入"),
                             remove_collection=lambda name: pytest.fail("不可重建"))
    adapter = VannaAdapter(engine=engine)
    with pytest.raises(SQLValidationError):
        adapter.train({"ddl": "DDL", "documentation": ["doc"],
                       "examples": [{"question": "危险", "sql": "DROP TABLE dim_talent_wide"}]}, reset=True)


def test_offline_embedding_is_stable_normalized_and_chinese():
    embedding = LocalTextEmbedding()
    assert embedding(["部门人才数量"]) == embedding(["部门人才数量"])
    assert embedding(["部门人才数量"]) != embedding(["培训时间"])
    assert sum(value * value for value in embedding(["部门人才数量"])[0]) == pytest.approx(1)


@pytest.mark.asyncio
async def test_real_vanna_memory_training_and_generation_without_network(tmp_path, configured):
    from app.ai.core.llm_manager import llm_manager
    from scripts.train_vanna import build_corpus
    configured.setattr(settings, "VANNA_MEMORY_PATH", str(tmp_path / "memory"))
    model = AsyncMock(return_value=SimpleNamespace(content="SELECT education, count(*) AS n FROM dim_talent_wide GROUP BY education"))
    configured.setattr(llm_manager, "chat", model)
    adapter, executor = adapter_for()
    # 使用真实 Vanna/Chroma，仅查询执行与模型结果为离线 fake。
    adapter = VannaAdapter(executor=executor)
    corpus = build_corpus()
    counts = await __import__('asyncio').to_thread(adapter.train, corpus)
    assert counts["examples"] == len(corpus["examples"])
    _vanna_engine.cache_clear()
    fresh = VannaAdapter(executor=executor)
    result = await fresh.ask("学历分布", user_scope=ADMIN, with_insight=False)
    assert result.rows == [["本科", 2]]
    model.assert_awaited_once()
    assert model.call_args.kwargs["purpose"] == "sql"
    assert any("CREATE TABLE" in message["content"] for message in model.call_args.args[0])


@pytest.mark.asyncio
async def test_api_route_and_strict_request_model(client, monkeypatch):
    from tests.conftest import app
    from app.core.dependencies import get_current_user
    from app.routers.analytics_router import get_service
    user = SimpleNamespace(id=1, role_codes=["admin"], is_superuser=False, organization_id=1)
    service = SimpleNamespace(nl2sql_ask=AsyncMock(return_value=NL2SQLResponse(
        question="学历分布", sql="SELECT 1", columns=["n"], rows=[[1]], elapsed_ms=3)))
    original = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_service] = lambda: service
    try:
        response = await client.post("/api/v1/analytics/nl2sql", json={"question": "学历分布", "with_insight": False})
        assert response.status_code == 200
        assert response.json()["data"]["elapsed_ms"] == 3
        assert "trace_id" in response.json()
        for body in ({"question": "   "}, {"question": "x", "max_rows": 1001},
                     {"question": "x", "roles": ["admin"]}, {"question": "x", "sql": "SELECT 1"}):
            assert (await client.post("/api/v1/analytics/nl2sql", json=body)).status_code == 422
        user.role_codes = ["employee"]
        assert (await client.post("/api/v1/analytics/nl2sql", json={"question": "x"})).status_code == 403
        del app.dependency_overrides[get_current_user]
        assert (await client.post("/api/v1/analytics/nl2sql", json={"question": "x"})).status_code == 401
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


@pytest.mark.asyncio
async def test_service_scopes_superuser_and_restores_context(monkeypatch):
    from app.ai.context import get_context
    from app.infrastructure.cache import redis as cache
    from app.services.analytics_service import AnalyticsService
    class _H:
        def get(self): return None
        def peek(self): return None
        async def aclose(self): pass
    monkeypatch.setattr(cache, "_client_holder", _H())
    original = get_context()
    expected = NL2SQLResponse(
        question="x", sql="SELECT count(*) FROM public.dim_talent_wide LIMIT 1",
        columns=["count"], rows=[[2]],
    )
    adapter = SimpleNamespace(ask=AsyncMock(return_value=expected))
    user = SimpleNamespace(id=5, role_codes=[], is_superuser=True, organization_id=8)
    service = AnalyticsService(adapter=adapter)
    result = await service.nl2sql_ask("x", user)
    assert result.question == expected.question
    assert result.sql == expected.sql and result.rows == expected.rows
    assert adapter.ask.call_args.kwargs["user_scope"] == {"user_id": 5, "roles": ["super_admin"], "organization_id": 8}
    assert get_context() is original


@pytest.mark.asyncio
async def test_function_skill_entry_cannot_skip_permission():
    from app.ai.context import context_scope
    from app.ai.skills.sql_skill import query_database
    from app.core.exceptions import BusinessException
    with context_scope():
        with pytest.raises(BusinessException) as error:
            await query_database("学历分布")
        assert error.value.code == 2004 and error.value.status_code == 403


@pytest.mark.asyncio
async def test_readonly_executor_transaction_timeout_and_credentials(configured, monkeypatch):
    from app.infrastructure.database import analytics_query
    from unittest.mock import MagicMock
    class Transaction:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    result = MagicMock()
    result.keys.return_value = ["n"]
    result.fetchmany.return_value = [(2,)]
    connection = SimpleNamespace(execute=AsyncMock(return_value=result), begin=lambda: Transaction())
    class Connection(Transaction):
        async def __aenter__(self): return connection
    engine = SimpleNamespace(connect=lambda: Connection(), dispose=AsyncMock())
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(analytics_query, "create_async_engine", factory)
    columns, rows = await analytics_query.execute_readonly("SELECT count(*) FROM public.dim_talent_wide LIMIT 2", 2)
    assert columns == ["n"] and rows == [[2]]
    calls = connection.execute.call_args_list
    assert str(calls[0].args[0]) == "SET TRANSACTION READ ONLY"
    assert "statement_timeout" in str(calls[1].args[0])
    assert calls[1].args[1] == {"timeout": str(settings.NL2SQL_TIMEOUT_SECONDS * 1000)}
    assert factory.call_args.args[0].username == "test_readonly"
    assert "mysql" not in str(factory.call_args.args[0])
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_readonly_executor_disposes_connection_on_error(configured, monkeypatch):
    from app.infrastructure.database import analytics_query
    from sqlalchemy.exc import SQLAlchemyError
    class FailedConnection:
        async def __aenter__(self): raise SQLAlchemyError("private driver detail")
        async def __aexit__(self, *args): return False
    engine = SimpleNamespace(connect=lambda: FailedConnection(), dispose=AsyncMock())
    monkeypatch.setattr(analytics_query, "create_async_engine", lambda *a, **k: engine)
    with pytest.raises(AIError) as error:
        await analytics_query.execute_readonly("SELECT * FROM public.dim_talent_wide LIMIT 2", 2)
    assert "private driver detail" not in str(error.value)
    engine.dispose.assert_awaited_once()


def test_restricted_schema_qualified_columns_remain_valid_alias_references():
    sql = inject_data_scope("SELECT public.fact_talent_snapshot.organization_id FROM public.fact_talent_snapshot", MANAGER)
    assert "SELECT fact_talent_snapshot.organization_id FROM" in sql
    validate_select_sql(sql)


@pytest.mark.asyncio
async def test_empty_memory_never_calls_model(configured):
    from app.ai.core.llm_manager import llm_manager
    engine = SimpleNamespace(get_training_data=lambda: SimpleNamespace(empty=True),
                             generate_sql=lambda **kw: pytest.fail("空记忆不得生成"))
    adapter = VannaAdapter(engine=engine)
    configured.setattr(llm_manager, "chat", AsyncMock(side_effect=AssertionError("不得调用模型")))
    with pytest.raises(AIError, match="训练语料"):
        await adapter._generate("学历分布", ADMIN)


@pytest.mark.asyncio
async def test_function_skill_preserves_sql_rejection_status(configured):
    from app.ai.context import context_scope
    from app.ai.skills.sql_skill import SqlQuerySkill, query_database
    from app.core.exceptions import BusinessException
    adapter, executor = adapter_for("DELETE FROM dim_talent_wide")
    configured.setattr(SqlQuerySkill, "_load_adapter", lambda self: adapter)
    with context_scope({"id": 1, "roles": ["admin"]}):
        with pytest.raises(BusinessException) as error:
            await query_database("学历分布")
    assert error.value.code == 1002 and error.value.status_code == 400
    executor.assert_not_awaited()
