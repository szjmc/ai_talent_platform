"""
模块：tests/unit/test_gap_service.py
职责：GAP-001 能力差距矩阵单元测试（纯函数，无数据库、无 LLM）。

覆盖：
    全命中 / 部分命中 / 零命中 / 技能字段缺失 / 学历不足 / 优先技能 / 素质缺失
    权重归一化与维度回收 / soft 策略空矩阵 / 旧快照兼容

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：*kwq* 新增文件（GAP-001 验收）。
"""

from __future__ import annotations

import pytest

from app.schemas.matching import HardStatus, MatchItem
from app.schemas.position import JobProfileParsed
from app.services.gap_service import build_gap_matrix
from app.services.matching_rules import judge_hard_conditions

POSITION_ID = 3
TALENT_ID = 12

WEIGHTS = {"skill": 0.4, "experience": 0.3, "education": 0.2, "quality": 0.1}


def _position(**requirements_overrides):
    requirements = {
        "min_education": "本科",
        "min_work_years": 3,
        "required_skills": ["Python", "FastAPI"],
        "preferred_skills": ["MySQL", "Redis"],
    }
    requirements.update(requirements_overrides)
    return {"id": POSITION_ID, "title": "高级后端工程师", "requirements": requirements}


def _talent(**overrides):
    data = {
        "id": TALENT_ID,
        "name": "张三",
        "education": "本科",
        "work_years": 5,
        "skill_tags": ["Python", "FastAPI", "MySQL"],
        "potential_level": "B",
    }
    data.update(overrides)
    return data


def _profile() -> JobProfileParsed:
    return JobProfileParsed(
        job_title="高级后端工程师",
        required_skills=["Python", "FastAPI"],
        preferred_skills=["MySQL", "Redis"],
        min_education="本科",
        min_work_years=3,
        source="manual",
    )


def _hard_results(talent, position):
    return judge_hard_conditions(
        talent, position, position_id=POSITION_ID, talent_id=TALENT_ID
    )


def _by_item(rows):
    return {row.item: row for row in rows}


# ------------------------------------------------------------------ 用例


def test_all_pass_rows_and_weight_sum():
    """全命中：技能逐行 PASS，权重合计 1.0。"""
    talent, position = _talent(), _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    assert matrix.summary["total"] > 0
    assert matrix.summary["fail"] == 0
    assert matrix.summary["unknown"] == 0
    # 优先技能 Redis 未命中为 PARTIAL（部分符合），不算 fail
    assert matrix.summary["pass"] + matrix.summary["partial"] == matrix.summary["total"]

    rows = _by_item(matrix.rows)
    assert rows["Python"].status == "PASS" and rows["Python"].hit is True
    assert rows["FastAPI"].status == "PASS"
    assert rows["min_education"].status == "PASS"
    assert rows["min_work_years"].status == "PASS"
    # 必备技能 + 优先技能行共享 skill 维度权重，行权重和 = skill 维权重
    skill_rows = [r for r in matrix.rows if r.dimension == "skill"]
    assert abs(sum(r.weight for r in skill_rows) - WEIGHTS["skill"]) < 1e-3
    assert abs(sum(r.weight for r in matrix.rows) - 1.0) < 1e-3


def test_partial_hit_marks_missing_as_fail_major():
    """部分命中（all 口径聚合 FAIL 且有命中）：缺失技能 FAIL / major。"""
    talent = _talent(skill_tags=["Python", "MySQL"])
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    fastapi = next(r for r in matrix.rows if r.item == "FastAPI")
    assert fastapi.status == "FAIL"
    assert fastapi.severity == "major"
    assert fastapi.hit is False
    assert fastapi.actual is None
    assert matrix.summary["fail"] >= 1


def test_zero_hit_marks_blocking():
    """零命中：缺失技能 FAIL / blocking。"""
    talent = _talent(skill_tags=["Go", "Rust"])
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    assert matrix.summary["blocking"] >= 2
    for item in ("Python", "FastAPI"):
        row = next(r for r in matrix.rows if r.item == item)
        assert row.status == "FAIL" and row.severity == "blocking"


