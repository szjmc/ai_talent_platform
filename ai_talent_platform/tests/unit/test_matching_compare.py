"""
模块：tests/unit/test_matching_compare.py
职责：CMP-001 多候选人横向对比单元测试（伪仓库，无数据库）。

覆盖：字段一致性 / 去重 / 2~8 边界 / 缺失人才 notes / 批次不存在 / 空批次 /
      include_gap 开关 / 排序

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：*kwq* 新增文件（CMP-001 验收）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.schemas.matching import MatchCompareRequest
from app.services.matching_service import MatchingService

BATCH_ID = "0123456789abcdef0123456789abcdef"


def _item(talent_id: int, rank: int, score: float, decision: str = "PASS") -> dict:
    return {
        "talent_id": talent_id,
        "talent_name": f"候选人{talent_id}",
        "rank": rank,
        "decision": decision,
        "score": score,
        "dimension_scores": {"skill": score, "experience": 70.0, "education": 80.0, "quality": 60.0},
        "defaulted_dimensions": ["quality"],
        "data_completeness": 0.75,
        "matched_skills": ["Python"],
        "highlights": ["技能匹配度高"],
        "gaps": ["缺少大规模项目经验"],
        "hard_results": [
            {"item": "min_education", "required": "本科", "actual": "本科", "status": "PASS", "reason": "满足"}
        ],
        "suggestion": "建议进入初面",
        "explanation_source": "rule_fallback",
        "gap_matrix": [
            {
                "dimension": "skill",
                "item": "Python",
                "requirement": "Python",
                "actual": "Python",
                "status": "PASS",
                "hit": True,
                "weight": 0.2,
                "severity": "minor",
                "evidence_ids": ["EVID-1-2-1"],
                "source_field": "talents.skill_tags",
                "reason": "命中必备技能「Python」",
            }
        ],
    }


def _snapshot(items: list[dict], strategy: str = "hybrid") -> dict:
    return {
        "batch_id": BATCH_ID,
        "position_id": 7,
        "position_title": "高级后端工程师",
        "requirement_version": 3,
        "recall_mode": "sql_fallback",
        "matched_at": "2026-11-05T10:00:00",
        "stats": {"candidates": 10, "excluded_by_hard": 2, "unknown": 0, "needs_review": 0, "output": len(items)},
        "items": items,
        "excluded": [],
        "request_scope": {"strategy": strategy, "top_k": 20, "min_score": 0},
    }


class _FakeRepo:
    def __init__(self, response: dict | None):
        self._response = response

    async def snapshot_by_id(self, db, batch_id: str):
        if self._response is None:
            return None
        scope = {k: v for k, v in self._response.items() if k == "request_scope"}
        return SimpleNamespace(
            batch_id=batch_id,
            response={k: v for k, v in self._response.items() if k != "request_scope"},
            request_scope=scope.get("request_scope"),
        )


class _FakeReviewRepo:
    """复核仓储替身：记录调用次数，用来断言「批量查询没有退化成 N+1」。"""

    def __init__(self, records: list | None = None):
        self._records = records or []
        self.calls = 0

    async def list_by_batch_talents(self, db, *, batch_id: str, talent_ids: list[int]):
        self.calls += 1
        keep = {int(t) for t in talent_ids}
        return [r for r in self._records if int(r.talent_id) in keep and r.batch_id == batch_id]


def _review(talent_id: int, status: str = "shortlisted", **kw) -> SimpleNamespace:
    base = dict(
        id=talent_id * 10,
        batch_id=BATCH_ID,
        position_id=7,
        talent_id=talent_id,
        review_status=status,
        reason_code=None,
        note=None,
        decided_by=101,
        decided_at=None,
        version=2,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _service(response: dict | None, reviews: list | None = None) -> MatchingService:
    return MatchingService(
        db=None, repo=_FakeRepo(response), review_repo=_FakeReviewRepo(reviews)
    )


def _req(talent_ids, include_gap: bool = False) -> MatchCompareRequest:
    return MatchCompareRequest(batch_id=BATCH_ID, talent_ids=talent_ids, include_gap=include_gap)


@pytest.mark.asyncio
async def test_compare_fields_match_snapshot():
    """对比行字段与快照原值完全一致（不重算、不加工）。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0), _item(13, 3, 70.0, "NEEDS_REVIEW")]
    response = await _service(_snapshot(items)).compare(_req([11, 12, 13]))

    assert response.batch_id == BATCH_ID
    assert response.position_id == 7
    assert response.requirement_version == 3
    assert len(response.rows) == 3

    first = response.rows[0]
    assert (first.talent_id, first.rank, first.score) == (11, 1, 91.5)
    assert first.dimension_scores == items[0]["dimension_scores"]
    assert first.data_completeness == 0.75
    assert first.hard_results[0].status.value == "PASS"
    assert first.explanation_source == "rule_fallback"
    assert response.rows[-1].decision.value == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_compare_dedupes_and_sorts_by_rank():
    """重复 ID 自动去重；输出按原名次升序。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    response = await _service(_snapshot(items)).compare(_req([12, 11, 12]))

    assert [row.talent_id for row in response.rows] == [11, 12]


def test_compare_requires_two_unique_talents():
    """去重后不足 2 人：请求校验直接拒绝。"""
    with pytest.raises(ValidationError):
        MatchCompareRequest(batch_id=BATCH_ID, talent_ids=[5, 5])


def test_compare_rejects_more_than_eight():
    """超过 8 人：请求校验直接拒绝。"""
    with pytest.raises(ValidationError):
        MatchCompareRequest(batch_id=BATCH_ID, talent_ids=list(range(1, 10)))


@pytest.mark.asyncio
async def test_compare_missing_talent_goes_to_notes():
    """快照中不存在的人才进 notes 点名，不静默丢弃，其余正常返回。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    response = await _service(_snapshot(items)).compare(_req([11, 12, 99]))

    assert [row.talent_id for row in response.rows] == [11, 12]
    assert len(response.notes) == 1 and "99" in response.notes[0]


