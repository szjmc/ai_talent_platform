"""PgSQL 分析库结构测试：不依赖真实数据库服务。

标签：[架构组]+[Vanna组] [P1]（分析库结构验收，离线）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config.settings import settings
from app.infrastructure.database.analytics_schema import initialize_pgsql, render_pgsql_ddl
from app.models import Base
from app.models.analytics import (
    ANALYTICS_TABLE_NAMES,
    DEFAULT_PARTITIONS,
    PARTITION_KEYS,
    AnalyticsBase,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_analytics_tables_match_design_names():
    assert set(AnalyticsBase.metadata.tables) == {
        f"{settings.PGSQL_SCHEMA}.{name}" for name in ANALYTICS_TABLE_NAMES
    }
    assert len(ANALYTICS_TABLE_NAMES) == 6


def test_analytics_metadata_is_separate_from_mysql():
    assert AnalyticsBase.metadata is not Base.metadata
    assert not set(AnalyticsBase.metadata.tables) & set(Base.metadata.tables)
    assert "users" in Base.metadata.tables
    assert f"{settings.PGSQL_SCHEMA}.dim_talent_wide" in AnalyticsBase.metadata.tables


def test_fact_tables_are_partitioned_by_time_key():
    for name, key in PARTITION_KEYS.items():
        table = AnalyticsBase.metadata.tables[f"{settings.PGSQL_SCHEMA}.{name}"]
        assert table.dialect_options["postgresql"]["partition_by"] == f"RANGE ({key})"
        assert key in table.primary_key.columns
        assert DEFAULT_PARTITIONS[name] == f"{name}_default"


def test_rendered_ddl_is_postgresql_and_contains_all_tables():
    ddl = render_pgsql_ddl()
    for name in ANALYTICS_TABLE_NAMES:
        assert f"CREATE TABLE {settings.PGSQL_SCHEMA}.{name}" in ddl
    for name in DEFAULT_PARTITIONS.values():
        assert f"PARTITION OF {settings.PGSQL_SCHEMA}." in ddl
        assert name in ddl
    assert "JSONB" in ddl
    assert "TEXT[]" in ddl
    assert "PARTITION BY RANGE" in ddl
    assert "AUTO_INCREMENT" not in ddl


def test_script_print_ddl_does_not_require_database():
    result = subprocess.run(
        [sys.executable, "scripts/init_pgsql.py", "--print-ddl"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "fact_talent_snapshot" in result.stdout
    assert "dim_talent_wide" in result.stdout


@pytest.mark.asyncio
async def test_initialize_rejects_disabled_pgsql(monkeypatch):
    monkeypatch.setattr(settings, "PGSQL_ENABLED", False)
    with pytest.raises(RuntimeError, match="PGSQL_ENABLED=False"):
        await initialize_pgsql()
