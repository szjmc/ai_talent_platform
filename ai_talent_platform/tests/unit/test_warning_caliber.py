"""
模块：tests/unit/test_warning_caliber.py
职责：WARN-001 岗位预警统计口径单元测试（伪仓库，无数据库）。

覆盖：
    阈值 > top_k 的口径受限标注 / 正常阈值不标注 /
    有快照无明细时用快照还原（不误报从未匹配）/ 长期空缺改用 publish_at 计时

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：*kwq* 新增文件（WARN-001 验收）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.reserve_warning_service import ReserveWarningService

POSITION_ID = 7


def _position(**overrides):
    data = {
        "id": POSITION_ID,
        "title": "高级后端工程师",
        "publish_at": datetime.now() - timedelta(days=40),
        "created_at": datetime.now() - timedelta(days=60),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _snapshot(output: int, pass_count: int, top_k: int = 10) -> SimpleNamespace:
    items = [
        {"talent_id": 100 + i, "score": 90 - i, "decision": "PASS" if i < pass_count else "NEEDS_REVIEW"}
        for i in range(output)
    ]
    return SimpleNamespace(
        batch_id="b" * 32,
        request_scope={"top_k": top_k, "min_score": 0.0},
        response={
            "batch_id": "b" * 32,
            "position_id": POSITION_ID,
            "stats": {"candidates": 30, "excluded_by_hard": 5, "unknown": 0,
                      "needs_review": output - pass_count, "output": output},
            "items": items,
            "excluded": [],
        },
    )


class _FakeMatchingRepo:
    def __init__(self, snapshot=None, records=None):
        self._snapshot = snapshot
        self._records = records or []

    async def latest_snapshot(self, db, position_id):
        return self._snapshot

    async def latest_by_position(self, db, position_id):
        return self._records


def _service(snapshot=None, records=None) -> ReserveWarningService:
    return ReserveWarningService(
        db=None, matching_repo=_FakeMatchingRepo(snapshot, records)
    )


async def _evaluate(service, threshold: int = 5, vacant_days: int = 30):
    return await service._evaluate(
        _position(),
        shortage_threshold=threshold,
        vacant_deadline=datetime.now() - timedelta(days=vacant_days),
    )


@pytest.mark.asyncio
async def test_threshold_exceeding_output_cap_is_flagged():
    """阈值 15 > 输出上限 10：仍出预警，但必须标注「口径受限」，不得静默误导。"""
    findings = await _evaluate(_service(snapshot=_snapshot(output=8, pass_count=3, top_k=10)), threshold=15)

    assert len(findings) == 1
    finding = findings[0]
    assert finding["warning_type"] == "position_reserve_shortage"
    assert finding["detail"]["threshold_exceeds_cap"] is True
    assert finding["detail"]["output_cap"] == 10
    assert finding["detail"]["pass_count_basis"] == "latest_batch_output"
    assert "超出统计口径" in finding["message"]


@pytest.mark.asyncio
async def test_threshold_within_cap_is_not_flagged():
    """阈值在口径内：正常预警，不出现口径提示。"""
    findings = await _evaluate(_service(snapshot=_snapshot(output=8, pass_count=3, top_k=10)), threshold=5)

    assert findings[0]["detail"]["threshold_exceeds_cap"] is False
    assert "超出统计口径" not in findings[0]["message"]
    assert findings[0]["detail"]["pass_count"] in (0, 3)


@pytest.mark.asyncio
async def test_snapshot_without_records_does_not_report_long_vacant():
    """快照有结果但明细缺失：用快照还原统计，绝不误报「从未执行匹配」。"""
    findings = await _evaluate(_service(snapshot=_snapshot(output=8, pass_count=4), records=[]), threshold=6)

    assert findings, "应产出储备不足预警"
    assert findings[0]["warning_type"] == "position_reserve_shortage"
    assert findings[0]["detail"]["pass_count"] == 4


@pytest.mark.asyncio
async def test_long_vacant_uses_publish_at_basis():
    """从未匹配：计时基准优先 publish_at，并在 detail 中标注。"""
    findings = await _evaluate(_service(snapshot=None, records=[]), vacant_days=30)

    assert findings[0]["warning_type"] == "position_long_vacant"
    assert findings[0]["detail"]["open_basis"] == "publish_at"
    assert findings[0]["detail"]["open_days"] >= 40


@pytest.mark.asyncio
async def test_long_vacant_falls_back_to_created_at():
    """无 publish_at 时回退 created_at，并如实标注基准。"""
    service = _service(snapshot=None, records=[])
    finding = await service._evaluate(
        _position(publish_at=None, created_at=datetime.now() - timedelta(days=45)),
        shortage_threshold=5,
        vacant_deadline=datetime.now() - timedelta(days=30),
    )
    assert finding[0]["detail"]["open_basis"] == "created_at"


@pytest.mark.asyncio
async def test_records_path_pass_count():
    """有明细时按明细 hard_results 聚合 PASS 人数。"""
    records = [
        SimpleNamespace(batch_id="b" * 32, stats={"output": 3, "unknown": 1, "needs_review": 0},
                        hard_results=[{"status": "PASS"}]),
        SimpleNamespace(batch_id="b" * 32, stats={"output": 3, "unknown": 1, "needs_review": 0},
                        hard_results=[{"status": "NEEDS_REVIEW"}]),
        SimpleNamespace(batch_id="b" * 32, stats={"output": 3, "unknown": 1, "needs_review": 0},
                        hard_results=[{"status": "FAIL"}]),
    ]
    findings = await _evaluate(_service(snapshot=None, records=records), threshold=5)

    assert findings[0]["detail"]["pass_count"] == 1
    assert findings[0]["detail"]["output_cap"] is None
    assert findings[0]["detail"]["threshold_exceeds_cap"] is False
