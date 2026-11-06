"""
标签：[Agent组] [P1]
模块：tests/unit/test_matching_review.py
职责：HR 复核流程的规则单测（纯逻辑 + mock 仓储，不依赖数据库）。

覆盖重点（这些是最容易写错、且出错代价最高的地方）：
1. 状态机：没面试不能录用、淘汰必填原因、录用不能直接跳回入围；
2. override 判定：只有 FAIL 才算绕过，UNKNOWN/NEEDS_REVIEW 不算；
3. 幂等：重复提交同状态不写流水；
4. 并发：版本不一致必须 409，不能静默覆盖；
5. 漏斗口径：total 与各阶段之和一致，录用率分母为已建档数。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import BusinessRuleError, DataConflictError, NotFoundError, ParamError
from app.schemas.matching_review import MatchingReviewUpsertRequest
from app.services.matching_review_service import (
    MatchingReviewService,
    is_override,
    validate_transition,
)


def _review(**overrides):
    """构造一个复核记录替身（字段与 ORM 一一对应）。"""
    base = dict(
        id=9,
        batch_id="b" * 32,
        position_id=3,
        talent_id=7,
        review_status="pending",
        reason_code=None,
        note=None,
        score_snapshot=None,
        rank_snapshot=None,
        decided_by=None,
        decided_at=None,
        version=1,
        created_at=None,
        updated_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(items, position_id=3):
    """构造匹配批次快照替身。"""
    return SimpleNamespace(
        batch_id="b" * 32,
        position_id=position_id,
        response={"items": items, "stats": {"output": len(items)}},
    )


class TestTransitionRules:
    """状态流转规则。"""

    def test_first_shortlist_from_pending_is_allowed(self):
        """首次建档直接入围：合法（pending 有 shortlisted 出口）。"""
        validate_transition(None, "shortlisted")

    def test_pending_cannot_jump_to_hired(self):
        """没走面试直接录用必须拦下——这是合规红线，不是可配置项。"""
        with pytest.raises(BusinessRuleError, match="不允许变更"):
            validate_transition("pending", "hired")

    def test_first_time_cannot_be_hired(self):
        """首次提交就 hired 同样拦下（current=None 视为 pending 起跑）。"""
        with pytest.raises(BusinessRuleError):
            validate_transition(None, "hired")

    def test_interviewing_to_hired_is_allowed(self):
        validate_transition("interviewing", "hired")

    def test_hired_can_only_go_to_rejected(self):
        """录用后只能因候选人放弃转淘汰，不能退回其他阶段。"""
        validate_transition("hired", "rejected", reason_code="withdraw")
        with pytest.raises(BusinessRuleError):
            validate_transition("hired", "shortlisted")

    def test_rejected_requires_reason_code(self):
        """淘汰必须留原因，否则统计口径和合规审计都无从谈起。"""
        with pytest.raises(ParamError, match="reason_code"):
            validate_transition("pending", "rejected")

    def test_rejected_with_reason_code_is_allowed(self):
        validate_transition("pending", "rejected", reason_code="skill_gap")

    def test_reason_other_requires_note(self):
        with pytest.raises(ParamError, match="note"):
            validate_transition("pending", "rejected", reason_code="other")
        validate_transition("pending", "rejected", reason_code="other", note="背景调查未通过")

    def test_unknown_reason_code_rejected(self):
        with pytest.raises(ParamError, match="取值非法"):
            validate_transition("pending", "rejected", reason_code="not_a_reason")

    def test_same_status_is_not_an_error(self):
        """同状态重复提交交给幂等分支处理，不该在这里报错。"""
        validate_transition("pending", "pending")

    def test_rejected_can_be_withdrawn_to_pending(self):
        validate_transition("rejected", "pending")


class TestOverrideFlag:
    """人工放行判定口径。"""

    def test_fail_with_advancing_status_is_override(self):
        for status in ("shortlisted", "interviewing", "hired"):
            assert is_override("FAIL", status) is True

    def test_fail_with_pending_is_not_override(self):
        assert is_override("FAIL", "pending") is False

    def test_unknown_is_not_override(self):
        """UNKNOWN 是「还没确认」，不是「已确认不符合」，不能算合规问题。"""
        assert is_override("UNKNOWN", "shortlisted") is False

    def test_needs_review_is_not_override(self):
        assert is_override("NEEDS_REVIEW", "interviewing") is False

    def test_pass_is_not_override(self):
        assert is_override("PASS", "hired") is False

    def test_lowercase_decision_is_normalized(self):
        assert is_override("fail", "shortlisted") is True


class TestRequestSchema:
    """请求契约的空值处理。"""

    def test_blank_note_becomes_none(self):
        """全空白备注视为未填，否则会绕过「other 必须说明」的校验。"""
        payload = MatchingReviewUpsertRequest(status="pending", note="   ")
        assert payload.note is None

    def test_note_is_stripped(self):
        payload = MatchingReviewUpsertRequest(status="shortlisted", note="  团队扩编  ")
        assert payload.note == "团队扩编"


class TestDecide:
    """决策主流程（mock 仓储，验证分支与异常类型）。"""

    def _service(self, *, snapshot, existing=None, updated=True):
        db = AsyncMock()
        review_repo = AsyncMock()
        review_repo.get_by_batch_talent.return_value = existing
        review_repo.update_with_version.return_value = updated
        # decide 结束时会强制回读一次（populate_existing），这里给一个确定的对象
        review_repo.get_by_id_fresh.return_value = existing or _review(review_status="shortlisted")
        review_repo.create.return_value = _review()
        event_repo = AsyncMock()
        event_repo.append.return_value = SimpleNamespace(
            id=1, review_id=9, from_status="pending", to_status="shortlisted",
            reason_code=None, note=None, operator_id=None, created_at=None,
        )
        matching_repo = AsyncMock()
        matching_repo.snapshot_by_id.return_value = snapshot
        return MatchingReviewService(
            db, review_repo=review_repo, event_repo=event_repo, matching_repo=matching_repo
        )

    async def test_missing_batch_raises_404(self):
        service = self._service(snapshot=None)
        with pytest.raises(NotFoundError, match="批次不存在"):
            await service.decide(
                batch_id="x", talent_id=7,
                payload=MatchingReviewUpsertRequest(status="shortlisted"),
            )

    async def test_talent_not_in_batch_raises_404(self):
        """不在结果里的候选人不能建档，否则漏斗会凭空多出人。"""
        snapshot = _snapshot([{"talent_id": 99, "score": 80, "decision": "PASS"}])
        service = self._service(snapshot=snapshot)
        with pytest.raises(NotFoundError, match="不在批次"):
            await service.decide(
                batch_id="x", talent_id=7,
                payload=MatchingReviewUpsertRequest(status="shortlisted"),
            )

    async def test_same_status_is_idempotent_and_writes_no_event(self):
        """重复提交同状态：返回 changed=False，不写流水，避免重试刷出噪音。"""
        snapshot = _snapshot([{"talent_id": 7, "score": 82, "decision": "PASS"}])
        service = self._service(snapshot=snapshot, existing=_review(review_status="pending"))
        result = await service.decide(
            batch_id="x", talent_id=7,
            payload=MatchingReviewUpsertRequest(status="pending"),
        )
        assert result.changed is False
        assert result.event is None
        service.review_repo.update_with_version.assert_not_awaited()
        service.event_repo.append.assert_not_awaited()

    async def test_version_conflict_raises_409(self):
        """两人同时操作：后者必须收到 409，而不是覆盖前者的决策。"""
        snapshot = _snapshot([{"talent_id": 7, "score": 82, "decision": "PASS"}])
        service = self._service(
            snapshot=snapshot, existing=_review(review_status="pending", version=2), updated=False
        )
        with pytest.raises(DataConflictError):
            await service.decide(
                batch_id="x", talent_id=7,
                payload=MatchingReviewUpsertRequest(status="shortlisted", expected_version=1),
            )

    async def test_override_requires_note(self):
        """硬条件 FAIL 仍入围：不写理由就拒绝。"""
        snapshot = _snapshot([{"talent_id": 7, "score": 55, "decision": "FAIL"}])
        service = self._service(snapshot=snapshot)
        with pytest.raises(ParamError, match="人工放行"):
            await service.decide(
                batch_id="x", talent_id=7,
                payload=MatchingReviewUpsertRequest(status="shortlisted"),
            )

    async def test_override_with_note_succeeds_and_marks_event(self):
        snapshot = _snapshot([{"talent_id": 7, "score": 55, "decision": "FAIL"}])
        service = self._service(snapshot=snapshot)
        await service.decide(
            batch_id="x", talent_id=7,
            payload=MatchingReviewUpsertRequest(status="shortlisted", note="内部推荐，特批"),
        )
        event = service.event_repo.append.await_args.args[1]
        assert event.overridden is True
        assert event.note == "内部推荐，特批"

    async def test_snapshot_score_is_frozen_into_review(self):
        """决策时必须冻结当时的分数，否则重算后审计对不上账。"""
        snapshot = _snapshot([{"talent_id": 7, "score": 82.5, "decision": "PASS", "rank": 2}])
        service = self._service(snapshot=snapshot)
        await service.decide(
            batch_id="x", talent_id=7,
            payload=MatchingReviewUpsertRequest(status="shortlisted"),
        )
        created = service.review_repo.create.await_args.args[1]
        assert created["score_snapshot"] == 82.5
        assert created["rank_snapshot"] == 2
        assert created["review_status"] == "shortlisted"


class TestFunnel:
    """漏斗口径。"""

    async def test_funnel_counts_and_rate(self):
        db = AsyncMock()
        review_repo = AsyncMock()
        review_repo.funnel_counts.return_value = {
            "pending": 4, "shortlisted": 2, "interviewing": 1, "rejected": 2, "hired": 1
        }
        service = MatchingReviewService(db, review_repo=review_repo)
        result = await service.funnel(position_id=3)

        assert result.total == 10
        assert sum(stage.count for stage in result.stages) == result.total
        assert result.converted == 2  # 面试中 1 + 录用 1
        assert result.hired_rate == pytest.approx(0.1)
        assert result.caliber_note  # 口径说明必须写进响应，避免报表各说各话

    async def test_empty_position_funnel_is_zero(self):
        db = AsyncMock()
        review_repo = AsyncMock()
        review_repo.funnel_counts.return_value = {}
        service = MatchingReviewService(db, review_repo=review_repo)
        result = await service.funnel(position_id=3)

        assert result.total == 0
        assert result.hired_rate == 0.0  # 不能除零
        assert len(result.stages) == 5


class TestEventsQuery:
    """流水查询参数校验。"""

    async def test_requires_review_or_position(self):
        service = MatchingReviewService(AsyncMock(), review_repo=AsyncMock())
        with pytest.raises(ParamError, match="至少提供一个"):
            await service.list_events()


class TestTransactionBoundary:
    """事务边界：服务层只 flush，提交由请求级 Unit of Work 统一负责。"""

    async def test_decide_does_not_commit_itself(self):
        """状态更新与流水写入必须落在同一个事务里。

        做法是「谁都不提交」——由 get_db 在请求结束时统一 commit/rollback。
        如果服务层自己 commit，流水写入失败时就无法把状态一起回滚，
        会出现「状态改了、流水没记」的半截数据。
        """
        db = AsyncMock()
        review_repo = AsyncMock()
        review_repo.get_by_batch_talent.return_value = None
        review_repo.create.return_value = _review(batch_id="b" * 32)
        review_repo.get_by_id_fresh.return_value = _review(batch_id="b" * 32, review_status="shortlisted")
        event_repo = AsyncMock()
        event_repo.append.return_value = SimpleNamespace(
            id=1, review_id=9, from_status=None, to_status="shortlisted",
            reason_code=None, note=None, operator_id=5, created_at=None,
        )
        matching_repo = AsyncMock()
        matching_repo.snapshot_by_id.return_value = _snapshot(
            [{"talent_id": 7, "score": 82, "decision": "PASS"}]
        )
        service = MatchingReviewService(
            db, review_repo=review_repo, event_repo=event_repo, matching_repo=matching_repo
        )

        await service.decide(
            batch_id="b" * 32, talent_id=7,
            payload=MatchingReviewUpsertRequest(status="shortlisted"),
            operator_id=5,
        )

        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()
        # 状态与流水都只 flush 过一次，没有独立的提交点
        review_repo.create.assert_awaited_once()
        event_repo.append.assert_awaited_once()
