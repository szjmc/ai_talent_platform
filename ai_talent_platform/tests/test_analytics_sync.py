"""
模块：tests/test_analytics_sync.py
职责：分析库同步（MySQL -> PgSQL）与进程内调度器的离线单测。

标签：[架构组] [P1]（同步自动化防回退：分块、水位、差集清理、锁与重试）

覆盖范围：
1. 窗口计算：默认 12 个月 / 传入水位后收敛到水位月初；
2. 批量写入：超过 CHUNK_SIZE 自动分块（防 asyncpg 32767 参数上限）；
3. 差集清理：临时表 + NOT IN 删除源端已消失的行；
4. 水位表 SQL：只有成功才推进 last_synced_at（失败保留旧水位）；
5. 调度器：未启用时不启动、Redis/进程内锁互斥、失败重试次数。

全部用例离线运行：不连接 MySQL / PgSQL / Redis，也不安装 apscheduler。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.infrastructure import scheduler


class FakePG:
    """最小 PgSQL 会话替身：记录 SQL 与参数，返回固定 rowcount。"""

    def __init__(self, rowcount: int = 3) -> None:
        self.calls: list[tuple[str, object]] = []
        self.rowcount = rowcount
        self.committed = False

    async def execute(self, statement, params=None):  # noqa: ANN001
        self.calls.append((str(statement), params))
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self) -> None:
        self.committed = True


# ---------------------------------------------------------------- 1) 窗口计算
def test_window_bounds_default_is_full_history_window():
    start, end = scheduler_job_module()._window_bounds()
    assert start.day == 1
    assert end >= date.today()
    assert (date.today().year - start.year) * 12 + (date.today().month - start.month) >= 11


def test_window_bounds_narrows_to_watermark_month():
    sync = scheduler_job_module()
    watermark = date(2026, 10, 17)
    start, _ = sync._window_bounds(watermark)
    assert start == date(2026, 10, 1)


def test_window_bounds_ignores_future_or_too_old_watermark():
    sync = scheduler_job_module()
    default_start, _ = sync._window_bounds()
    # 水位早于默认窗口起点时不放大范围
    start, _ = sync._window_bounds(date(2000, 1, 1))
    assert start == default_start


# ---------------------------------------------------------------- 2) 分块写入
async def test_exec_many_splits_into_chunks():
    sync = scheduler_job_module()
    pg = FakePG()
    rows = [{"id": i} for i in range(sync.CHUNK_SIZE * 2 + 1)]

    written = await sync._exec_many(pg, "INSERT INTO t (id) VALUES (:id)", rows)

    assert written == len(rows)
    assert [len(params) for _, params in pg.calls] == [
        sync.CHUNK_SIZE,
        sync.CHUNK_SIZE,
        1,
    ]


async def test_exec_many_skips_empty_rows():
    sync = scheduler_job_module()
    pg = FakePG()
    assert await sync._exec_many(pg, "INSERT INTO t (id) VALUES (:id)", []) == 0
    assert pg.calls == []


# ---------------------------------------------------------------- 3) 差集清理
async def test_delete_missing_uses_temp_table_and_returns_rowcount():
    sync = scheduler_job_module()
    pg = FakePG(rowcount=7)

    deleted = await sync._delete_missing(pg, "dim_talent_wide", "talent_id", [1, 2, 3])

    assert deleted == 7
    sql_texts = [sql for sql, _ in pg.calls]
    assert "CREATE TEMP TABLE IF NOT EXISTS tmp_sync_ids" in sql_texts[0]
    assert sql_texts[1] == "TRUNCATE tmp_sync_ids"
    assert any("INSERT INTO tmp_sync_ids" in s for s in sql_texts)
    assert "DELETE FROM dim_talent_wide WHERE talent_id NOT IN (SELECT id FROM tmp_sync_ids)" in sql_texts[-1]


# ---------------------------------------------------------------- 4) 水位 SQL
def test_state_upsert_keeps_old_watermark_when_null():
    sync = scheduler_job_module()
    assert "COALESCE(EXCLUDED.last_synced_at" in sync._UPSERT_STATE
    assert "analytics_sync_state" in sync._DDL_STATE


# ---------------------------------------------------------------- 5) 调度器
async def test_job_skips_when_process_lock_held(monkeypatch):
    monkeypatch.setattr(settings, "PGSQL_ENABLED", True)
    monkeypatch.setattr(
        "app.infrastructure.cache.redis.get_redis", lambda: None, raising=True
    )

    await scheduler._process_lock.acquire()
    try:
        assert await scheduler.run_analytics_sync_job() is None
    finally:
        scheduler._process_lock.release()


def test_scheduler_not_started_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ANALYTICS_SYNC_ENABLED", False)
    assert scheduler.start_scheduler() is None


async def test_retry_uses_given_attempts(monkeypatch):
    attempts: list[int] = []

    async def boom():
        attempts.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler, "_load_sync_all", lambda: boom)
    monkeypatch.setattr(scheduler.asyncio, "sleep", _no_sleep)

    with pytest.raises(RuntimeError, match="db down"):
        await scheduler._sync_with_retry(3)
    assert len(attempts) == 3


async def _no_sleep(_seconds: float) -> None:
    return None


def scheduler_job_module():
    """ETL 脚本模块（延迟导入，避免测试收集期执行 sys.path 操作）。"""
    import scripts.sync_analytics as sync

    return sync
