"""
功能：匹配业务入口与结果保存。
输入：MatchRunRequest、当前用户ID、查询条件。
输出：匹配报告、批次快照、历史记录。
流程：调用流水线 → 保存人才明细及批次快照 → 返回报告。
边界：不处理HTTP；请求数据库依赖统一提交或回滚。零输出也保存快照。

标签：[Agent组] [P1]（匹配业务编排与结果落库）
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.workflows.matching_workflow import run_matching
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.pagination import PageParams, build_page_result
from app.core.response import PageResult
from app.models.matching import MatchingRecord
# *kwq* 新增（CMP-002）：状态中文名统一取常量，避免前后端各写一套文案。
from app.models.matching_review import REVIEW_STATUS_LABELS
from app.models.talent import Talent
from app.patterns.strategy import MATCH_DEFAULT_WEIGHTS
from app.repositories.matching_repository import MatchingRepository
# *kwq* 新增（CMP-002）：横向对比要带出 HR 复核状态，只读本域新建表。
from app.repositories.matching_review_repository import MatchingReviewRepository
from app.repositories.position_repository import PositionRepository
from app.repositories.talent_repository import TalentRepository
from app.schemas.matching import (
    ExcludedItem,
    HardStatus,
    MatchCompareRequest,
    MatchCompareResponse,
    MatchCompareReview,
    MatchCompareRow,
    MatchExplainRequest,
    MatchExplanation,
    MatchHistoryItem,
    MatchItem,
    MatchReverseItem,
    MatchReverseRequest,
    MatchReverseResponse,
    MatchReverseSkipped,
    MatchRunRequest,
    MatchRunResponse,
    MatchStats,
)
from app.services.evidence_builder import build_match_evidence
from app.services.explanation_service import ExplanationService
from app.services.gap_service import compare_gap_overview
from app.services.matching_pipeline import (
    MATCH_RULE_CONFIG,
    MatchingPipeline,
    defaulted_dimensions,
)
from app.services.matching_rules import (
    apply_unknown_penalty,
    compute_data_completeness,
    judge_hard_conditions,
)
from app.services.position_profile_service import PositionProfileService

logger = logging.getLogger(__name__)


class MatchingService:
    """人岗匹配业务服务。"""

    def __init__(
        self,
        db: AsyncSession,
        repo: MatchingRepository | None = None,
        position_repo: PositionRepository | None = None,
        talent_repo: TalentRepository | None = None,
        pipeline: MatchingPipeline | None = None,
        explanation_service: ExplanationService | None = None,
        review_repo: MatchingReviewRepository | None = None,
    ) -> None:
        self.db = db
        self.repo = repo or MatchingRepository()
        self.position_repo = position_repo or PositionRepository()
        self.talent_repo = talent_repo or TalentRepository()
        self.review_repo = review_repo or MatchingReviewRepository()
        self.profile_service = PositionProfileService(db, self.position_repo)
        self.explanation_service = explanation_service or ExplanationService()
        self.pipeline = pipeline or MatchingPipeline(
            db,
            position_repo=self.position_repo,
            talent_repo=self.talent_repo,
            profile_service=self.profile_service,
            explanation_service=self.explanation_service,
        )

    # ================================================================ 执行匹配
    # *kwq* 新增：执行匹配并落库。
    async def run(
        self, payload: MatchRunRequest, *, current_user_id: int | None = None
    ) -> MatchRunResponse:
        """
        功能：执行一次正式匹配并保存结果。
        输入：匹配请求和用户ID。
        输出：包含batch_id的MatchRunResponse。
        步骤：构建State → 执行流水线 → 保存明细及快照 → 返回。
        """
        batch_id = uuid.uuid4().hex
        matched_at = datetime.now()

        state = {
            "position_id": payload.position_id,
            "talent_ids": payload.talent_ids,
            "top_k": payload.top_k,
            "min_score": payload.min_score,
            "strategy": payload.strategy,
            "with_explanation": payload.with_explanation,
            "requirement_version": payload.requirement_version,
            "weights": payload.weights,  # *kwq* 新增：单次请求权重覆盖（None 不覆盖）
            "batch_id": batch_id,
            "matched_by": current_user_id,
            "errors": [],
        }

        final = await run_matching(self.pipeline, state)  # type: ignore[arg-type]

        items = list(final.get("items") or [])
        stats = MatchStats(**{k: int(v) for k, v in (final.get("stats") or {}).items() if k in MatchStats.model_fields})
        excluded = list(final.get("excluded") or [])
        errors = list(final.get("errors") or [])

        await self._persist(
            batch_id=batch_id,
            position_id=payload.position_id,
            strategy=payload.strategy,
            requirement_version=int(final.get("profile_version") or 1),
            recall_mode=str(final.get("recall_mode") or "sql_fallback"),
            matched_by=current_user_id,
            matched_at=matched_at,
            items=items,
            stats=stats,
            warnings=errors,
            explanations=dict(final.get("explanations") or {}),
            weights=(final.get("position_view") or {}).get("weights"),
            # *kwq* 新增（MATCH-014）：淘汰明细随批次一起落库。
            # 原因：回归时发现「回读最新批次」的 excluded 恒为空 —— 淘汰原因只活在本次响应里，
            #      页面一刷新就丢失，事后无法回答「当时为什么没让他出现」，与「结果可追溯」不符。
            # 影响范围：仅写入 stats JSON 的 excluded 键（不新增列、不改表结构）；读侧在 get_latest 还原。
            excluded=excluded,
        )

        response = MatchRunResponse(
            batch_id=batch_id,
            position_id=payload.position_id,
            position_title=final.get("position_title"),
            requirement_version=int(final.get("profile_version") or 1),
            recall_mode=str(final.get("recall_mode") or "sql_fallback"),
            matched_at=matched_at,
            stats=stats,
            items=items,
            excluded=excluded,
            # *kwq* 新增：回传本次实际生效的权重（岗位配置或请求覆盖，归一化后）
            weights_used=(final.get("position_view") or {}).get("weights"),
        )
        # 步骤：即使items为空也保存批次，最新查询不再退回上次成功结果。
        await self.repo.save_snapshot(
            self.db, response=response.model_dump(mode="json"),
            request_scope={"talent_ids": payload.talent_ids, "top_k": payload.top_k,
                           "min_score": payload.min_score, "strategy": payload.strategy,
                           "warnings": errors},
        )
        logger.info(
            "matching.run.done batch_id=%s position_id=%s output=%s excluded=%s warnings=%s",
            batch_id,
            payload.position_id,
            len(items),
            len(excluded),
            len(errors),
        )
        return response

    # ================================================================ 双向匹配
    # *kwq* 新增：人才找岗位（需求 §06）。不落库（只读试算），解释走规则兜底或按需开外部通道。
    async def run_reverse(
        self, payload: MatchReverseRequest, *, current_user_id: int | None = None
    ) -> MatchReverseResponse:
        """双向匹配：给定人才，返回适合的岗位排序与淘汰原因。"""
        final = await self.pipeline.run_reverse(
            {
                "talent_id": payload.talent_id,
                "position_ids": payload.position_ids,
                "top_k": payload.top_k,
                "min_score": payload.min_score,
                "strategy": payload.strategy,
                "with_explanation": payload.with_explanation,
                "weights": payload.weights,
                "matched_by": current_user_id,
                "errors": [],
            }  # type: ignore[typeddict-unknown-key]
        )
        talent = final.get("talent")
        logger.info(
            "matching.reverse.done talent_id=%s evaluated=%s output=%s skipped=%s excluded=%s",
            payload.talent_id,
            final.get("positions_evaluated"),
            len(final.get("items") or []),
            len(final.get("skipped") or []),
            len(final.get("excluded") or []),
        )
        return MatchReverseResponse(
            talent_id=payload.talent_id,
            talent_name=getattr(talent, "name", None),
            positions_evaluated=int(final.get("positions_evaluated") or 0),
            skipped=list(final.get("skipped") or []),
            excluded=list(final.get("excluded") or []),
            recall_mode=str(final.get("recall_mode") or "sql_fallback"),
            weights_used=final.get("weights_used"),
            # *kwq* 新增（REV-001）：多岗位权重不一致时的口径说明，避免调用方误用 weights_used
            weights_note=final.get("weights_note"),
            items=list(final.get("items") or []),
            # *kwq* 新增：把逐岗位收集的非阻断提示（岗位暂停 / 向量降级原因）回传给调用方，
            # 不再只写日志；字段为增量可选，不影响既有调用方。
            errors=list(final.get("errors") or []),
        )

    # ---------------------------------------------------------------- 持久化
    async def _persist(
        self,
        *,
        batch_id: str,
        position_id: int,
        strategy: str,
        requirement_version: int,
        recall_mode: str,
        matched_by: int | None,
        matched_at: datetime,
        items: Sequence[MatchItem],
        stats: MatchStats,
        warnings: Sequence[str],
        explanations: dict[int, Any] | None = None,
        weights: dict[str, float] | None = None,
        excluded: Sequence[Any] | None = None,
    ) -> list[MatchingRecord]:
        """
        功能：写入当前批次的人才明细。
        输入：排名、分数、解释、统计和淘汰信息。
        输出：MatchingRecord列表；无输出时为空。
        步骤：组装记录 → 批量flush；零输出由run另存快照。
        """
        if not items:
            # 无人才明细时不写matching_records；run仍会写matching_batches快照。
            logger.warning(
                "matching.details.empty 无人才明细，批次由快照保留 batch_id=%s excluded=%s",
                batch_id,
                len(excluded or []),
            )
            return []

        stats_payload = stats.model_dump()
        stats_payload["warnings"] = list(warnings)
        # *kwq* 新增：把本次使用的维度权重一并留痕。
        # 原因：业务要求「评分与排序规则可追溯」；只有维度分、没有权重，
        #      事后无法复算出同一个总分（权重可能被岗位配置调整）。
        stats_payload["weights"] = dict(weights or {})
        # *kwq* 新增（MATCH-014）：淘汰明细留痕（talent_id/姓名/状态/逐条原因）。
        # 说明：复用既有 stats JSON 列而非新增列，避免在交付阶段引入未经审批的 DDL；
        #      代价是同一批次各行人手一份副本（候选人规模可控，可接受）。
        stats_payload["excluded"] = [
            raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
            for raw in (excluded or [])
        ]
        # *kwq* 新增（GAP-001）：差距矩阵随批次落库（复用 stats JSON 列，不加列、无 DDL）。
        # 说明：键为 talent_id 字符串；旧数据无此键，读侧按缺省处理，天然兼容。
        gap_payload = {
            str(item.talent_id): [row.model_dump(mode="json") for row in (item.gap_matrix or [])]
            for item in items
            if getattr(item, "gap_matrix", None)
        }
        if gap_payload:
            stats_payload["gap_matrices"] = gap_payload
        explanation_map = explanations or {}

        records: list[MatchingRecord] = []
        for item in items:
            # *kwq* 修改：优先落库「校验后的完整解释」（含 rejected_claims / pending_verification），
            # 而不是从 MatchItem 的展示字段反推 —— 反推会丢掉剔除记录与待核实清单，
            # 导致事后审计无法回答「当时模型说了什么、为什么被删」。
            outcome = explanation_map.get(int(item.talent_id))
            explanation_payload = (
                outcome.explanation.model_dump(mode="json") if outcome is not None else None
            )
            records.append(
                MatchingRecord(
                    batch_id=batch_id,
                    position_id=position_id,
                    talent_id=item.talent_id,
                    score=Decimal(str(round(float(item.score), 2))),
                    dimension_scores=dict(item.dimension_scores),
                    # highlights 列沿用既有语义：事实型「命中技能」
                    highlights=list(item.matched_skills),
                    gaps=list(item.gaps),
                    suggestion=(item.suggestion or "")[:512] or None,
                    strategy=strategy,
                    model_name=item.explanation_source,
                    matched_by=matched_by,
                    matched_at=matched_at,
                    requirement_version=requirement_version,
                    recall_mode=recall_mode,
                    hard_results=[raw.model_dump(mode="json") for raw in item.hard_results],
                    explanation=explanation_payload,
                    stats=stats_payload,
                )
            )
        return await self.repo.save_batch(self.db, records)

    # ================================================================ 查询
    async def get_batch_snapshot(self, batch_id: str) -> MatchRunResponse:
        """
        功能：按批次追溯报告，包含零结果。
        输入：batch_id。
        输出：MatchRunResponse。
        步骤：读取快照 → 校验响应；旧批次无快照则明确报错。
        """
        snapshot = await self.repo.snapshot_by_id(self.db, batch_id)
        if snapshot is None:
            raise NotFoundError("该批次无快照（旧批次请通过岗位最新结果或人才历史查询）")
        return MatchRunResponse.model_validate(snapshot.response)

    # ================================================================ 横向对比
    # *kwq* 新增（CMP-001）：多候选人横向对比。
    # 设计红线：只读批次快照，不重算分数、不落库、不产生新批次；
    #          所有字段原样取自 MatchItem，保证与单条结果逐字段一致。
    async def compare(self, payload: MatchCompareRequest) -> MatchCompareResponse:
        """
        功能：同一批次内 2~8 名候选人的横向对比。
        输入：批次ID + 人才ID列表 + 是否含差距矩阵。
        输出：对比行（按原名次排序）与未参与人才说明。
        步骤：读快照 → 建索引 → 按请求顺序取行 → 排序；缺失人才进 notes。
        """
        snapshot = await self.repo.snapshot_by_id(self.db, payload.batch_id)
        if snapshot is None:
            raise NotFoundError(f"批次不存在或已删除（batch_id={payload.batch_id}）")

        run = MatchRunResponse.model_validate(snapshot.response)
        if not run.items:
            raise BusinessRuleError("该批次没有候选人结果（零输出），无法进行横向对比")

        index = {int(item.talent_id): item for item in run.items}
        # *kwq* 新增（CMP-002）：一次查回全部复核记录，禁止在循环里逐人查（N+1）。
        # 无复核记录属于正常状态（还没人看过），不能让对比接口因此报错。
        review_map = await self._review_map(
            batch_id=payload.batch_id,
            talent_ids=[int(tid) for tid in payload.talent_ids],
        )
        rows: list[MatchCompareRow] = []
        notes: list[str] = []
        for talent_id in payload.talent_ids:
            item = index.get(int(talent_id))
            if item is None:
                notes.append(
                    f"人才 {talent_id} 不在该批次结果中（可能未参与评估、被硬条件淘汰或超出输出上限）"
                )
                continue
            rows.append(
                MatchCompareRow(
                    talent_id=item.talent_id,
                    talent_name=item.talent_name,
                    rank=item.rank,
                    score=item.score,
                    decision=item.decision,
                    dimension_scores=dict(item.dimension_scores),
                    data_completeness=item.data_completeness,
                    defaulted_dimensions=list(item.defaulted_dimensions),
                    hard_results=list(item.hard_results),
                    matched_skills=list(item.matched_skills),
                    highlights=list(item.highlights),
                    gaps=list(item.gaps),
                    suggestion=item.suggestion,
                    explanation_source=item.explanation_source,
                    gap_matrix=(list(item.gap_matrix) if payload.include_gap else []),
                    review=review_map.get(int(item.talent_id)),
                )
            )

        if not rows:
            raise BusinessRuleError("所选人才均不在该批次结果中，无法进行横向对比")

        rows.sort(key=lambda row: (row.rank if row.rank > 0 else 1 << 30, -row.score))

        # *kwq* 新增（CMP-002）：缺口横向概览。
        # 注意概览用的是「快照里的完整矩阵」，而不是 rows 里被 include_gap 裁掉的副本——
        # 否则不开明细时概览会全变成 0，比不返回更容易误导。
        # 为什么要按 rows 顺序取：概览要与对比行一一对齐（前端按索引配图），
        # 而快照 items 的顺序不保证等于 rank 顺序。
        gap_source = {
            int(row.talent_id): list(index[int(row.talent_id)].gap_matrix) for row in rows
        }
        # 判定标准是「有没有任何一行矩阵」，而不是「dict 是否为空」：
        # 历史批次每人都是空列表，此时给一堆全 0 的统计比不返回更误导。
        has_matrix = any(gap_source.values())
        gap_stats, common_gaps = (
            compare_gap_overview(gap_source) if has_matrix else ([], [])
        )
        gap_note: str | None = None
        if not has_matrix:
            # *kwq* 修复（QA-002）：矩阵为空有三种原因，不能一律说成「未生成」。
            #   soft 策略：本就不做硬条件判定 → 不存在矩阵，不是数据缺失；
            #   历史批次：功能上线前生成 → 需重跑才有；
            #   其它（画像无硬性要求等）→ 归入历史口径并提示重跑。
            scope = getattr(snapshot, "request_scope", None) or {}
            strategy = str(scope.get("strategy") or "")
            if strategy == "soft":
                gap_note = (
                    "该批次使用 soft 策略（只做加权打分、不做硬条件判定），"
                    "因此不产出能力差距矩阵；如需差距明细请改用 hard / hybrid 策略重跑"
                )
            else:
                gap_note = (
                    "该批次未生成能力差距矩阵（生成于矩阵功能上线前或画像无硬性条件），"
                    "如需查看请重跑该岗位匹配"
                )
        elif not payload.include_gap:
            gap_note = "概览基于快照矩阵计算；逐条明细需 include_gap=true 才会返回"

        logger.info(
            "matching.compare.done batch_id=%s requested=%s rows=%s notes=%s reviews=%s common_gaps=%s",
            payload.batch_id, len(payload.talent_ids), len(rows), len(notes),
            len(review_map), len(common_gaps),
        )
        return MatchCompareResponse(
            batch_id=payload.batch_id,
            position_id=run.position_id,
            position_title=run.position_title,
            requirement_version=run.requirement_version,
            matched_at=run.matched_at,
            rows=rows,
            notes=notes,
            gap_stats=gap_stats,
            common_gaps=common_gaps,
            gap_note=gap_note,
        )

    async def _review_map(
        self, *, batch_id: str, talent_ids: list[int]
    ) -> dict[int, MatchCompareReview]:
        """批量取复核状态并转成接口模型（一次 SQL，返回 {talent_id: review}）。

        单独抽方法的原因：
            1. 对比接口与后续「批次结果页」都会用到，避免两处各写一遍查询；
            2. 转换逻辑集中，响应字段口径只维护一份。
        """
        records = await self.review_repo.list_by_batch_talents(
            self.db, batch_id=batch_id, talent_ids=talent_ids
        )
        result: dict[int, MatchCompareReview] = {}
        for record in records:
            status = str(record.review_status)
            if status not in REVIEW_STATUS_LABELS:
                # 库里出现未知状态（历史脏数据 / 手工改库）时跳过这一条，
                # 而不是让整个对比接口 500 —— 少一个状态标记远好过整页打不开。
                logger.warning(
                    "matching.compare.unknown_review_status talent_id=%s status=%s",
                    record.talent_id, status,
                )
                continue
            result[int(record.talent_id)] = MatchCompareReview(
                review_id=int(record.id),
                review_status=status,  # type: ignore[arg-type]
                status_label=REVIEW_STATUS_LABELS.get(status),
                reason_code=record.reason_code,
                note=record.note,
                decided_by=record.decided_by,
                decided_at=record.decided_at,
                version=int(record.version or 1),
            )
        return result

    async def get_latest(self, position_id: int) -> MatchRunResponse:
        """
        功能：读取岗位最近一次报告。
        输入：岗位ID。
        输出：最新快照或历史兼容报告。
        步骤：优先快照 → 无快照时还原旧明细；无记录时报错。
        """
        # 新批次直接还原不可变响应快照；旧数据无快照时沿用历史兼容路径。
        snapshot = await self.repo.latest_snapshot(self.db, position_id)
        if snapshot is not None:
            return MatchRunResponse.model_validate(snapshot.response)
        records = await self.repo.latest_by_position(self.db, position_id)
        if not records:
            raise NotFoundError(f"该岗位还没有匹配记录（position_id={position_id}）")

        position = await self.position_repo.get_by_id(self.db, position_id)
        talents = await self.talent_repo.get_by_ids(self.db, [r.talent_id for r in records])
        talent_map = {int(t.id): t for t in talents}

        items = [
            self._item_from_record(record, talent_map.get(int(record.talent_id)), rank=index)
            for index, record in enumerate(records, start=1)
        ]
        first = records[0]
        stats = MatchStats(
            **{
                k: int(v)
                for k, v in (first.stats or {}).items()
                if k in MatchStats.model_fields and isinstance(v, (int, float))
            }
        ) if isinstance(first.stats, dict) else MatchStats()
        stats.output = len(items)
        recall_mode = first.recall_mode if first.recall_mode in {"vector", "sql_fallback"} else "sql_fallback"

        # *kwq* 新增（MATCH-014）：还原淘汰明细。
        # 原因：与写入侧对称——否则 GET /matchings/positions/{id} 的 excluded 恒为 []，
        #      与刚跑完那次的结果不一致（前端会以为没人被淘汰）。
        # 容错：历史批次没有该键、或结构不合法时降级为空列表，不让脏数据 500。
        excluded: list[ExcludedItem] = []
        raw_excluded = (first.stats or {}).get("excluded") if isinstance(first.stats, dict) else None
        for raw in raw_excluded or []:
            if not isinstance(raw, dict):
                continue
            try:
                excluded.append(ExcludedItem(**raw))
            except Exception:  # noqa: BLE001 - 单条脏数据不应影响整批回读
                logger.warning("matching.latest.excluded.skip 脏数据 talent_id=%s", raw.get("talent_id"))

        return MatchRunResponse(
            batch_id=str(first.batch_id),
            position_id=position_id,
            position_title=(position.title if position else None),
            requirement_version=int(first.requirement_version or 1),
            recall_mode=recall_mode,  # type: ignore[arg-type]
            matched_at=first.matched_at,
            stats=stats,
            items=items,
            excluded=excluded,
        )

    @staticmethod
    def _item_from_record(
        record: MatchingRecord, talent: Talent | None, rank: int = 0
    ) -> MatchItem:
        """把库中记录还原成 MatchItem。"""
        explanation = record.explanation if isinstance(record.explanation, dict) else {}
        highlights = explanation.get("highlights") or []
        gaps = explanation.get("gaps") or []
        hard_results = [raw for raw in (record.hard_results or []) if isinstance(raw, dict)]
        # 只认已冻结的三个来源值，历史脏数据不允许把非法字符串塞进 Literal 字段
        source = record.model_name if record.model_name in {"dify", "llm_fallback", "rule_fallback"} else None
        # *kwq* 新增（MATCH-018）：回读时按已落库的四态明细还原整体判定，
        # 不重新计算（避免画像改动后同一批次回读结果发生变化，破坏可追溯性）。
        decision = HardStatus.PASS
        priority = {"UNKNOWN": 1, "NEEDS_REVIEW": 2, "FAIL": 3}
        strongest = 0
        for raw in hard_results:
            status = str(raw.get("status") or "")
            if priority.get(status, 0) > strongest:
                strongest = priority[status]
                decision = HardStatus(status)

        return MatchItem(
            talent_id=int(record.talent_id),
            talent_name=(talent.name if talent else None),
            rank=rank,
            decision=decision,
            score=float(record.score or 0),
            dimension_scores={
                str(k): float(v) for k, v in (record.dimension_scores or {}).items()
            },
            defaulted_dimensions=defaulted_dimensions(talent, None),
            data_completeness=compute_data_completeness(talent.to_dict() if talent else {}),
            matched_skills=[str(s) for s in (record.highlights or [])],
            highlights=[
                str(item.get("text"))
                for item in highlights
                if isinstance(item, dict) and item.get("text")
            ],
            gaps=[
                str(item.get("text"))
                for item in gaps
                if isinstance(item, dict) and item.get("text")
            ],
            hard_results=hard_results,
            suggestion=record.suggestion,
            explanation_source=source,
        )

    async def get_history_by_talent(
        self, talent_id: int, limit: int = 50
    ) -> list[MatchHistoryItem]:
        """某人才的历史匹配记录（按时间倒序）。"""
        records = await self.repo.history_by_talent(self.db, talent_id, limit=limit)
        # *kwq* 新增（MATCH-016）：补人才姓名（解引用一次即可，历史条目共用）。
        talent = await self.talent_repo.get_by_id(self.db, talent_id)
        talent_name = talent.name if talent is not None else None
        position_ids = {int(record.position_id) for record in records}
        titles: dict[int, str] = {}
        for pid in position_ids:
            position = await self.position_repo.get_by_id(self.db, pid)
            if position is not None:
                titles[pid] = position.title

        return [
            MatchHistoryItem(
                batch_id=str(record.batch_id),
                position_id=int(record.position_id),
                position_title=titles.get(int(record.position_id)),
                talent_id=int(record.talent_id),
                talent_name=talent_name,
                score=float(record.score or 0),
                strategy=record.strategy,
                requirement_version=record.requirement_version,
                matched_at=record.matched_at,
                explanation_source=record.model_name,
            )
            for record in records
        ]

    async def page_list(
        self,
        page: PageParams,
        *,
        position_id: int | None = None,
        talent_id: int | None = None,
        min_score: float | None = None,
    ) -> PageResult:
        """匹配记录分页（按批次时间倒序）。

        *kwq* 修改（MATCH-021）：支持岗位 / 人才 / 最低分过滤。
        修改原因：原实现只有分页参数，`?position_id=` 这类查询串会被静默丢弃，
                 列表页看起来「筛选成功」但拿的是全表，属静默错误。
        """
        filters: dict[str, Any] = {}
        if position_id is not None:
            filters["position_id"] = position_id
        if talent_id is not None:
            filters["talent_id"] = talent_id
        extra_where = (
            [MatchingRecord.score >= Decimal(str(min_score))] if min_score is not None else None
        )

        result = await self.repo.paginate(
            self.db, page, filters=(filters or None), extra_where=extra_where
        )
        records = list(result.items)
        position_ids = {int(record.position_id) for record in records}
        titles: dict[int, str] = {}
        for pid in position_ids:
            position = await self.position_repo.get_by_id(self.db, pid)
            if position is not None:
                titles[pid] = position.title

        # *kwq* 新增（MATCH-016）：补人才姓名，供列表页展示。
        talent_ids = {int(record.talent_id) for record in records}
        talents = await self.talent_repo.get_by_ids(self.db, list(talent_ids)) if talent_ids else []
        names = {int(t.id): t.name for t in talents}

        items = [
            MatchHistoryItem(
                batch_id=str(record.batch_id),
                position_id=int(record.position_id),
                position_title=titles.get(int(record.position_id)),
                talent_id=int(record.talent_id),
                talent_name=names.get(int(record.talent_id)),
                score=float(record.score or 0),
                strategy=record.strategy,
                requirement_version=record.requirement_version,
                matched_at=record.matched_at,
                explanation_source=record.model_name,
            ).model_dump()
            for record in records
        ]
        return build_page_result(items, result.total, page)

    # ================================================================ 解释
    # *kwq* 新增：对指定「岗位 × 人才」重算并生成解释。
    # 与 run 的区别：不写库、不受硬条件淘汰影响 —— HR 需要能问「这个人为什么被淘汰」。
    async def explain(self, payload: MatchExplainRequest) -> MatchExplanation:
        """
        功能：对指定岗位与人才重新生成解释。
        输入：MatchExplainRequest。
        输出：校验后的MatchExplanation。
        步骤：读取当前档案与确认画像 → 计算证据 → 生成并校验解释；不落库。
        """
        position = await self.position_repo.get_by_id(self.db, payload.position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={payload.position_id}）")
        if position.status == "closed":
            raise BusinessRuleError("岗位已关闭，不能生成匹配解释")

        profile, version = await self.profile_service.require_confirmed(
            payload.position_id, payload.requirement_version
        )
        talent = await self.talent_repo.get_by_id(self.db, payload.talent_id)
        if talent is None:
            raise NotFoundError(f"人才不存在（id={payload.talent_id}）")

        weights = self._weights(position)
        position_view = {
            "id": position.id,
            "title": position.title,
            "skill_tags": list(profile.required_skills),
            "requirements": profile.model_dump(mode="json"),
        }

        # 复用四态判定与打分（与跑批同口径），再构建证据、生成解释
        hard_results = judge_hard_conditions(
            talent,
            position_view,
            config=MATCH_RULE_CONFIG,
            position_id=payload.position_id,
            talent_id=payload.talent_id,
        )
        from app.ai.skills.matching_skill import compute_match_score

        scored = compute_match_score(talent.to_dict(), position_view, weights=weights)
        score = apply_unknown_penalty(
            float(scored.get("total_score") or 0), hard_results, MATCH_RULE_CONFIG
        )
        pack = build_match_evidence(
            position_view,
            talent,
            hard_results=hard_results,
            dimension_scores=scored.get("dimensions") or {},
            position_id=payload.position_id,
            talent_id=payload.talent_id,
            requirement_version=version,
        )
        outcome = await self.explanation_service.generate(pack, use_external=True)
        explanation = outcome.explanation

        if explanation.summary:
            explanation.summary = f"{explanation.summary}（综合分 {score:.2f}）"[:1000]
        logger.info(
            "matching.explain.done position_id=%s talent_id=%s source=%s rejected=%s",
            payload.position_id,
            payload.talent_id,
            explanation.source,
            len(explanation.rejected_claims),
        )
        return explanation

    @staticmethod
    def _weights(position: Any) -> dict[str, float]:
        """取岗位权重（非法配置回落默认值）。"""
        raw = position.requirements if isinstance(position.requirements, dict) else {}
        weights = raw.get("weights") if isinstance(raw, dict) else None
        if not isinstance(weights, dict):
            return dict(MATCH_DEFAULT_WEIGHTS)
        parsed: dict[str, float] = {}
        for key in MATCH_DEFAULT_WEIGHTS:
            try:
                value = float(weights.get(key))
            except (TypeError, ValueError):
                return dict(MATCH_DEFAULT_WEIGHTS)
            if value < 0:
                return dict(MATCH_DEFAULT_WEIGHTS)
            parsed[key] = value
        total = sum(parsed.values())
        if total <= 0:
            return dict(MATCH_DEFAULT_WEIGHTS)
        return {key: round(value / total, 6) for key, value in parsed.items()}


__all__ = ["MatchingService"]