@pytest.mark.asyncio
async def test_compare_all_missing_raises():
    """所选人才全部不在批次中：明确业务错误，不返回空壳。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    with pytest.raises(BusinessRuleError):
        await _service(_snapshot(items)).compare(_req([98, 99]))


@pytest.mark.asyncio
async def test_compare_unknown_batch_raises_not_found():
    """批次不存在：404 语义。"""
    with pytest.raises(NotFoundError):
        await _service(None).compare(_req([11, 12]))


@pytest.mark.asyncio
async def test_compare_zero_output_batch_raises():
    """零输出批次：无法对比，明确业务错误。"""
    with pytest.raises(BusinessRuleError):
        await _service(_snapshot([])).compare(_req([11, 12]))


@pytest.mark.asyncio
async def test_include_gap_switch():
    """include_gap=false 时不返回矩阵；true 时原样带出快照矩阵。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]

    plain = await _service(_snapshot(items)).compare(_req([11, 12]))
    assert all(row.gap_matrix == [] for row in plain.rows)

    with_gap = await _service(_snapshot(items)).compare(_req([11, 12], include_gap=True))
    assert with_gap.rows[0].gap_matrix[0].item == "Python"
    assert with_gap.rows[0].gap_matrix[0].evidence_ids == ["EVID-1-2-1"]


@pytest.mark.asyncio
async def test_compare_does_not_create_batch():
    """对比只读：不调用任何写入方法（伪仓库无写入方法，能跑通即为只读）。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    service = _service(_snapshot(items))
    response = await service.compare(_req([11, 12]))
    assert response.rows and len(response.rows) == 2


# ============================================================================
# *kwq* 新增（CMP-002）：HR 复核状态整合 + 缺口横向概览。
# ============================================================================
def _gap(item: str, status: str, severity: str, dimension: str = "skill") -> dict:
    return {
        "dimension": dimension,
        "item": item,
        "requirement": item,
        "actual": None,
        "status": status,
        "hit": None,
        "weight": 0.1,
        "severity": severity,
        "evidence_ids": [],
        "source_field": "talents.skill_tags",
        "reason": f"{item} 相关说明",
    }


def _item_with_gaps(talent_id: int, rank: int, score: float, gaps: list[dict]) -> dict:
    item = _item(talent_id, rank, score)
    item["gap_matrix"] = gaps
    return item


@pytest.mark.asyncio
async def test_compare_includes_review_status():
    """复核状态随对比行返回，且字段原样透传（不推断、不加工）。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    review = _review(11, status="interviewing", reason_code=None, note="初面通过")
    response = await _service(_snapshot(items), reviews=[review]).compare(_req([11, 12]))

    first, second = response.rows
    assert first.review is not None
    assert first.review.review_status == "interviewing"
    assert first.review.status_label == "面试中"
    assert first.review.note == "初面通过"
    assert first.review.version == 2
    # 未建档的人才必须是 None，不能伪造成「待复核」——两者含义完全不同
    assert second.review is None


@pytest.mark.asyncio
async def test_compare_fetches_reviews_in_one_query():
    """8 人对比只查一次复核表：断言批量查询没有退化成 N+1。"""
    items = [_item(tid, i + 1, 90.0 - i) for i, tid in enumerate(range(11, 19))]
    reviews = [_review(tid) for tid in range(11, 19)]
    service = _service(_snapshot(items), reviews=reviews)
    response = await service.compare(_req(list(range(11, 19))))

    assert len(response.rows) == 8
    assert all(row.review is not None for row in response.rows)
    assert service.review_repo.calls == 1


