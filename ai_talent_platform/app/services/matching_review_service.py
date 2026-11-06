"""
标签：[Agent组] [P1]
模块：app/services/matching_review_service.py
职责：HR 人工复核的业务规则与状态流转（本阶段新建）。

============================ 这个服务解决什么 ============================
现状缺口：匹配跑完之后，HR 只能「看」，不能「记」——谁被入围、谁被淘汰、
为什么，全部只存在于 HR 脑子里和微信聊天记录里。于是：
1. 排序结果无法沉淀为招聘决策，下一批次没有任何可参考的历史；
2. 权重 0.4/0.3/0.2/0.1 永远拍脑袋，无法用真实录用结果校准；
3. 出问题时查不到「谁在什么时候把这个人淘汰了」。

本服务把「一次性排序」变成「可追溯的决策流」：
    POST 决策（幂等 upsert + 乐观锁）→ 状态表更新 + 流水追加（同一事务）
    GET  列表 / 流水 / 岗位漏斗 → 复核工作台与转化率看板的数据源

============================ 关键规则 ============================
1. 幂等：同一 (batch_id, talent_id) 只有一条状态记录，重复提交是更新不是新增；
   目标状态与当前相同时直接返回 changed=False，不刷流水（网络重发安全）。
2. 状态机：见 models/matching_review.REVIEW_TRANSITIONS；
   没面试过不能录用（interviewing 才有 hired 出口），这是合规红线。
3. 淘汰必填原因码（REVIEW_REASON_REQUIRED），"其他"必须在 note 说明。
4. 硬条件未通过仍推进（入围/面试/录用）属于人工 override：
   note 必填，并在流水上打 overridden=True，便于统计风险放行次数。
5. 并发：带 version 条件的原子 UPDATE，冲突返回 409 而不是静默覆盖。

边界：只读写 matching_reviews / matching_review_events，只读匹配批次快照；
不修改人才、岗位、简历任何表；本服务不重算匹配分。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    DataConflictError,
    NotFoundError,
    ParamError,
    SystemError,
)
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.matching_review import (
    REVIEW_HIRED,
    REVIEW_INTERVIEWING,
    REVIEW_PENDING,
    REVIEW_REASON_CODES,
    REVIEW_REASON_REQUIRED,
    REVIEW_SHORTLISTED,
    REVIEW_STATUS_LABELS,
    REVIEW_STATUSES,
    REVIEW_TRANSITIONS,
    MatchingReview,
    MatchingReviewEvent,
)
from app.repositories.matching_repository import MatchingRepository
from app.repositories.matching_review_repository import (
    MatchingReviewEventRepository,
    MatchingReviewRepository,
)
from app.schemas.matching_review import (
    MatchingReviewDecideResponse,
    MatchingReviewEventItem,
    MatchingReviewItem,
    MatchingReviewUpsertRequest,
    ReviewFunnelResponse,
    ReviewFunnelStage,
)

logger = logging.getLogger(__name__)

#: 这些目标状态意味着「候选人被推进」，若匹配判定为 FAIL 即视为绕过硬门槛
_ADVANCING_STATUSES: frozenset[str] = frozenset({REVIEW_SHORTLISTED, REVIEW_INTERVIEWING, REVIEW_HIRED})


def validate_transition(
    current: str | None,
    target: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
) -> None:
    """校验一次状态流转是否合法（纯函数，便于单测直接覆盖规则）。

    current=None 表示该候选人尚未建档，视为从 pending 起跑——因此
    「首次直接录用」会被拦下（pending 没有 hired 出口），这符合业务直觉：
    没走复核流程就录用，本质是绕过流程。

    异常类型的选择理由：
        - 非法流转 = 业务规则不允许 → 400（不是参数格式错）；
        - 缺必填原因 = 参数不完整 → 422。
    """
    if target not in REVIEW_STATUSES:
        raise ParamError(f"未知的复核状态：{target}")

    effective_current = current or REVIEW_PENDING
    if target != effective_current:
        allowed = REVIEW_TRANSITIONS.get(effective_current, frozenset())
        if target not in allowed:
            allowed_text = "、".join(
                f"{s}({REVIEW_STATUS_LABELS.get(s, s)})" for s in sorted(allowed)
            ) or "无"
            raise BusinessRuleError(
                f"当前状态 {effective_current}({REVIEW_STATUS_LABELS.get(effective_current, '')})"
                f" 不允许变更为 {target}({REVIEW_STATUS_LABELS.get(target, '')})；"
                f"允许的目标状态：{allowed_text}"
            )

    if target in REVIEW_REASON_REQUIRED and not reason_code:
        raise ParamError(
            f"变更为 {REVIEW_STATUS_LABELS.get(target, target)} 必须填写 reason_code"
            f"（可选值：{'、'.join(REVIEW_REASON_CODES)}）"
        )
    if reason_code is not None and reason_code not in REVIEW_REASON_CODES:
        raise ParamError(
            f"reason_code 取值非法：{reason_code}；可选值：{'、'.join(REVIEW_REASON_CODES)}"
        )
    if reason_code == "other" and not note:
        raise ParamError("reason_code=other 时必须在 note 中说明具体原因")


def is_override(match_decision: str | None, target: str) -> bool:
    """是否属于「绕过硬性条件」的人为放行。

    只认 FAIL：UNKNOWN（数据缺失）与 NEEDS_REVIEW（待核实）不算绕过——
    那两种情况是「还没确认」，不是「已确认不符合」，强行等价会把数据缺口
    误判成合规问题。
    """
    return str(match_decision or "").upper() == "FAIL" and target in _ADVANCING_STATUSES


class MatchingReviewService:
    """HR 复核业务服务。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        review_repo: MatchingReviewRepository | None = None,
        event_repo: MatchingReviewEventRepository | None = None,
        matching_repo: MatchingRepository | None = None,
    ) -> None:
        self.db = db
        self.review_repo = review_repo or MatchingReviewRepository()
        self.event_repo = event_repo or MatchingReviewEventRepository()
        self.matching_repo = matching_repo or MatchingRepository()

    # ---------------------------------------------------------------- 决策
    async def decide(
        self,
        *,
        batch_id: str,
        talent_id: int,
        payload: MatchingReviewUpsertRequest,
        operator_id: int | None = None,
    ) -> MatchingReviewDecideResponse:
        """提交一次复核决策（幂等 upsert）。

        步骤：
            1. 校验批次存在 + 该人才确实在本批次结果中（不能给没匹配过的人做决策）；
            2. 取批次快照里的 score/rank/name 冻结为决策依据；
            3. 取出已有复核记录，校验状态流转与必填字段；
            4. 硬条件 FAIL 仍推进 → 必须写理由，并打 overridden 标记；
            5. 乐观锁更新状态 + 追加流水（同一事务），冲突返回 409。
        """
        snapshot = await self.matching_repo.snapshot_by_id(self.db, batch_id)
        if snapshot is None:
            raise NotFoundError(f"匹配批次不存在：{batch_id}")

        response_payload = snapshot.response or {}
        items = list(response_payload.get("items") or [])
        matched_item = next(
            (it for it in items if int(it.get("talent_id") or 0) == int(talent_id)), None
        )
        if matched_item is None:
            # 不静默创建：给一个不在结果里的候选人建档，会让漏斗数字对不上明细。
            raise NotFoundError(
                f"人才 {talent_id} 不在批次 {batch_id} 的匹配结果中，无法复核"
                "（可能已被 top_k / min_score 过滤，或批次已重跑）"
            )

        position_id = int(snapshot.position_id)
        match_decision = str(matched_item.get("decision") or "") or None
        score_snapshot = _as_float(matched_item.get("score"))
        rank_snapshot = _as_int(matched_item.get("rank"))
        talent_name = matched_item.get("talent_name")

        existing = await self.review_repo.get_by_batch_talent(
            self.db, batch_id=batch_id, talent_id=int(talent_id)
        )
        current_status = existing.review_status if existing is not None else None

        validate_transition(
            current_status, payload.status, reason_code=payload.reason_code, note=payload.note
        )

        overridden = is_override(match_decision, payload.status)
        if overridden and not payload.note:
            raise ParamError(
                f"该候选人的硬性条件未通过（判定 {match_decision}），"
                f"仍要变更为 {REVIEW_STATUS_LABELS.get(payload.status, payload.status)} "
                "属于人工放行，必须在 note 中说明理由"
            )

        # 幂等：目标状态与当前一致 → 不改不记，直接回现状。
        if existing is not None and current_status == payload.status:
            return MatchingReviewDecideResponse(
                review=self._to_item(existing, talent_name),
                event=None,
                changed=False,
                message=f"状态未变化（仍为 {REVIEW_STATUS_LABELS.get(payload.status, payload.status)}）",
            )

        now = datetime.now()
        review: MatchingReview | None = None

        if existing is None:
            # 首次建档：从 pending 起跑（无流水前置状态）
            review = await self.review_repo.create(
                self.db,
                {
                    "batch_id": batch_id,
                    "position_id": position_id,
                    "talent_id": int(talent_id),
                    "review_status": payload.status,
                    "reason_code": payload.reason_code,
                    "note": payload.note,
                    "score_snapshot": score_snapshot,
                    "rank_snapshot": rank_snapshot,
                    "decided_by": operator_id,
                    "decided_at": now,
                    "created_by": operator_id,
                    "updated_by": operator_id,
                },
            )
            from_status: str | None = None
            review_id = int(review.id)
        else:
            review_id = int(existing.id)
            version_used = int(payload.expected_version or existing.version)
            updated = await self.review_repo.update_with_version(
                self.db,
                review_id=review_id,
                expected_version=version_used,
                values={
                    "review_status": payload.status,
                    "reason_code": payload.reason_code,
                    "note": payload.note,
                    "score_snapshot": score_snapshot,
                    "rank_snapshot": rank_snapshot,
                    "decided_by": operator_id,
                    "decided_at": now,
                    "updated_by": operator_id,
                },
            )
            if not updated:
                raise DataConflictError(
                    "该复核记录已被他人修改（版本不一致），请刷新后重试"
                )
            from_status = current_status

        event = MatchingReviewEvent(
            review_id=review_id,
            batch_id=batch_id,
            position_id=position_id,
            talent_id=int(talent_id),
            from_status=from_status,
            to_status=payload.status,
            reason_code=payload.reason_code,
            note=payload.note,
            operator_id=operator_id,
            overridden=overridden,
        )
        # 必须用 append 的返回值：flush 之后主键才会被回填到实例上，
        # 直接拿入参对象的话，某些情况下 id 仍可能是 None（构造阶段快照）。
        event = await self.event_repo.append(self.db, event)

        item_source = existing if existing is not None else review
        # 回读放在提交前：状态更新、流水写入、回读三者在同一事务内完成，
        # 任一环节失败由 get_db 统一 rollback，不会出现「状态改了但流水没记」。
        # 不在此显式 commit —— 本项目事务边界由 get_db（请求级 Unit of Work）
        # 统一管理，服务层只 flush；这里提前提交会破坏「一请求一事务」语义。
        # 为什么必须 populate_existing：原子 UPDATE 不会刷新身份映射里的实例，
        # 而本项目 session 是 expire_on_commit=False，直接访问会拿到旧 version。
        final = await self.review_repo.get_by_id_fresh(self.db, review_id)
        if final is not None:
            item_source = final
        if item_source is None:  # pragma: no cover - 理论上不可达，防御脏数据
            raise SystemError("复核记录读写异常：无法取得落库后的记录")

        return MatchingReviewDecideResponse(
            review=self._to_item(item_source, talent_name),
            event=self._to_event(event),
            changed=True,
            message=(
                f"已更新为 {REVIEW_STATUS_LABELS.get(payload.status, payload.status)}"
                + ("（人工放行，已记录 overridden）" if overridden else "")
            ),
        )

    # ---------------------------------------------------------------- 查询
    async def page_reviews(
        self,
        page: PageParams,
        *,
        batch_id: str | None = None,
        position_id: int | None = None,
        talent_id: int | None = None,
        review_status: str | None = None,
    ) -> PageResult:
        """复核记录分页（默认按更新时间倒序，即最近处理的在前）。"""
        filters: dict[str, Any] = {}
        if batch_id:
            filters["batch_id"] = batch_id
        if position_id is not None:
            filters["position_id"] = position_id
        if talent_id is not None:
            filters["talent_id"] = talent_id
        if review_status:
            filters["review_status"] = review_status

        result = await self.review_repo.paginate(self.db, page, filters=filters or None)
        names = await self.review_repo.talent_names_by_batch(
            self.db, {str(row.batch_id) for row in result.items}
        )
        items = [
            self._to_item(row, names.get((str(row.batch_id), int(row.talent_id))))
            for row in result.items
        ]
        return PageResult[MatchingReviewItem](
            items=items,
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            pages=result.pages,
        )

    async def list_events(
        self,
        *,
        review_id: int | None = None,
        position_id: int | None = None,
        limit: int = 100,
    ) -> list[MatchingReviewEventItem]:
        """动作流水：给了 review_id 就查单条时间线，否则查岗位维度的最近动作。"""
        if review_id is not None:
            rows = await self.event_repo.list_by_review(self.db, review_id=review_id, limit=limit)
        elif position_id is not None:
            rows = await self.event_repo.list_by_position(
                self.db, position_id=position_id, limit=limit
            )
        else:
            raise ParamError("review_id 与 position_id 至少提供一个")
        return [self._to_event(row) for row in rows]

    async def funnel(self, *, position_id: int) -> ReviewFunnelResponse:
        """岗位级复核漏斗（统计当前所处状态，不是历史累计进入人数）。"""
        counts = await self.review_repo.funnel_counts(self.db, position_id=position_id)
        stages = [
            ReviewFunnelStage(
                status=status,
                label=REVIEW_STATUS_LABELS.get(status, status),
                count=int(counts.get(status, 0)),
            )
            for status in REVIEW_STATUSES
        ]
        total = sum(stage.count for stage in stages)
        hired = int(counts.get(REVIEW_HIRED, 0))
        converted = int(counts.get(REVIEW_INTERVIEWING, 0)) + hired
        return ReviewFunnelResponse(
            position_id=position_id,
            total=total,
            stages=stages,
            converted=converted,
            hired_rate=(hired / total) if total else 0.0,
            caliber_note=(
                "统计各候选人「当前所处状态」，非历史累计进入人数；"
                "总数为已建档复核记录数，可能少于该岗位批次输出人数（未处理的候选人尚未建档）"
            ),
        )

    # ---------------------------------------------------------------- 转换
    def _to_item(self, row: MatchingReview, talent_name: str | None = None) -> MatchingReviewItem:
        """ORM → 接口模型（字段原样映射，不做加工）。"""
        status = str(row.review_status)
        return MatchingReviewItem(
            id=int(row.id),
            batch_id=str(row.batch_id),
            position_id=int(row.position_id),
            talent_id=int(row.talent_id),
            talent_name=talent_name,
            review_status=status,  # type: ignore[arg-type]
            status_label=REVIEW_STATUS_LABELS.get(status),
            reason_code=row.reason_code,  # type: ignore[arg-type]
            note=row.note,
            score_snapshot=_as_float(row.score_snapshot),
            rank_snapshot=_as_int(row.rank_snapshot),
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            version=int(row.version or 1),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _to_event(self, row: MatchingReviewEvent) -> MatchingReviewEventItem:
        """流水 ORM → 接口模型。"""
        return MatchingReviewEventItem(
            id=int(row.id),
            review_id=int(row.review_id),
            from_status=row.from_status,  # type: ignore[arg-type]
            to_status=str(row.to_status),  # type: ignore[arg-type]
            reason_code=row.reason_code,  # type: ignore[arg-type]
            note=row.note,
            operator_id=row.operator_id,
            created_at=row.created_at,
        )


def _as_float(value: Any) -> float | None:
    """Decimal/str → float，None 原样返回（快照里可能是 Decimal 或空）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - 脏数据兜底
        return None


def _as_int(value: Any) -> int | None:
    """安全转 int；失败返回 None（不抛，避免一条脏数据打断整页查询）。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - 脏数据兜底
        return None


__all__ = ["MatchingReviewService", "validate_transition", "is_override"]