def test_missing_skill_field_is_unknown_never_fail():
    """技能字段缺失：UNKNOWN / missing_data，绝不推测为不具备。"""
    talent = _talent(skill_tags=None)
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    for item in ("Python", "FastAPI"):
        row = next(r for r in matrix.rows if r.item == item)
        assert row.status == "UNKNOWN"
        assert row.severity == "missing_data"
        assert row.actual is None
    assert matrix.summary["fail"] == 0


def test_education_fail_row_carries_values():
    """学历不足：requirement/actual 保留原值，FAIL / blocking，带证据 ID。"""
    talent = _talent(education="大专")
    position = _position()
    results = _hard_results(talent, position)
    matrix = build_gap_matrix(_profile(), talent, results, WEIGHTS)

    row = next(r for r in matrix.rows if r.item == "min_education")
    assert row.status == "FAIL" and row.severity == "blocking"
    assert row.requirement == "本科" and row.actual == "大专"
    assert row.source_field == "talents.education"
    src = next(r for r in results if r.item == "min_education")
    if src.evidence_id:
        assert row.evidence_ids == [src.evidence_id]


def test_preferred_skills_never_fail():
    """优先技能：命中 PASS / 未命中 PARTIAL，永不 FAIL、不参与淘汰。"""
    talent = _talent(skill_tags=["Python", "FastAPI"])  # 只命中 MySQL 之外的技能
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    preferred_rows = [r for r in matrix.rows if r.item.startswith("优先技能")]
    assert {r.status for r in preferred_rows} <= {"PASS", "PARTIAL"}
    assert all(r.severity != "blocking" for r in preferred_rows)


def test_missing_potential_level_is_unknown():
    """素质维度缺失：UNKNOWN / missing_data，明确不计为能力结论。"""
    talent = _talent(potential_level=None)
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    row = next(r for r in matrix.rows if r.dimension == "quality")
    assert row.status == "UNKNOWN" and row.severity == "missing_data"
    assert row.actual is None


def test_empty_hard_results_returns_empty_matrix():
    """soft 策略无四态明细：返回空矩阵，不平行造判定。"""
    matrix = build_gap_matrix(_profile(), _talent(), [], WEIGHTS)
    assert matrix.rows == []
    assert matrix.summary["total"] == 0


def test_unused_dimension_weight_is_recycled():
    """画像未设定学历要求：无学历行，其余行权重归一化后合计 1.0。"""
    talent, position = _talent(), _position(min_education=None)
    profile = JobProfileParsed(
        job_title="高级后端工程师",
        required_skills=["Python", "FastAPI"],
        min_work_years=3,
        source="manual",
    )
    matrix = build_gap_matrix(profile, talent, _hard_results(talent, position), WEIGHTS)

    assert all(r.item != "min_education" for r in matrix.rows)
    assert abs(sum(r.weight for r in matrix.rows) - 1.0) < 1e-3


def test_old_snapshot_item_without_gap_matrix_is_compatible():
    """旧批次快照无 gap_matrix 键：MatchItem 回放补默认空列表，不报错。"""
    old = {
        "talent_id": TALENT_ID,
        "score": 80.0,
        "dimension_scores": {"skill": 90.0},
    }
    item = MatchItem.model_validate(old)
    assert item.gap_matrix == []


def test_rows_sorted_by_severity_then_weight():
    """排序：blocking 在前，同严重度内权重降序。"""
    talent = _talent(skill_tags=["Go"], education="大专")
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    order = {"blocking": 0, "missing_data": 1, "major": 2, "minor": 3}
    keys = [(order[r.severity], -r.weight) for r in matrix.rows]
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    "overrides, expect_status",
    [
        ({"work_years": 1}, "FAIL"),          # 年限不足
        ({"work_years": None}, "UNKNOWN"),    # 年限缺失（保守口径）
    ],
)
def test_work_years_rows(overrides, expect_status):
    """年限行：不足 FAIL / 缺失 UNKNOWN（0 年保守口径由判定器决定）。"""
    talent = _talent(**overrides)
    position = _position()
    matrix = build_gap_matrix(_profile(), talent, _hard_results(talent, position), WEIGHTS)

    row = next(r for r in matrix.rows if r.item == "min_work_years")
    assert row.status == expect_status


