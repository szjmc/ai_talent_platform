"""
模块：tests/test_analytics_channel.py
职责：数据分析通道（analytics）回归测试。

标签：[Vanna组] [P0]（analytics 模块从占位转正后的防回退守门测试）

覆盖范围：
1. 路由转正：6 个 /analytics 接口进入 OpenAPI、无 token 401、_STUB_MODULES 不再含 analytics；
2. SQL 安全：sql_validator 允许 SELECT / 拒绝 DML·注释·系统表·白名单外表 / 强制 LIMIT / 顶层范围注入；
3. NL2SQL 适配器：FakeEngine 全链路（生成->校验->数据范围->只读执行->图表/解读）、
   缺依赖时「依赖未就绪 + Vanna」结构化异常、数据范围 fail-closed；
4. 服务层：概览/分布/趋势的取数与降级注记、参数校验、报告任务生命周期；
5. ETL 脚本：PGSQL 未启用直接失败、纯函数（年龄段/快照/宽表/组织）口径正确；
6. 工作流：四节点结构 + 流失风险规则 + 只经服务层取数（不 import pg_session）。

全部用例离线运行：不依赖 MySQL / PgSQL / Redis / vanna / 真实 LLM。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from app.adapters.nl2sql import vanna_adapter
from app.adapters.nl2sql.sql_validator import (
    inject_data_scope,
    inject_limit,
    validate_select_sql,
)
from app.adapters.nl2sql.vanna_adapter import (
    VannaAdapter,
    build_scope_clause,
    infer_chart,
    is_scope_unrestricted,
)
from app.ai.workflows import analysis_workflow
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, ParamError
from app.routers import router as router_module
from app.schemas.analytics import NL2SQLRequest, ReportRequest
from app.services import analytics_service
from app.services.analytics_service import (
    AnalyticsService,
    reset_report_tasks,
    scope_for_user,
)

ADMIN_SCOPE = {"roles": ["admin"], "organization_id": 1}
MANAGER_SCOPE = {"roles": ["manager"], "organization_id": 9}


# ================================================================ 1) 路由转正
@pytest.mark.asyncio
async def test_analytics_paths_in_openapi(client):
    """6 个 analytics 接口全部出现在 OpenAPI（转正后不再是 501 占位）。"""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/api/v1/analytics/overview",
        "/api/v1/analytics/distribution",
        "/api/v1/analytics/trend",
        "/api/v1/analytics/nl2sql",
        "/api/v1/analytics/report",
        "/api/v1/analytics/reports",
    ):
        assert path in paths, f"缺少 analytics 接口：{path}"


@pytest.mark.asyncio
async def test_analytics_requires_auth(client):
    """无 token 访问 analytics 接口 -> 401（鉴权前置，与全站契约一致）。"""
    response = await client.get("/api/v1/analytics/overview")
    assert response.status_code == 401


def test_analytics_removed_from_stub_modules():
    """analytics 必须从 _STUB_MODULES 摘除并列入 IMPLEMENTED_MODULES。"""
    stubs = {prefix for prefix, _, _ in router_module._STUB_MODULES}
    assert "analytics" not in stubs
    assert "analytics" in router_module.IMPLEMENTED_MODULES


# ================================================================ 2) SQL 安全
def test_validator_allows_readonly_queries():
    assert validate_select_sql(
        "SELECT education, count(*) FROM dim_talent_wide GROUP BY education"
    ).endswith("LIMIT 200")
    with_cte = validate_select_sql(
        "WITH recent AS (SELECT * FROM dim_talent_wide) SELECT * FROM recent"
    )
    assert "LIMIT 200" in with_cte
    # 字符串字面量不参与关键字判定
    ok = validate_select_sql(
        "SELECT * FROM dim_talent_wide WHERE name = 'drop table anything'"
    )
    assert "LIMIT" in ok


@pytest.mark.parametrize(
    "sql,keyword",
    [
        ("DELETE FROM dim_talent_wide", "只读"),
        ("SELECT * FROM dim_talent_wide; DROP TABLE x", "分号"),
        ("SELECT * FROM dim_talent_wide -- hi", "注释"),
        ("SELECT * FROM information_schema.tables", "系统"),
        ("SELECT * FROM pg_stat_activity", "系统"),
        ("SELECT * FROM talents", "白名单"),
        ("", "不能为空"),
    ],
)
def test_validator_rejects_unsafe(sql, keyword):
    with pytest.raises(BusinessRuleError) as exc:
        validate_select_sql(sql)
    assert keyword in exc.value.message


def test_validator_limit_and_scope_injection():
    assert inject_limit("SELECT a FROM dim_talent_wide LIMIT 99999", 200).endswith(
        "LIMIT 200"
    )
    with_where = inject_data_scope(
        "SELECT a FROM dim_talent_wide WHERE x=1", "org_path LIKE '%/9/%'"
    )
    assert "WHERE x=1 AND (org_path LIKE '%/9/%')" in with_where
    no_where = inject_data_scope(
        "SELECT a, count(*) FROM dim_talent_wide GROUP BY a", "org_path LIKE '%/9/%'"
    )
    assert "WHERE (org_path LIKE '%/9/%') GROUP BY a" in no_where
    # 子查询内 WHERE 不动，范围拼在外层
    nested = inject_data_scope(
        "WITH w AS (SELECT * FROM dim_talent_wide WHERE y=2) SELECT * FROM w",
        "org_path LIKE '%/9/%'",
    )
    assert nested.count("org_path LIKE '%/9/%'") == 1
    assert "WHERE y=2) SELECT" in nested


# ================================================================ 3) 适配器
def test_scope_clause_rules():
    assert build_scope_clause("SELECT * FROM dim_talent_wide", ADMIN_SCOPE) == ""
    assert is_scope_unrestricted({"data_scope": "all"})
    wide = build_scope_clause("SELECT * FROM dim_talent_wide", MANAGER_SCOPE)
    assert "org_path LIKE '%/9/%'" in wide
    snap = build_scope_clause("SELECT * FROM fact_talent_snapshot", MANAGER_SCOPE)
    assert "organization_id = 9" in snap
    fact = build_scope_clause("SELECT * FROM fact_training_track", MANAGER_SCOPE)
    assert "talent_id IN (SELECT talent_id FROM dim_talent_wide" in fact
    with pytest.raises(BusinessRuleError):
        build_scope_clause("SELECT * FROM dim_talent_wide", {"roles": ["manager"]})


def test_infer_chart_rules():
    line = infer_chart(
        ["month", "count"],
        [["2026-09", 1], ["2026-10", 2]],
        "逐月趋势",
    )
    assert line["type"] == "line"
    pie = infer_chart(["edu", "n"], [["本科", 60], ["硕士", 40]], "学历占比")
    assert pie["type"] == "pie"
    bar = infer_chart(["edu", "n"], [["本科", 6], ["硕士", 4]], "学历分布")
    assert bar["type"] == "bar"
    radar = infer_chart(
        ["dim", "a", "b", "c"],
        [["x", 1, 2, 3]],
        "三维对比",
    )
    assert radar["type"] == "radar"
    assert infer_chart([], []) is None


class _FakeEngine:
    """Vanna 兼容引擎（generate_sql 返回固定 SQL）。"""

    def __init__(self, sql: str = "SELECT education, COUNT(*) AS n FROM dim_talent_wide GROUP BY education"):
        self.sql = sql
        self.trained: list[dict] = []

    def generate_sql(self, question: str) -> str:
        return self.sql

    def train(self, **kwargs) -> None:
        self.trained.append(kwargs)


class _FakeResult:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows

    def keys(self):
        return self._columns

    def all(self):
        return [tuple(row) for row in self._rows]


class _FakePg:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        return _FakeResult(self.columns, self.rows)


@pytest.fixture
def fake_pg(monkeypatch):
    """把 adapter 的 pg_session 换成返回 FakePg 的异步上下文管理器。"""
    holder: dict = {"pg": _FakePg(["education", "n"], [["本科", 60], ["硕士", 40]])}

    @asynccontextmanager
    async def _session():
        yield holder["pg"]

    monkeypatch.setattr(vanna_adapter, "pg_session", _session)
    VannaAdapter.reset_configured()
    yield holder
    VannaAdapter.reset_configured()


@pytest.mark.asyncio
async def test_adapter_ask_full_chain(fake_pg):
    """问数全链路：FakeEngine -> 校验 -> LIMIT -> 只读执行 -> 图表/解读。"""
    VannaAdapter.configure(_FakeEngine())
    result = await VannaAdapter().ask("学历分布如何", user_scope=ADMIN_SCOPE)
    assert result["row_count"] == 2
    assert result["sql"].endswith("LIMIT 200")
    assert result["chart"]["type"] == "bar"
    assert "共返回 2 行" in result["insight"]
    executed_sql = fake_pg["pg"].executed[0][0]
    assert "dim_talent_wide" in executed_sql


@pytest.mark.asyncio
async def test_adapter_ask_injects_manager_scope(fake_pg):
    """受限身份：SQL 注入 org_path 数据范围。"""
    VannaAdapter.configure(_FakeEngine())
    result = await VannaAdapter().ask("学历分布", user_scope=MANAGER_SCOPE)
    assert "org_path LIKE '%/9/%'" in result["sql"]


@pytest.mark.asyncio
async def test_adapter_ask_missing_dependency():
    """vanna 未安装且未注入引擎 -> 「依赖未就绪 + Vanna」结构化异常。"""
    VannaAdapter.reset_configured()
    with pytest.raises(BusinessRuleError) as exc:
        await VannaAdapter().ask("有多少人", user_scope=ADMIN_SCOPE)
    assert "依赖未就绪" in exc.value.message
    assert "Vanna" in exc.value.message


@pytest.mark.asyncio
async def test_adapter_train_maps_corpus():
    """train 将 corpus 的 ddl/documentation/examples 映射为引擎训练调用。"""
    engine = _FakeEngine()
    VannaAdapter.configure(engine)
    result = VannaAdapter().train(
        {
            "database": "pgsql",
            "ddl": "CREATE TABLE dim_talent_wide (...)",
            "documentation": ["人才宽表按人建模"],
            "examples": [{"question": "学历分布", "sql": "SELECT ..."}],
        }
    )
    assert result["trained"] == 3
    kinds = [next(iter(item)) for item in engine.trained]
    assert kinds == ["ddl", "documentation", "question"]


@pytest.mark.asyncio
async def test_adapter_train_missing_dependency():
    VannaAdapter.reset_configured()
    with pytest.raises(BusinessRuleError) as exc:
        VannaAdapter().train({"ddl": "CREATE TABLE x (id int)"})
    assert "依赖未就绪" in exc.value.message
    assert "Vanna" in exc.value.message


# ================================================================ 4) 服务层
@pytest.fixture
def service_db(monkeypatch):
    """以 SQL 子串分发的假取数层，捕获执行过的 SQL。"""
    captured: dict[str, list[str]] = {
        "pg_rows": [],
        "pg_scalar": [],
        "mysql_rows": [],
        "mysql_scalar": [],
    }

    async def fake_pg_rows(self, sql, params=None):
        captured["pg_rows"].append(sql)
        if "TO_CHAR(snapshot_month" in sql:
            return [
                {"m": "2026-09", "c": 40},
                {"m": "2026-10", "c": 42},
            ]
        if "dim_talent_wide" in sql and "unnest" in sql:
            return [{"label": "Python", "value": 3}]
        if "education" in sql and "dim_talent_wide" in sql:
            return [{"label": "本科", "value": 30}]
        if "age_group" in sql and "fact_talent_snapshot" in sql:
            return [{"label": "30-35", "value": 12}]
        if "dim_organization" in sql:
            return [{"label": "研发部", "value": 10}]
        return []

    async def fake_pg_scalar(self, sql, params=None):
        captured["pg_scalar"].append(sql)
        if "dim_talent_wide" in sql:
            return 50.0
        if "fact_training_track" in sql:
            return 0.8
        if "fact_assessment_history" in sql:
            return 75.0
        return None

    async def fake_mysql_rows(self, sql, params=None):
        captured["mysql_rows"].append(sql)
        return []

    async def fake_mysql_scalar(self, sql, params=None):
        captured["mysql_scalar"].append(sql)
        if "positions" in sql:
            return 3.0
        if "score >= 60" in sql:
            return 0.62
        if "COUNT(*)" in sql and "talents" in sql:
            return 42.0
        return None

    monkeypatch.setattr(AnalyticsService, "_pg_rows", fake_pg_rows)
    monkeypatch.setattr(AnalyticsService, "_pg_scalar", fake_pg_scalar)
    monkeypatch.setattr(AnalyticsService, "_mysql_rows", fake_mysql_rows)
    monkeypatch.setattr(AnalyticsService, "_mysql_scalar", fake_mysql_scalar)
    return captured


@pytest.mark.asyncio
async def test_overview_uses_pgsql_first(service_db):
    overview = await AnalyticsService(None).overview(ADMIN_SCOPE)
    assert overview.total_talents == 50
    assert overview.active_positions == 3
    assert overview.match_rate == pytest.approx(0.62)
    assert overview.training_completion == pytest.approx(0.8)
    assert overview.assessment_avg_score == pytest.approx(75.0)
    assert len(overview.monthly_trend) == 2
    assert overview.data_notes == []


@pytest.mark.asyncio
async def test_overview_restricted_scope_filters(service_db):
    """受限身份：PgSQL 与 MySQL 查询都附加行级部门过滤。"""
    overview = await AnalyticsService(None).overview(MANAGER_SCOPE)
    assert overview.total_talents == 50  # 假数据不区分范围，只断言注入
    assert any("org_path LIKE '%/9/%'" in sql for sql in service_db["pg_scalar"])
    assert any(":scope_org" in sql for sql in service_db["mysql_scalar"])


@pytest.mark.asyncio
async def test_distribution_and_trend(service_db):
    service = AnalyticsService(None)
    education = await service.distribution("education", ADMIN_SCOPE)
    assert education.type == "education"
    assert education.items[0].label == "本科"
    trend = await service.trend("turnover", ADMIN_SCOPE)
    assert trend.unit == "count"
    assert trend.metric == "turnover"
    with pytest.raises(ParamError):
        await service.distribution("salary", ADMIN_SCOPE)
    with pytest.raises(ParamError):
        await service.trend("unknown", ADMIN_SCOPE)


@pytest.mark.asyncio
async def test_nl2sql_delegates_to_adapter(monkeypatch):
    async def fake_ask(self, question, user_scope=None, max_rows=200):
        return {
            "question": question,
            "sql": "SELECT 1 LIMIT 10",
            "columns": ["x"],
            "rows": [[1]],
            "row_count": 1,
            "chart": None,
            "insight": "ok",
            "elapsed_ms": 1.0,
        }

    monkeypatch.setattr(VannaAdapter, "ask", fake_ask)
    response = await AnalyticsService(None).nl2sql(
        NL2SQLRequest(question="有多少人"), ADMIN_SCOPE
    )
    assert response.row_count == 1
    assert response.question == "有多少人"


@pytest.mark.asyncio
async def test_report_task_lifecycle(monkeypatch):
    """报告任务：创建 -> 执行（注入素材与落盘）-> 状态 succeeded。"""
    reset_report_tasks()

    async def fake_collect(self, request, scope):
        return {"overview_error": "离线无数据"}

    def fake_write(task_id, request, markdown):
        return (f"reports/2026/11/{task_id}.md", "md")

    monkeypatch.setattr(AnalyticsService, "_collect_report_pieces", fake_collect)
    monkeypatch.setattr(AnalyticsService, "_write_report_file", staticmethod(fake_write))

    service = AnalyticsService(None)
    task = await service.generate_report(ReportRequest(report_type="monthly"), ADMIN_SCOPE)
    assert task.status in {"pending", "running", "succeeded"}
    finished = await service.wait_for_report(task.task_id, timeout=5)
    assert finished.status == "succeeded"
    assert finished.url and finished.url.endswith(".md")
    assert finished.actual_format == "md"
    listed = service.reports()
    assert any(item.task_id == task.task_id for item in listed)
    reset_report_tasks()


@pytest.mark.asyncio
async def test_report_custom_requires_date_range():
    with pytest.raises(ParamError):
        await AnalyticsService(None).generate_report(
            ReportRequest(report_type="custom", date_range=None), ADMIN_SCOPE
        )


def test_scope_for_user_none_and_stub():
    assert scope_for_user(None) == {}
    fake_user = SimpleNamespace(id=7, role_codes=["manager"], organization_id=9)
    assert scope_for_user(fake_user) == {  # type: ignore[arg-type]
        "user_id": 7,
        "roles": ["manager"],
        "organization_id": 9,
    }


# ================================================================ 5) ETL 脚本
def test_sync_all_requires_pgsql_enabled(monkeypatch):
    import scripts.sync_analytics as sync

    monkeypatch.setattr(settings, "PGSQL_ENABLED", False)
    with pytest.raises(RuntimeError, match="PGSQL_ENABLED"):
        asyncio.run(sync.sync_all())


def test_sync_pure_builders():
    import scripts.sync_analytics as sync

    assert sync.age_group_label(date(2004, 1, 1), ref=date(2026, 11, 5)) == "<25"
    assert sync.age_group_label(date(1980, 6, 1), ref=date(2026, 11, 5)) == "40+"
    assert sync.age_group_label(None) == ""

    months = list(sync.month_iter(date(2026, 10, 1), date(2027, 1, 15)))
    assert months == [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]

    talents = [
        {
            "id": 1,
            "organization_id": 7,
            "education": "本科",
            "birth_date": date(1995, 1, 1),
            "status": "active",
            "created_at": date(2024, 1, 10),
            "updated_at": date(2026, 1, 1),
            "work_years": 5,
            "skill_tags": '["Python", "SQL"]',
            "talent_no": "T1",
            "name": "甲",
            "potential_level": "high",
        },
        {
            "id": 2,
            "organization_id": 7,
            "education": "硕士",
            "birth_date": date(1998, 1, 1),
            "status": "left",
            "created_at": date(2024, 1, 10),
            "updated_at": date(2026, 10, 5),  # 离职月 2026-10
            "work_years": 3,
            "skill_tags": None,
            "talent_no": "T2",
            "name": "乙",
            "potential_level": None,
        },
    ]
    rows = sync.build_snapshot_rows(talents, [date(2026, 9, 1), date(2026, 11, 1)])
    # 2026-09：两人都在；2026-11：离职者已被移出
    sep_total = next(
        r for r in rows
        if r["snapshot_month"] == date(2026, 9, 1)
        and r["organization_id"] == 0 and r["education"] == "" and r["age_group"] == ""
    )
    nov_total = next(
        r for r in rows
        if r["snapshot_month"] == date(2026, 11, 1)
        and r["organization_id"] == 0 and r["education"] == "" and r["age_group"] == ""
    )
    assert sep_total["talent_count"] == 2
    assert nov_total["talent_count"] == 1
    # 学历明细行存在（ETL 后可按学历过滤）
    assert any(
        r["organization_id"] == 7 and r["education"] == "本科" and r["talent_count"] == 1
        for r in rows
    )

    wide = sync.build_dim_talent_wide_rows(
        talents, {7: "/1/7/"}, {1: 88.5, 2: 70.0}, snapshot_month=date(2026, 11, 5)
    )
    assert len(wide) == 1  # 离职者不进宽表
    assert wide[0]["org_path"] == "/1/7/"
    assert wide[0]["skill_tags"] == ["Python", "SQL"]
    assert wide[0]["match_score"] == 88.5

    orgs = sync.build_org_rows(
        [{"id": 7, "code": "RD", "name": "研发", "level": 2, "path": "/1/7/", "parent_id": 1}]
    )
    assert orgs[0]["org_path"] == "/1/7/"
    assert orgs[0]["org_level"] == 2


# ================================================================ 6) 工作流
@pytest.mark.asyncio
async def test_workflow_four_nodes(monkeypatch):
    """四节点流水线结构 + 风险 + 洞察（服务层假数据，离线）。"""
    from app.schemas.analytics import (
        DashboardOverview,
        DistributionItem,
        DistributionResponse,
        MonthlyTrendPoint,
        TrendPoint,
        TrendResponse,
    )

    async def fake_overview(self, scope=None):
        return DashboardOverview(
            total_talents=100,
            active_positions=5,
            match_rate=0.7,
            training_completion=0.8,
            assessment_avg_score=76.5,
            monthly_trend=[MonthlyTrendPoint(month="2026-10", value=100)],
        )

    async def fake_distribution(self, dist_type, scope=None):
        return DistributionResponse(
            type=dist_type,  # type: ignore[arg-type]
            items=[DistributionItem(label="研发部", value=40)],
        )

    async def fake_trend(self, metric, scope=None, granularity="month"):
        return TrendResponse(
            metric=metric,  # type: ignore[arg-type]
            points=[
                TrendPoint(month="2026-09", value=1),
                TrendPoint(month="2026-10", value=4),
            ],
            note="离职上升",
        )

    monkeypatch.setattr(AnalyticsService, "overview", fake_overview)
    monkeypatch.setattr(AnalyticsService, "distribution", fake_distribution)
    monkeypatch.setattr(AnalyticsService, "trend", fake_trend)

    result = await analysis_workflow.run_analysis_workflow("Q4 研发盘点")
    assert set(result["nodes"]) == {
        "collect_metrics",
        "talent_review",
        "team_structure",
        "recommendation",
    }
    assert result["status"] == "succeeded"
    assert any(r["type"] == "turnover_rising" for r in result["risks"])
    assert "Q4 研发盘点" in result["insight"]
    assert result["nodes"]["recommendation"]["actions"]


def test_workflow_assess_risks_rules():
    risks = analysis_workflow._assess_risks(
        {
            "overview": {"training_completion": 0.3, "match_rate": 0.2, "total_talents": 0},
            "trend_turnover": {"points": [{"month": "a", "value": 1}, {"month": "b", "value": 5}]},
        }
    )
    kinds = {r["type"] for r in risks}
    assert {"turnover_rising", "training_low", "match_low", "no_data"} <= kinds
    assert analysis_workflow._assess_risks(
        {
            "overview": {"training_completion": 0.9, "match_rate": 0.8, "total_talents": 10},
            "trend_turnover": {"points": []},
        }
    ) == []


def test_workflow_collect_metrics_never_touches_pg_session():
    """collect_metrics 只允许经 AnalyticsService 取数：禁止 import pg_session / 裸 SQL。"""
    source = (analysis_workflow.__file__)
    assert source.endswith("analysis_workflow.py")
    text = open(source, encoding="utf-8").read()
    # 模块级不允许出现分析库会话与 SQL 执行入口（docstring 提及不算：只查 import）
    assert "from app.infrastructure.database.postgres import" not in text
    assert "from sqlalchemy import" not in text
    # 唯一取数入口是 AnalyticsService
    assert "AnalyticsService" in text


@pytest.mark.asyncio
async def test_workflow_collect_failure_returns_failed_status(monkeypatch):
    async def broken_overview(self, scope=None):
        raise BusinessRuleError("依赖未就绪：PgSQL 分析库未启用")

    monkeypatch.setattr(AnalyticsService, "overview", broken_overview)
    result = await analysis_workflow.run_analysis_workflow("x")
    assert result["status"] == "failed"
    assert "依赖未就绪" in result["error"]


# ================================================================ 7) 清理
@pytest.fixture(autouse=True)
def _clean_report_registry():
    reset_report_tasks()
    yield
    reset_report_tasks()
    VannaAdapter.reset_configured()
