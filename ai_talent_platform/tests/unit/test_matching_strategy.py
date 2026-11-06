"""
模块：tests/unit/test_matching_strategy.py
职责：MATCH-003 硬过滤策略修复的单元测试 + 原有策略回归。

覆盖任务书场景：
    UNKNOWN 人才不进入 excluded / FAIL 人才记录明确淘汰原因
    原有 hard、soft、hybrid 策略回归通过（soft 仍复用 compute_match_score）

运行：pytest tests/unit --confcutdir=tests/unit -q

标签：[Agent组] [P1]（匹配策略单元用例，MATCH-003）
"""

from __future__ import annotations

import pytest

from app.ai.skills.matching_skill import compute_match_score
from app.patterns.strategy import (
    HardFilterStrategy,
    HybridStrategy,
    MatchStrategyFactory,
    SoftMatchStrategy,
)
from app.services.matching_rules import HardRuleConfig

POSITION = {
    "id": 3,
    "title": "高级后端工程师",
    "requirements": {
        "min_education": "本科",
        "min_work_years": 3,
        "required_skills": ["Python", "FastAPI"],
    },
    "skill_tags": ["Python", "FastAPI", "MySQL"],
}

# 完整档案：应 PASS 并排第 1
CANDIDATE_COMPLETE = {
    "id": 12,
    "name": "张三",
    "education": "本科",
    "work_years": 5,
    "skill_tags": ["Python", "FastAPI", "MySQL"],
    "potential_level": "B",
}

# 学历缺失：应 UNKNOWN，保留且降权（原实现会被直接淘汰 —— 本次修复的核心）
CANDIDATE_MISSING_EDU = {
    "id": 13,
    "name": "李四",
    "education": None,
    "work_years": 5,
    "skill_tags": ["Python", "FastAPI", "MySQL"],
    "potential_level": "B",
}

# 学历明确不达标：应 FAIL 并进入 excluded
CANDIDATE_FAIL = {
    "id": 31,
    "name": "王五",
    "education": "大专",
    "work_years": 2,
    "skill_tags": ["Java"],
    "potential_level": "C",
}

CANDIDATES = [CANDIDATE_COMPLETE, CANDIDATE_MISSING_EDU, CANDIDATE_FAIL]


@pytest.mark.asyncio
async def test_hard_keeps_unknown_candidate():
    """*kwq* 核心回归：学历缺失的人才必须被保留（UNKNOWN），不能当成不达标淘汰。

    原实现：_education_level(None)=0 < 3 -> reasons -> continue（静默淘汰）。
    """
    strategy = HardFilterStrategy()
    passed = await strategy.match(POSITION, CANDIDATES)

    passed_ids = {item["talent_id"] for item in passed}
    assert 13 in passed_ids, "缺学历的候选人必须保留在结果中"
    assert 12 in passed_ids

    unknown_item = next(item for item in passed if item["talent_id"] == 13)
    assert unknown_item["has_unknown"] is True
    assert unknown_item["hard_status"] == "UNKNOWN"
    assert any(
        result["item"] == "min_education" and result["status"] == "UNKNOWN"
        for result in unknown_item["hard_results"]
    )


@pytest.mark.asyncio
async def test_hard_excludes_fail_with_reasons():
    """FAIL 人才进入 excluded，且必须带明确淘汰原因（原实现只丢弃、无原因）。"""
    strategy = HardFilterStrategy()
    passed = await strategy.match(POSITION, CANDIDATES)

    assert 31 not in {item["talent_id"] for item in passed}
    assert len(strategy.excluded) == 1

    excluded = strategy.excluded[0]
    assert excluded["talent_id"] == 31
    assert excluded["status"] == "FAIL"
    assert excluded["reasons"], "淘汰必须记录原因"
    assert any("学历" in reason for reason in excluded["reasons"])


@pytest.mark.asyncio
async def test_unknown_is_demoted_not_dropped():
    """UNKNOWN 不淘汰但要降权：分数低于同等条件的完整档案。"""
    strategy = HardFilterStrategy()
    passed = await strategy.match(POSITION, CANDIDATES)
    by_id = {item["talent_id"]: item for item in passed}

    assert by_id[13]["score"] < by_id[12]["score"], "缺档案者不应与证据齐全者同分"
    assert by_id[13]["score"] == pytest.approx(round(by_id[13]["score"], 2))