# ============================================================================
# *kwq* 新增（CMP-002）：多人缺口概览（compare_gap_overview 纯函数）。
# ============================================================================
from app.schemas.matching import GapRow  # noqa: E402
from app.services.gap_service import compare_gap_overview  # noqa: E402


def _gap_row(item: str, status: str, severity: str, dimension: str = "skill") -> GapRow:
    return GapRow(
        dimension=dimension, item=item, requirement=item, actual=None, status=status,
        hit=None, weight=0.1, severity=severity, evidence_ids=[], source_field="talents.skill_tags",
        reason=f"{item} 说明",
    )


def test_overview_counts_status_and_severity_separately():
    """状态计数与严重度计数是两套维度，不能互相覆盖。"""
    stats, common = compare_gap_overview({
        11: [_gap_row("Python", "PASS", "minor"), _gap_row("K8s", "FAIL", "blocking")],
    })
    assert stats[0].total == 2
    assert (stats[0].pass_count, stats[0].fail) == (1, 1)
    # *kwq* 修复（QA-001）：PASS 行不计入严重度，minor 只能来自未满足项
    assert (stats[0].blocking, stats[0].minor) == (1, 0)
    # 只有一个人，构不成「共同缺口」
    assert common == []


def test_overview_pass_rows_not_counted_as_severity():
    """符合项不能被算成缺口 —— 这是本轮修复的核心断言。

    场景：5 项符合、0 项不符合。修复前 severity 一律累加，
    会输出「minor=5」，HR 看到「5 项轻微不足」，而实际一项都不缺。
    """
    rows = [_gap_row(f"skill-{i}", "PASS", "minor") for i in range(5)]
    stats, common = compare_gap_overview({11: rows, 12: rows})

    for stat in stats:
        assert stat.pass_count == 5
        assert (stat.blocking, stat.major, stat.minor) == (0, 0, 0)
        assert (stat.fail, stat.partial, stat.unknown) == (0, 0, 0)
    assert common == []


def test_overview_minor_counts_only_unsatisfied_rows():
    """混合场景：只有 PARTIAL/FAIL 行的 minor 才计入，PASS 行的 minor 忽略。"""
    stats, _ = compare_gap_overview({
        11: [
            _gap_row("A", "PASS", "minor"),
            _gap_row("B", "PARTIAL", "minor"),
            _gap_row("C", "FAIL", "minor"),
            _gap_row("D", "PASS", "minor"),
        ],
    })
    assert stats[0].pass_count == 2
    assert stats[0].minor == 2
    assert stats[0].partial == 1 and stats[0].fail == 1


def test_overview_missing_data_is_not_a_gap():
    """数据缺失（UNKNOWN/missing_data）不计入缺口，否则会把「没数据」说成「不行」。"""
    stats, common = compare_gap_overview({
        11: [_gap_row("Go", "UNKNOWN", "missing_data")],
        12: [_gap_row("Go", "UNKNOWN", "missing_data")],
    })
    assert stats[0].unknown == 1 and stats[0].blocking == 0
    assert common == []


def test_overview_all_missing_ranks_first():
    """全员都缺的条目排最前（决策信号最强）。"""
    _, common = compare_gap_overview({
        11: [_gap_row("K8s", "FAIL", "blocking"), _gap_row("Go", "FAIL", "major")],
        12: [_gap_row("K8s", "FAIL", "blocking"), _gap_row("Go", "FAIL", "major")],
        13: [_gap_row("K8s", "FAIL", "blocking")],
    })
    assert common[0].item == "K8s" and common[0].all_missing is True
    # Go 只有 2/3 人缺 → 不是全员，排在后面
    assert common[1].item == "Go" and common[1].all_missing is False
    assert common[1].missing_for == [11, 12]


def test_overview_limits_common_gaps():
    """共同缺口最多返回 10 条，避免一屏放不下的长列表稀释重点。"""
    gaps = [_gap_row(f"skill-{i}", "FAIL", "blocking") for i in range(15)]
    _, common = compare_gap_overview({11: gaps, 12: gaps})
    assert len(common) == 10


def test_overview_empty_matrix_yields_zero_stat():
    """空矩阵仍会给出一条 total=0 的统计（是否展示由调用方决定）。"""
    stats, common = compare_gap_overview({11: [], 12: []})
    assert [s.total for s in stats] == [0, 0]
    assert common == []
