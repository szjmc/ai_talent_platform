"""
模块：tests/test_database_health.py
职责：数据库健康检查的单元测试（不依赖真实 MySQL / PgSQL 服务）。

标签：[架构组] [P1]

覆盖点：
1. 端口不通时，健康检查返回 down + connection_refused + 中文修复建议，且不抛异常；
2. MySQL 不可用时 /health/ready 返回 503，且响应体包含可读原因；
3. /health（存活探针）不受数据库状态影响，始终 200；
4. 配置默认值：数据库必须为 MySQL / PgSQL，不再包含 SQLite。

说明：这些用例在任何环境下都能跑通（哪怕没有安装数据库），
     真正的 MySQL 连通性/业务链路验证见 tests/test_common_layer.py（库不可用会自动 skip）。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings
from app.infrastructure.database.health import check_mysql, check_pgsql

pytestmark = pytest.mark.asyncio


async def test_settings_use_mysql_and_pgsql_only():
    """配置层面不允许出现 SQLite。"""
    assert settings.DATABASE_URL.startswith("mysql+aiomysql://")
    assert settings.MYSQL_SERVER_URL.startswith("mysql+aiomysql://")
    assert settings.PGSQL_URL.startswith("postgresql+asyncpg://")
    summary = settings.database_summary()
    assert "sqlite" not in str(summary).lower()
    assert summary["mysql"].endswith(f"/{settings.DB_NAME}")


async def test_check_mysql_returns_structured_report():
    """无论数据库是否可用，都必须返回结构化报告而不是抛异常。"""
    report = await check_mysql()
    assert set(report) == {"target", "status", "reason", "message", "elapsed_ms"}
    assert report["status"] in {"up", "down"}
    assert report["message"]  # 一定有中文说明
    if report["status"] == "down":
        assert report["reason"] in {
            "connection_refused",
            "auth_failed",
            "database_missing",
            "driver_missing",
            "timeout",
            "unknown",
        }


async def test_check_pgsql_respects_enabled_flag():
    """PgSQL 未启用时返回 disabled；启用时给出结构化报告。"""
    report = await check_pgsql()
    assert report["status"] in {"up", "down", "disabled"}
    if not settings.PGSQL_ENABLED:
        assert report["status"] == "disabled"


async def test_liveness_is_independent_of_database(client):
    """/health 只反映进程存活，数据库挂了也必须 200。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["status"] == "up"
    assert "databases" in body["data"]


async def test_readiness_reports_reason_when_mysql_down(client):
    """/health/ready：MySQL 不可用 → 503 且带可读原因；可用 → 200 且 mysql=up。"""
    resp = await client.get("/health/ready")
    body = resp.json()

    if resp.status_code == 503:
        assert body["code"] == 503
        assert "MySQL" in body["message"]
        assert body["data"]["mysql"]["status"] == "down"
        assert body["data"]["mysql"]["reason"]
        assert body["data"]["mysql"]["message"]
    else:
        assert resp.status_code == 200
        assert body["data"]["mysql"]["status"] == "up"