@pytest.mark.asyncio
async def test_compare_review_not_leaked_across_batches():
    """别的批次的复核记录不能串进来（按 batch_id 过滤）。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    foreign = _review(11, status="hired")
    foreign.batch_id = "f" * 32
    response = await _service(_snapshot(items), reviews=[foreign]).compare(_req([11, 12]))
    assert all(row.review is None for row in response.rows)


@pytest.mark.asyncio
async def test_compare_gap_overview_counts_per_talent():
    """每人缺口分布：状态计数与严重度计数分别统计，不混算。"""
    items = [
        _item_with_gaps(11, 1, 91.0, [
            _gap("Python", "PASS", "minor"),
            _gap("K8s", "FAIL", "blocking"),
        ]),
        _item_with_gaps(12, 2, 88.0, [
            _gap("Python", "PASS", "minor"),
            _gap("K8s", "PARTIAL", "major"),
            _gap("Go", "UNKNOWN", "missing_data"),
        ]),
    ]
    response = await _service(_snapshot(items)).compare(_req([11, 12]))

    stats = {stat.talent_id: stat for stat in response.gap_stats}
    assert stats[11].total == 2 and stats[11].pass_count == 1
    assert stats[11].fail == 1 and stats[11].blocking == 1
    assert stats[12].partial == 1 and stats[12].major == 1
    # 数据缺失单独计数，不能算成缺口
    assert stats[12].unknown == 1 and stats[12].missing_data == 1
    assert stats[12].fail == 0


@pytest.mark.asyncio
async def test_compare_common_gaps_marks_all_missing():
    """共同缺口：全员都缺的排最前并标 all_missing；只有个别人缺的不进列表。"""
    items = [
        _item_with_gaps(11, 1, 91.0, [_gap("K8s", "FAIL", "blocking"), _gap("Go", "FAIL", "major")]),
        _item_with_gaps(12, 2, 88.0, [_gap("K8s", "FAIL", "blocking")]),
    ]
    response = await _service(_snapshot(items)).compare(_req([11, 12]))

    assert len(response.common_gaps) == 1
    shared = response.common_gaps[0]
    assert shared.item == "K8s"
    assert shared.all_missing is True
    assert shared.missing_for == [11, 12]


@pytest.mark.asyncio
async def test_compare_gap_overview_available_without_detail():
    """include_gap=false 时明细为空，但概览仍可用（避免前端拿不到任何缺口信息）。"""
    items = [
        _item_with_gaps(11, 1, 91.0, [_gap("K8s", "FAIL", "blocking")]),
        _item_with_gaps(12, 2, 88.0, [_gap("K8s", "FAIL", "blocking")]),
    ]
    response = await _service(_snapshot(items)).compare(_req([11, 12], include_gap=False))

    assert all(row.gap_matrix == [] for row in response.rows)
    assert sum(stat.blocking for stat in response.gap_stats) == 2
    assert response.gap_note and "include_gap" in response.gap_note


@pytest.mark.asyncio
async def test_compare_gap_note_when_matrix_absent():
    """历史批次（矩阵功能上线前生成）没有矩阵：概览为空但给出明确说明，不报错。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    for item in items:
        item["gap_matrix"] = []
    response = await _service(_snapshot(items)).compare(_req([11, 12]))

    assert response.gap_stats == []
    assert response.common_gaps == []
    assert response.gap_note and "未生成" in response.gap_note


# ============================================================================
# *kwq* 新增（QA-002）：矩阵为空的三种原因要能区分，不能一律说「未生成」。
# ============================================================================
@pytest.mark.asyncio
async def test_gap_note_soft_strategy_explains_no_hard_conditions():
    """soft 策略：本就不做硬条件判定，提示语必须说清楚，而不是甩锅「未生成」。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    for item in items:
        item["gap_matrix"] = []
        item["hard_results"] = []          # soft 策略没有硬条件明细
    response = await _service(_snapshot(items, strategy="soft")).compare(_req([11, 12]))

    assert response.gap_stats == [] and response.common_gaps == []
    assert "soft" in (response.gap_note or "")
    assert "不产出能力差距矩阵" in (response.gap_note or "")
    # 不能误导成「数据缺失 / 历史批次」
    assert "未生成" not in (response.gap_note or "")


@pytest.mark.asyncio
async def test_gap_note_historical_batch_when_hard_results_present():
    """历史批次：有硬条件明细却没有矩阵 → 说明是功能上线前生成的，建议重跑。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    for item in items:
        item["gap_matrix"] = []
    response = await _service(_snapshot(items, strategy="hybrid")).compare(_req([11, 12]))

    assert "未生成" in (response.gap_note or "")
    assert "重跑" in (response.gap_note or "")


@pytest.mark.asyncio
async def test_gap_note_absent_scope_falls_back_to_historical():
    """旧快照没有 request_scope（功能上线前的批次）：无策略信息时按历史批次说明。"""
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    for item in items:
        item["gap_matrix"] = []
    snapshot = _snapshot(items)
    snapshot.pop("request_scope")
    response = await _service(snapshot).compare(_req([11, 12]))

    assert "未生成" in (response.gap_note or "")
