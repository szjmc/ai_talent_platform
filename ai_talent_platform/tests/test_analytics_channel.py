"""
模块：tests/test_analytics_channel.py
职责：分析链路的集成级测试（路由转正 / ETL 脚本 / 研判工作流）。

标签：[Vanna组]+[架构组] [P1]（合并校准：路由转正/ETL/研判工作流）

历史说明（合并校准 2026-11）：
- Vanna 组重构后，validator / adapter / 图表 / 报告 / 统计口径的单元测试
  已迁移到 tests/test_nl2sql.py、test_analytics_statistics.py、
  test_report_generation.py、test_analytics_snapshot.py，此处不再重复；
- 本文件保留三类跨模块测试：路由转正契约、sync_analytics 纯函数与开关、
  analysis_workflow 与 AnalyticsService 的兼容降级行为。

全部用例离线运行：不依赖 MySQL / PgSQL / Redis / vanna / 真实 LLM。
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.ai.workflows import analysis_workflow
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError
from app.routers import router as router_module

ADMIN_SCOPE = {"roles": ["admin"], "organization_id": 1}
MANAGER_SCOPE = {"roles": ["manager"], "organization_id": 9}


# ================================================================ 1) 路由转正
@pytest.mark.asyncio
async def test_analytics_paths_in_openapi(client):
    """analytics 接口全部出现在 OpenAPI（转正后不再是 501 占位）。"""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/api/v1/analytics/distribution",
        "/api/v1/analytics/assessment/summary",
        "/api/v1/analytics/trend",
        "/api/v1/analytics/compare",
        "/api/v1/analytics/nl2sql",
        "/api/v1/analytics/report",
        "/api/v1/analytics/reports",
    ):
        assert path in paths, f"缺少 analytics 接口：{path}"


@pytest.mark.asyncio
async def test_analytics_requires_auth(client):
    """无 token 访问 analytics 接口 -> 401（鉴权前置，与全站契约一致）。"""
    response = await client.get("/api/v1/analytics/distribution")
    assert response.status_code == 401


def test_analytics_removed_from_stub_modules():
    """analytics 必须从 _STUB_MODULES 摘除并列入 IMPLEMENTED_MODULES。"""
    stubs = {prefix for prefix, _, _ in router_module._STUB_MODULES}
    assert "analytics" not in stubs
    assert "analytics" in router_module.IMPLEMENTED_MODULES


# ================================================================ 2) ETL 脚本
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
    rows = sync.build_snapshot_rows(talents, [date(2026, 11, 1)])
    # 源表没有离职时间，纯函数不重建历史离职快照；当前离职者不进入任何近似月份。
    nov_total = next(
        r for r in rows
        if r["snapshot_month"] == date(2026, 11, 1)
        and r["organization_id"] == 0 and r["education"] == "" and r["age_group"] == ""
    )
    assert nov_total["talent_count"] == 1
    # 学历明细行存在（ETL 后可按学历过滤）
    assert any(
        r["organization_id"] == 7 and r["education"] == "本科" and r["talent_count"] == 1
        for r in rows
    )

    wide = sync.build_dim_talent_wide_rows(
        talents, {7: "/1/"}, {1: 88.5, 2: 70.0}, snapshot_month=date(2026, 11, 5)
    )
    assert len(wide) == 1  # 离职者不进宽表
    assert wide[0]["org_path"] == "/1/"  # 与 organizations.path 同口径：只含祖先
    assert wide[0]["skill_tags"] == ["Python", "SQL"]
    assert wide[0]["match_avg_score"] == 88.5

    orgs = sync.build_org_rows(
        [{"id": 7, "code": "RD", "name": "研发", "level": 2, "path": "/1/", "parent_id": 1}]
    )
    assert orgs[0]["path"] == "/1/"
    assert orgs[0]["level"] == 2


# ================================================================ 3) 工作流
class _FakeDistribution:
    def __init__(self, items, warnings=None):
        self.items = items
        self.warnings = warnings or []


class _FakeAnalyticsService:
    """当前 AnalyticsService 签名：distribution(type, filters, user) / trend(query, user)。"""

    def __init__(self, db=None):
        self.db = db

    async def distribution(self, type_, filters, user):
        assert user.id == 1
        if type_ == "education":
            return _FakeDistribution([SimpleNamespace(label="本科", count=40)])
        return _FakeDistribution([SimpleNamespace(label="研发部", count=40)])

    async def assessment_summary(self, filters, user):
        return SimpleNamespace(record_count=3, average_score=76.5, warnings=[])

    async def trend(self, query, user):
        assert query.metric == "talent_total"
        return SimpleNamespace(items=[SimpleNamespace(month=query.end_month, value=40, missing=False)], warnings=[])


@pytest.mark.asyncio
async def test_workflow_four_nodes(monkeypatch):
    monkeypatch.setattr(analysis_workflow, "_analytics_service_cls", _FakeAnalyticsService)
    user = SimpleNamespace(id=1, role_codes=["admin"], is_superuser=False, organization_id=1)
    result = await analysis_workflow.run_analysis_workflow("Q4 研发盘点", user=user)
    assert set(result["nodes"]) == {"collect_metrics", "talent_review", "team_structure", "recommendation"}
    assert result["status"] == "succeeded"
    assert result["nodes"]["collect_metrics"]["metrics"]["overview"]["total_talents"] == 40
    assert result["nodes"]["team_structure"]["org_distribution"] == [{"label": "研发部", "value": 40}]
    assert "Q4 研发盘点" in result["insight"]
    assert result["nodes"]["recommendation"]["actions"]


@pytest.mark.asyncio
async def test_workflow_requires_trusted_user():
    result = await analysis_workflow.run_analysis_workflow("无身份盘点")
    assert result["status"] == "failed"
    assert "登录用户" in result["error"]


@pytest.mark.asyncio
async def test_workflow_degrades_without_fake_zero(monkeypatch):
    class _Missing(_FakeAnalyticsService):
        async def trend(self, query, user):
            return SimpleNamespace(items=[SimpleNamespace(month=query.end_month, value=None, missing=True)],
                                   warnings=["缺期不补0"])

    monkeypatch.setattr(analysis_workflow, "_analytics_service_cls", _Missing)
    user = SimpleNamespace(id=1, role_codes=["admin"], is_superuser=False, organization_id=1)
    result = await analysis_workflow.run_analysis_workflow("降级盘点", user=user)
    assert result["status"] == "partial"
    overview = result["nodes"]["collect_metrics"]["metrics"]["overview"]
    assert overview["total_talents"] is None
    assert overview["match_rate"] is None and overview["training_completion"] is None
    assert result["nodes"]["collect_metrics"]["metrics"]["trend_turnover"] is None
    assert not any(r["type"] == "no_data" for r in result["risks"])
    assert result["nodes"]["recommendation"]["actions"]


def test_workflow_assess_risks_only_uses_real_zero():
    assert analysis_workflow._assess_risks({"overview": {"total_talents": 0}})[0]["type"] == "no_data"
    assert analysis_workflow._assess_risks({"overview": {"total_talents": None}}) == []
    assert analysis_workflow._assess_risks({"overview": {"total_talents": 10}}) == []


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
    """服务构造本身失败 -> 结构化 failed（区别于指标降级的 partial）。"""

    def broken_service(db=None):
        raise BusinessRuleError("依赖未就绪：PgSQL 分析库未启用")

    monkeypatch.setattr(analysis_workflow, "_analytics_service_cls", broken_service)
    user = SimpleNamespace(id=1, role_codes=["admin"], is_superuser=False, organization_id=1)
    result = await analysis_workflow.run_analysis_workflow("x", user=user)
    assert result["status"] == "failed"
    assert "依赖未就绪" in result["error"]