@pytest.mark.asyncio
async def test_hard_results_sorted_desc():
    """结果按分数倒序（Score 由固定 100 改为真实加权分后仍需保持排序语义）。"""
    strategy = HardFilterStrategy()
    passed = await strategy.match(POSITION, CANDIDATES)
    scores = [item["score"] for item in passed]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_rule_config_is_injectable():
    """B-1 口径可配置：partial_review 下部分命中不淘汰。"""
    strategy = HardFilterStrategy(rule_config=HardRuleConfig(required_skill_mode="partial_review"))
    partial = {**CANDIDATE_COMPLETE, "id": 14, "name": "赵六", "skill_tags": ["Python"]}
    passed = await strategy.match(POSITION, [partial])

    assert len(passed) == 1
    assert passed[0]["needs_review"] is True
    assert strategy.excluded == []


@pytest.mark.asyncio
async def test_hybrid_carries_hard_info_and_excluded():
    """混合策略必须透传四态明细与淘汰名单（否则解释与 excluded 无处可取）。"""
    strategy = HybridStrategy()
    results = await strategy.match(POSITION, CANDIDATES)

    assert {item["talent_id"] for item in results} == {12, 13}
    assert len(strategy.excluded) == 1 and strategy.excluded[0]["talent_id"] == 31

    for item in results:
        assert "hard_results" in item
        assert item["hard_status"] in {"PASS", "UNKNOWN", "NEEDS_REVIEW"}


@pytest.mark.asyncio
async def test_hybrid_unknown_penalty_survives_soft_rescore():
    """回归：软匹配会重算分数，UNKNOWN 降权必须重新施加，不能被覆盖成满分。"""
    strategy = HybridStrategy()
    results = await strategy.match(POSITION, [CANDIDATE_COMPLETE, CANDIDATE_MISSING_EDU])
    by_id = {item["talent_id"]: item for item in results}

    assert by_id[13]["score"] < by_id[12]["score"]

    # 直接按软匹配口径算一遍，确认降权确实生效
    raw_unknown = compute_match_score(CANDIDATE_MISSING_EDU, POSITION)["total_score"]
    assert by_id[13]["score"] == pytest.approx(round(raw_unknown * 0.85, 2))


@pytest.mark.asyncio
async def test_soft_strategy_regression():
    """原有软匹配策略回归：仍复用 compute_match_score，全部保留并按分数排序。"""
    strategy = SoftMatchStrategy()
    results = await strategy.match(POSITION, CANDIDATES)

    assert len(results) == 3
    scores = [item["score"] for item in results]
    assert scores == sorted(scores, reverse=True)

    expected = compute_match_score(CANDIDATE_COMPLETE, POSITION)["total_score"]
    assert results[0]["talent_id"] == 12
    assert results[0]["score"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_soft_strategy_carries_hard_four_states():
    """*kwq* 新增（MATCH-019）：软匹配也必须产出四态明细，否则无法解释「为什么分低」。"""
    strategy = SoftMatchStrategy()
    results = await strategy.match(POSITION, CANDIDATES)
    by_id = {item["talent_id"]: item for item in results}

    # 不做淘汰：连学历大专、技能全不符的人也保留（soft 的语义是只排序不筛人）
    assert len(results) == 3
    for item in results:
        assert item["hard_results"], f"{item['talent_id']} 缺少四态明细"
        assert item["hard_status"] in {"PASS", "UNKNOWN", "NEEDS_REVIEW", "FAIL"}

    # 逐项状态必须与 hybrid 口径一致：缺失学历 = UNKNOWN，不达标 = FAIL
    assert by_id[13]["hard_status"] == "UNKNOWN"
    assert by_id[31]["hard_status"] == "FAIL"
    assert by_id[31]["has_unknown"] is False


@pytest.mark.asyncio
async def test_soft_stats_counts_are_not_silently_zero():
    """*kwq* 新增（MATCH-019）：soft 下 unknown/needs_review 不能恒为 0（那是「没算」不是「没有」）。"""
    strategy = SoftMatchStrategy()
    results = await strategy.match(POSITION, CANDIDATES)

    unknown = sum(1 for item in results if item["has_unknown"])
    needs_review = sum(1 for item in results if item["needs_review"])
    assert unknown == 1  # 只有 id=13（学历缺失）
    assert needs_review >= 0


@pytest.mark.asyncio
async def test_factory_still_works():
    """策略工厂未被破坏：三种策略均可创建并执行。"""
    assert MatchStrategyFactory.available() == ["hard", "hybrid", "soft"]

    for name in ("hard", "soft", "hybrid"):
        strategy = MatchStrategyFactory.create(name)
        results = await strategy.match(POSITION, [CANDIDATE_COMPLETE])
        assert len(results) == 1
        assert results[0]["talent_id"] == 12


@pytest.mark.asyncio
async def test_empty_candidates():
    """空候选人列表不应抛异常。"""
    for strategy_cls in (HardFilterStrategy, SoftMatchStrategy, HybridStrategy):
        results = await strategy_cls().match(POSITION, [])
        assert results == []
