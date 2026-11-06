"""
文件：tests/integration/test_matching_flow.py
职责：匹配链路集成测试（真实 MySQL + 真实 Service/Repository + LangGraph 编排）。

分层对应：
- L2 模块集成：Service -> Repository -> MySQL；
- L4 业务流程：岗位画像确认 -> 召回 -> 四态判定 -> 打分 -> 证据 -> 解释 -> 校验 -> 落库 -> 回读；
- L5 异常与边界：画像未确认、无候选人、缺字段、硬条件淘汰、解释重算。

说明：外部服务（Dify / LLM）在本环境未配置，解释走规则兜底；
      这不是 Mock —— 是真实执行的降级通道，且 source 字段如实返回 rule_fallback。

标签：[Agent组] [P2]（匹配全流程集成用例）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models.matching import MatchingRecord
from app.models.user import User
from app.schemas.matching import HardStatus, MatchExplainRequest, MatchRunRequest
from app.services.matching_service import MatchingService


async def _count_records(db, batch_id: str) -> int:
    stmt = select(func.count()).select_from(MatchingRecord).where(MatchingRecord.batch_id == batch_id)
    return int((await db.execute(stmt)).scalar() or 0)


def _req(seed, **kwargs) -> MatchRunRequest:
    """构造请求：默认只针对本用例创建的候选人。

    原因：集成测试共用一个测试库，若每次全库召回会把其他用例造的人也算进分母，
    断言会随执行顺序漂移；限定 talent_ids 后用例之间互不影响。
    """
    kwargs.setdefault("talent_ids", list(seed.talent_ids.values()))
    return MatchRunRequest(position_id=seed.position_id, **kwargs)


class TestMatchingServiceIntegration:
    """Service 层集成（不经 HTTP）。"""

    async def test_run_returns_ranked_items_and_persists(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )

        assert result.batch_id
        assert result.position_title == "后端开发工程师"
        assert result.requirement_version == seed_data.profile_version
        assert result.recall_mode == "sql_fallback"
        assert result.stats.candidates == 5
        # 至少两名合格候选人（张伟 / 陈晨）
        assert len(result.items) >= 2
        scores = [item.score for item in result.items]
        assert scores == sorted(scores, reverse=True)
        assert all(0 <= score <= 100 for score in scores)

        # 落库真实存在，且条数与 items 一致
        assert await _count_records(db_session, result.batch_id) == len(result.items)

        # 每位候选人都有四态明细
        assert all(item.hard_results for item in result.items)

    async def test_missing_profile_fields_are_unknown_not_fail(self, db_session, seed_data):
        """档案缺学历 / 年限未填：必须 UNKNOWN / NEEDS_REVIEW 保留，不能淘汰。"""
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )

        excluded_ids = {item.talent_id for item in result.excluded}
        assert seed_data.talent_ids["王强"] not in excluded_ids

        wang = next(item for item in result.items if item.talent_id == seed_data.talent_ids["王强"])
        statuses = {item.status.value for item in wang.hard_results}
        assert "UNKNOWN" in statuses
        assert wang.data_completeness < 0.6
        assert set(wang.defaulted_dimensions) >= {"education", "skill"}

    async def test_hard_fail_is_excluded_with_reasons(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )

        excluded = {item.talent_id: item for item in result.excluded}
        assert seed_data.talent_ids["赵敏"] in excluded
        reasons = " ".join(excluded[seed_data.talent_ids["赵敏"]].reasons)
        assert "学历" in reasons
        # 淘汰者不得同时出现在结果列表里
        assert seed_data.talent_ids["赵敏"] not in {item.talent_id for item in result.items}
        assert result.stats.excluded_by_hard == len(result.excluded)

    async def test_partial_skills_can_still_fail_strict_mode(self, db_session, seed_data):
        """李娜只命中 1/3 必备技能：默认 all 口径下淘汰，但原因必须写清命中比例。"""
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )
        excluded = {item.talent_id: item for item in result.excluded}
        assert seed_data.talent_ids["李娜"] in excluded
        assert any("1/3" in reason for reason in excluded[seed_data.talent_ids["李娜"]].reasons)

    # *kwq* 新增（MATCH-014）：淘汰明细必须可回读。
    # 原因：验收时发现 GET /matchings/positions/{id} 的 excluded 恒为 []，
    #      刷新页面后「谁被淘汰、为什么」全部丢失，与原批次结果不一致。
    async def test_latest_restores_excluded_details(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(_req(seed_data, top_k=10), current_user_id=1)

        latest = await service.get_latest(seed_data.position_id)

        assert {item.talent_id for item in latest.excluded} == {
            item.talent_id for item in result.excluded
        }
        # 原因文本必须一起还原，否则「可追溯」只剩一个 ID
        run_reasons = {item.talent_id: item.reasons for item in result.excluded}
        for item in latest.excluded:
            assert item.reasons == run_reasons[item.talent_id]

    # *kwq* 新增（MATCH-014）：全淘汰批次的诚实降级（已知限制，见验收报告 P2）。
    async def test_zero_output_batch_is_persisted(self, db_session, seed_data):
        """零输出仍有可追溯批次；不返回旧结果，也不虚构人才明细。"""
        service = MatchingService(db_session)
        result = await service.run(_req(seed_data, top_k=10, min_score=100), current_user_id=1)
        assert not result.items
        latest = await service.get_latest(seed_data.position_id)
        assert latest.batch_id == result.batch_id and not latest.items
        snapshot = await service.get_batch_snapshot(result.batch_id)
        assert snapshot.stats == result.stats


        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )
        latest = await service.get_latest(seed_data.position_id)

        assert latest.batch_id == result.batch_id
        assert [item.talent_id for item in latest.items] == [item.talent_id for item in result.items]
        assert [round(item.score, 2) for item in latest.items] == [round(item.score, 2) for item in result.items]
        assert latest.items[0].highlights == result.items[0].highlights
        assert latest.items[0].explanation_source == "rule_fallback"

    async def test_history_by_talent(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, top_k=10),
            current_user_id=1,
        )
        top_id = result.items[0].talent_id

        history = await service.get_history_by_talent(top_id)
        assert history
        assert history[0].batch_id == result.batch_id
        assert history[0].position_title == "后端开发工程师"
        assert history[0].requirement_version == seed_data.profile_version

    async def test_explain_works_for_excluded_candidate(self, db_session, seed_data):
        """被硬条件淘汰的人也必须能生成解释（回答「为什么没出现」）。"""
        service = MatchingService(db_session)
        explanation = await service.explain(
            MatchExplainRequest(
                position_id=seed_data.position_id,
                talent_id=seed_data.talent_ids["赵敏"],
            )
        )

        assert explanation.summary
        assert explanation.source == "rule_fallback"
        # 解释中的结论必须挂真实证据；越界结论会被 rejected
        assert explanation.highlights or explanation.gaps
        assert all(claim.evidence_ids for claim in explanation.highlights + explanation.gaps)

    async def test_unconfirmed_profile_blocks_matching(self, db_session, unconfirmed_position):
        service = MatchingService(db_session)
        with pytest.raises(BusinessRuleError):
            await service.run(MatchRunRequest(position_id=unconfirmed_position), current_user_id=1)

    async def test_no_candidates_returns_empty_stats(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(
            _req(seed_data, talent_ids=[999_999_999], top_k=10),
            current_user_id=1,
        )

        assert result.items == []
        assert result.excluded == []
        assert result.stats.candidates == 0
        assert result.stats.output == 0
        assert await _count_records(db_session, result.batch_id) == 0

    async def test_top_k_and_min_score_only_affect_output(self, db_session, seed_data):
        service = MatchingService(db_session)
        full = await service.run(
            _req(seed_data, top_k=10, min_score=0),
            current_user_id=1,
        )
        limited = await service.run(
            _req(seed_data, top_k=1, min_score=0),
            current_user_id=1,
        )

        assert len(full.items) >= 2
        assert len(limited.items) == 1
        # 分数不受 top_k 影响（同一候选人同分）
        assert limited.items[0].score == full.items[0].score

        high = await service.run(
            _req(seed_data, top_k=10, min_score=99.9),
            current_user_id=1,
        )
        assert all(item.score >= 99.9 for item in high.items)

    async def test_recall_without_ids_uses_sql_fallback(self, db_session, seed_data):
        """不指定候选人时走 SQL 全库召回，且 recall_mode 必须如实标注。"""
        service = MatchingService(db_session)
        result = await service.run(
            MatchRunRequest(position_id=seed_data.position_id, top_k=3),
            current_user_id=1,
        )
        assert result.recall_mode == "sql_fallback"
        assert result.stats.candidates >= 5
        assert len(result.items) <= 3

    async def test_requirement_version_is_pinned(self, db_session, seed_data):
        """画像新增版本后，按旧版本重算必须使用旧画像且标注旧版本号。"""
        from app.schemas.position import JobProfileConfirmRequest
        from app.services.position_profile_service import PositionProfileService

        service = MatchingService(db_session)
        v1_result = await service.run(_req(seed_data), current_user_id=1)

        current = await service.profile_service.get_profile(seed_data.position_id)
        changed = current.profile.model_dump(mode="json")
        changed["required_skills"] = ["Python", "FastAPI", "MySQL", "Redis"]
        # 不能与必备重复，否则会被「技能同时必备+优先」冲突拦截（这正是校验生效的证明）
        changed["preferred_skills"] = []
        changed.pop("missing_fields", None)
        changed.pop("conflicts", None)
        await PositionProfileService(db_session).confirm(
            seed_data.position_id,
            JobProfileConfirmRequest(profile=changed, expected_version=seed_data.profile_version),
            user_id=1,
        )

        pinned = await service.run(
            _req(seed_data, requirement_version=seed_data.profile_version), current_user_id=1
        )
        latest = await service.run(_req(seed_data), current_user_id=1)

        assert v1_result.requirement_version == seed_data.profile_version
        assert pinned.requirement_version == seed_data.profile_version
        assert latest.requirement_version == seed_data.profile_version + 1

    async def test_same_score_order_is_stable(self, db_session, seed_data):
        """同分候选人顺序必须稳定（可复现），不能每次跑批顺序抖动。"""
        from app.models.talent import Talent

        twin_a = Talent(name="同分甲", education="本科", work_years=5, skill_tags=["Python", "FastAPI", "MySQL"], potential_level="B")
        twin_b = Talent(name="同分乙", education="本科", work_years=5, skill_tags=["Python", "FastAPI", "MySQL"], potential_level="B")
        db_session.add_all([twin_a, twin_b])
        await db_session.commit()

        service = MatchingService(db_session)
        first = await service.run(
            _req(seed_data, talent_ids=[twin_a.id, twin_b.id]), current_user_id=1
        )
        second = await service.run(
            _req(seed_data, talent_ids=[twin_a.id, twin_b.id]), current_user_id=1
        )

        assert [item.talent_id for item in first.items] == [item.talent_id for item in second.items]
        scores = [item.score for item in first.items]
        assert scores == sorted(scores, reverse=True)

    async def test_left_status_talent_is_not_matched(self, db_session, seed_data):
        """离职人员不参与匹配（即使显式指定 ID）。"""
        from app.models.talent import Talent

        left = Talent(name="离职者", education="本科", work_years=8, skill_tags=["Python", "FastAPI", "MySQL"], status="left")
        db_session.add(left)
        await db_session.commit()

        service = MatchingService(db_session)
        result = await service.run(_req(seed_data, talent_ids=[left.id]), current_user_id=1)

        assert result.items == []
        assert result.stats.candidates == 0

    async def test_batches_are_independent(self, db_session, seed_data):
        service = MatchingService(db_session)
        first = await service.run(_req(seed_data), current_user_id=1)
        second = await service.run(_req(seed_data), current_user_id=1)

        assert first.batch_id != second.batch_id
        assert await _count_records(db_session, first.batch_id) == len(first.items)
        assert await _count_records(db_session, second.batch_id) == len(second.items)

    async def test_unknown_position_raises(self, db_session):
        service = MatchingService(db_session)
        with pytest.raises(Exception) as exc:
            await service.run(MatchRunRequest(position_id=987_654_321), current_user_id=1)
        assert "岗位不存在" in str(exc.value)

    # *kwq* 新增（MATCH-017/018）：结果条目必须自带名次与整体判定。
    # 原因：前端若拿数组下标当名次，一旦分页 / min_score 过滤就会错位；
    #      整体判定原先要靠前端聚合四态，各端口径容易不一致。
    async def test_items_carry_rank_and_decision(self, db_session, seed_data):
        service = MatchingService(db_session)
        result = await service.run(_req(seed_data), current_user_id=1)

        assert [item.rank for item in result.items] == list(range(1, len(result.items) + 1))
        assert all(
            item.decision in (HardStatus.PASS, HardStatus.UNKNOWN, HardStatus.NEEDS_REVIEW)
            for item in result.items
        )
        # 存在档案缺失者时，其后名次的具体判定必须是 UNKNOWN / NEEDS_REVIEW，不能谎报 PASS
        unknown_like = [i for i in result.items if i.data_completeness < 1.0]
        assert unknown_like, "种子数据应包含档案不完整的候选人（否则该用例失去意义）"
        assert all(i.decision != HardStatus.PASS for i in unknown_like)

        # 回读必须与执行结果逐条一致（rank / decision 不能是前端临时算的）
        latest = await service.get_latest(seed_data.position_id)
        assert [i.rank for i in latest.items] == [i.rank for i in result.items]
        assert [i.decision for i in latest.items] == [i.decision for i in result.items]

    # *kwq* 新增（MATCH-020）：指定不存在的画像版本。
    # 原因：实测 requirement_version=999 返回「该岗位尚未确认岗位画像」，
    #      而该岗位其实有已确认版本 —— 报错指向错误方向，排查会被带偏。
    async def test_missing_requirement_version_is_404(self, db_session, seed_data):
        service = MatchingService(db_session)
        with pytest.raises(NotFoundError) as exc:
            await service.run(
                MatchRunRequest(position_id=seed_data.position_id, requirement_version=999),
                current_user_id=1,
            )
        assert "版本不存在" in str(exc.value)
        assert "尚未确认" not in str(exc.value)

    async def test_employee_permission_metadata(self, db_session, seed_data):
        """用户表真实存在，匹配记录写入 matched_by（用于审计）。"""
        user = User(
            username=f"u-{uuid.uuid4().hex[:8]}",
            password_hash="x",
            real_name="测试用户",
            is_active=True,
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        service = MatchingService(db_session)
        result = await service.run(_req(seed_data), current_user_id=user.id)

        stmt = select(MatchingRecord.matched_by).where(MatchingRecord.batch_id == result.batch_id).limit(1)
        assert (await db_session.execute(stmt)).scalar_one() == user.id
