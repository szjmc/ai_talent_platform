"""
功能：根据匹配结果维护岗位储备预警。
输入：岗位范围、阈值、分页或预警ID。
输出：预警列表、扫描计数和处理状态。
流程：扫描 → 判断规则 → 新建/沿用 → 可选自动解决。
边界：预警是派生数据；单次候选范围不代表全库，非零储备人数受输出筛选影响。

标签：[Agent组] [P1]（储备预警生成与扫描，需求模块五）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.reserve_warning import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    WARNING_POSITION_LONG_VACANT,
    WARNING_POSITION_NO_OUTPUT,
    WARNING_POSITION_SHORTAGE,
    ReserveWarning,
)
from app.repositories.matching_repository import MatchingRepository
from app.repositories.position_repository import PositionRepository
from app.repositories.warning_repository import WarningRepository
from app.schemas.warning import (
    ReserveWarningItem,
    WarningScanRequest,
    WarningScanResponse,
)

logger = logging.getLogger(__name__)


def _decision_of(hard_results: list[dict[str, Any]] | None) -> str:
    """从落库的四态明细聚合整体判定（FAIL > NEEDS_REVIEW > UNKNOWN > PASS）。"""
    statuses = [str(raw.get("status")) for raw in (hard_results or []) if raw.get("status")]
    if "FAIL" in statuses:
        return "FAIL"
    if "NEEDS_REVIEW" in statuses:
        return "NEEDS_REVIEW"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    return "PASS"


class ReserveWarningService:
    """储备预警业务服务。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        position_repo: PositionRepository | None = None,
        matching_repo: MatchingRepository | None = None,
        warning_repo: WarningRepository | None = None,
    ) -> None:
        self.db = db
        self.position_repo = position_repo or PositionRepository()
        self.matching_repo = matching_repo or MatchingRepository()
        self.warning_repo = warning_repo or WarningRepository()

    # ---------------------------------------------------------------- 扫描
    async def scan(
        self, payload: WarningScanRequest, *, current_user_id: int | None = None
    ) -> WarningScanResponse:
        """扫描开放岗位并维护预警记录（幂等 + 自动闭环）。"""
        position_ids = payload.position_ids
        if position_ids:
            requested = [int(pid) for pid in position_ids if int(pid) > 0]
            positions = await self.position_repo.get_by_ids(self.db, requested)
            # *kwq* 修改：与「扫描开放岗位」的文档口径对齐——显式指定 ID 时同样
            # 过滤掉非开放状态（closed/paused），避免对已关闭岗位产出误导性预警。
            positions = [p for p in positions if str(getattr(p, "status", "")) == "open"]
        else:
            positions = list(
                await self.position_repo.list(self.db, filters={"status": "open"})
            )

        now = datetime.now()
        vacant_deadline = now - timedelta(days=int(payload.long_vacant_days))
        created = resolved = unchanged = 0
        current_keys: set[tuple[str, int]] = set()
        touched_position_ids: list[int] = []

        for position in positions:
            pid = int(position.id)
            touched_position_ids.append(pid)
            findings = await self._evaluate(
                position,
                shortage_threshold=int(payload.shortage_threshold),
                vacant_deadline=vacant_deadline,
            )
            for finding in findings:
                key = (finding["warning_type"], pid)
                current_keys.add(key)
                existing = await self.warning_repo.get_unresolved(
                    self.db, warning_type=finding["warning_type"], position_id=pid
                )
                if existing is not None:
                    # 沿用既有记录：刷新明细与触发批次，保持 resolved=False
                    existing.severity = finding["severity"]
                    existing.message = finding["message"]
                    existing.detail = finding["detail"]
                    existing.batch_id = finding["batch_id"]
                    unchanged += 1
                else:
                    self.db.add(
                        ReserveWarning(
                            warning_type=finding["warning_type"],
                            severity=finding["severity"],
                            position_id=pid,
                            talent_id=None,
                            batch_id=finding["batch_id"],
                            message=finding["message"],
                            detail=finding["detail"],
                        )
                    )
                    created += 1

        # 数据闭环：这批岗位中「已不再成立」的未解决预警自动解决
        if payload.auto_resolve:
            stale = await self.warning_repo.list_unresolved_by_positions(
                self.db, touched_position_ids
            )
            for warning in stale:
                if (warning.warning_type, int(warning.position_id or 0)) not in current_keys:
                    await self.warning_repo.resolve(self.db, warning, by_user_id=None)
                    resolved += 1

        await self.db.flush()
        items = await self._items_for(touched_position_ids)
        logger.info(
            "warning.scan.done scanned=%s created=%s resolved=%s unchanged=%s",
            len(positions),
            created,
            resolved,
            unchanged,
        )
        return WarningScanResponse(
            positions_scanned=len(positions),
            created=created,
            resolved=resolved,
            unchanged=unchanged,
            items=items,
        )

    async def _evaluate(
        self, position: Any, *, shortage_threshold: int, vacant_deadline: datetime
    ) -> list[dict[str, Any]]:
        """对单个岗位产出预警发现（0~1 条）。"""
        # 优先批次快照：零输出也可见；旧批次仍兼容明细推导。
        snapshot = await self.matching_repo.latest_snapshot(self.db, int(position.id))
        # *kwq* 新增（WARN-001）：把「统计口径」显式带出。
        # 原因：pass_count 统计的是「最新批次输出明细」，而明细只含 top_k / min_score
        #      过滤后的候选人。若阈值 > top_k，该岗位永远不可能达标 —— 属于系统性误报。
        #      这里不改变判定公式，只把口径与上限暴露出来，避免静默给出错误结论。
        scope: dict[str, Any] = {}
        output_cap: int | None = None
        scope_min_score: float | None = None
        if snapshot is not None and isinstance(getattr(snapshot, "request_scope", None), dict):
            scope = dict(snapshot.request_scope)
            try:
                output_cap = int(scope["top_k"]) if scope.get("top_k") is not None else None
            except (TypeError, ValueError):
                output_cap = None
            try:
                scope_min_score = (
                    float(scope["min_score"]) if scope.get("min_score") is not None else None
                )
            except (TypeError, ValueError):
                scope_min_score = None

        if snapshot is not None:
            # *kwq* 修改：response/stats 改为容错读取。历史快照可能缺 stats 键
            # （旧版本落库 / 手工迁移数据），此前直接下标会 KeyError 使整轮 scan 500。
            response = snapshot.response if isinstance(snapshot.response, dict) else {}
            snapshot_top_stats = dict(response.get("stats") or {})
            output = int(snapshot_top_stats.get("output") or 0)
            batch_id = snapshot.batch_id
            if output == 0:
                return [{"warning_type": WARNING_POSITION_NO_OUTPUT, "severity": SEVERITY_HIGH,
                         "batch_id": batch_id, "message": f"岗位《{position.title}》最新批次无输出候选人",
                         "detail": {"output": 0, "candidates": int(snapshot_top_stats.get("candidates") or 0),
                                    "excluded_count": len(response.get("excluded", [])),
                                    "request_scope": snapshot.request_scope,
                                    "scope_note": "仅针对本次评估范围，不代表全库无人合适"}}]
        records = await self.matching_repo.latest_by_position(self.db, int(position.id))

        # *kwq* 修改（WARN-001）：统计来源三态收敛，避免「有快照但明细缺失」被误报为从未匹配。
        #   ① 有明细      → 以明细为准（states / hard_results 聚合）
        #   ② 无明细但有快照输出 → 用快照 items 还原（历史迁移 / 明细清理场景）
        #   ③ 两者皆无    → 判定「从未执行匹配」（长期空缺，计时基准优先 publish_at）
        snapshot_stats = (
            dict(snapshot.response.get("stats") or {}) if snapshot is not None else {}
        )
        snapshot_output = int(snapshot_stats.get("output") or 0)

        if records:
            stats = (records[0].stats or {}) if records[0].stats else {}
            output = int(stats["output"]) if stats.get("output") is not None else len(records)
            pass_count = sum(
                1 for record in records if _decision_of(record.hard_results) == "PASS"
            )
            batch_id = str(records[0].batch_id) if records else None
        elif snapshot is not None and snapshot_output > 0:
            items = list(snapshot.response.get("items") or [])
            stats = snapshot_stats
            output = len(items)
            pass_count = sum(1 for it in items if str(it.get("decision")) == "PASS")
            batch_id = snapshot.batch_id
        else:
            # 长期空缺的计时基准优先用 publish_at（对外发布日），
            # 缺失时回退 created_at，并在 detail 中标注实际基准，避免口径不明。
            publish_at = getattr(position, "publish_at", None)
            basis = "publish_at" if publish_at else "created_at"
            opened_at = publish_at or getattr(position, "created_at", None)
            if opened_at and opened_at < vacant_deadline:
                days = (datetime.now() - opened_at).days
                return [
                    {
                        "warning_type": WARNING_POSITION_LONG_VACANT,
                        "severity": SEVERITY_MEDIUM,
                        "batch_id": None,
                        "message": f"岗位《{position.title}》开放超过 {days} 天从未执行匹配",
                        "detail": {
                            "never_matched": True,
                            "open_days": days,
                            "open_basis": basis,
                        },
                    }
                ]
            return []

        if output <= 0:
            excluded = stats.get("excluded") or []
            return [
                {
                    "warning_type": WARNING_POSITION_NO_OUTPUT,
                    "severity": SEVERITY_HIGH,
                    "batch_id": batch_id,
                    "message": f"岗位《{position.title}》最新匹配批次无可用候选人",
                    "detail": {
                        "output": 0,
                        "excluded_count": len(excluded),
                        "candidates": int(stats.get("candidates") or 0),
                    },
                }
            ]
        if pass_count < shortage_threshold:
            # *kwq* 新增（WARN-001）：阈值 vs 输出上限的一致性判断。
            # 说明：不改变「pass_count < threshold 即预警」的既有判定，
            #      只在阈值超过统计上限时显式标注「口径受限」，避免 HR 把口径问题当成人选问题。
            cap_limited = output_cap is not None and shortage_threshold > max(output_cap, output)
            suffix = (
                f"（注意：本批次输出上限 {output_cap} 人，阈值 {shortage_threshold} 超出统计口径，"
                "结果仅供参考；请调低阈值或扩大 top_k 后重跑）"
                if cap_limited
                else ""
            )
            return [
                {
                    "warning_type": WARNING_POSITION_SHORTAGE,
                    "severity": SEVERITY_MEDIUM,
                    "batch_id": batch_id,
                    "message": (
                        f"岗位《{position.title}》储备不足：通过 {pass_count} 人，"
                        f"低于阈值 {shortage_threshold}{suffix}"
                    ),
                    "detail": {
                        "output": output,
                        "pass_count": pass_count,
                        "threshold": shortage_threshold,
                        "unknown": int(stats.get("unknown") or 0),
                        "needs_review": int(stats.get("needs_review") or 0),
                        # 口径留痕：通过人数只统计「该批次输出候选人」，不是全库候选池
                        "pass_count_basis": "latest_batch_output",
                        "output_cap": output_cap,
                        "scope_min_score": scope_min_score,
                        "threshold_exceeds_cap": cap_limited,
                    },
                }
            ]
        return []

    # ---------------------------------------------------------------- 查询
    async def page_list(
        self,
        page: PageParams,
        *,
        warning_type: str | None = None,
        position_id: int | None = None,
        resolved: bool | None = None,
    ) -> PageResult:
        """预警分页（按产生时间倒序）。"""
        filters: dict[str, Any] = {}
        if warning_type:
            filters["warning_type"] = warning_type
        if position_id is not None:
            filters["position_id"] = position_id
        if resolved is not None:
            filters["resolved"] = resolved
        # 步骤1：排序由PageParams传递，不能向公共paginate添加不存在的参数。
        result = await self.warning_repo.paginate(
            self.db, page, filters=filters or None
        )
        # 步骤2：ORM记录转接口模型并批量补岗位名称，避免响应序列化失败。
        titles = await self._position_titles(
            {int(item.position_id) for item in result.items if item.position_id}
        )
        items = [await self._to_item(item, titles) for item in result.items]
        return PageResult[ReserveWarningItem](
            items=items, total=result.total, page=result.page,
            page_size=result.page_size, pages=result.pages,
        )

    async def resolve(
        self,
        warning_id: int,
        *,
        note: str | None = None,
        current_user_id: int | None = None,
    ) -> ReserveWarningItem:
        """人工解决预警。"""
        warning = await self.warning_repo.get_by_id(self.db, warning_id)
        if warning is None or warning.is_deleted:
            raise NotFoundError(f"预警不存在（id={warning_id}）")
        if warning.resolved:
            return await self._to_item(warning)
        await self.warning_repo.resolve(self.db, warning, by_user_id=current_user_id)
        if note:
            detail = dict(warning.detail or {})
            detail["resolve_note"] = note
            warning.detail = detail
        await self.db.flush()
        logger.info("warning.resolved id=%s by=%s", warning_id, current_user_id)
        return await self._to_item(warning)

    # ---------------------------------------------------------------- 组装
    async def _items_for(self, position_ids: list[int]) -> list[ReserveWarningItem]:
        """取这批岗位当前的未解决预警，组装为响应条目。"""
        warnings = await self.warning_repo.list_unresolved_by_positions(
            self.db, position_ids
        )
        warnings.sort(key=lambda w: w.id or 0, reverse=True)
        titles = await self._position_titles({int(w.position_id) for w in warnings if w.position_id})
        return [await self._to_item(warning, titles) for warning in warnings]

    async def _to_item(
        self,
        warning: ReserveWarning,
        titles: dict[int, str] | None = None,
    ) -> ReserveWarningItem:
        titles = titles or {}
        pid = int(warning.position_id) if warning.position_id else None
        if pid is not None and pid not in titles:
            position = await self.position_repo.get_by_id(self.db, pid)
            if position is not None:
                titles[pid] = position.title
        return ReserveWarningItem(
            id=int(warning.id or 0),
            warning_type=warning.warning_type,  # type: ignore[arg-type]
            severity=warning.severity,  # type: ignore[arg-type]
            position_id=pid,
            position_title=titles.get(pid) if pid is not None else None,
            talent_id=int(warning.talent_id) if warning.talent_id else None,
            batch_id=warning.batch_id,
            message=warning.message,
            detail=warning.detail,
            resolved=bool(warning.resolved),
            resolved_at=warning.resolved_at,
            created_at=warning.created_at,
        )

    async def _position_titles(self, position_ids: set[int]) -> dict[int, str]:
        titles: dict[int, str] = {}
        for pid in position_ids:
            position = await self.position_repo.get_by_id(self.db, pid)
            if position is not None:
                titles[pid] = position.title
        return titles


__all__ = ["ReserveWarningService"]
