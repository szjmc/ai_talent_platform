"""
功能：岗位找人才的计算流水线。
输入：岗位ID、人才ID范围、策略、权重和已确认画像版本。
输出：候选排名、四态结果、证据包、解释和淘汰名单。
流程：加载画像 → 召回 → 判断评分 → 构建证据 → 解释 → 组装。
边界：节点只返回增量State；落库由MatchingService负责。

标签：[Agent组] [P0]（人岗匹配六节点流水线主体）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models.position import Position
from app.patterns.strategy import MATCH_DEFAULT_WEIGHTS, MatchStrategyFactory
from app.repositories.position_repository import PositionRepository
from app.repositories.talent_repository import TalentRepository
from app.schemas.matching import (
    ExcludedItem,
    GapRow,
    HardConditionResult,
    HardStatus,
    MatchEvidencePack,
    MatchItem,
    MatchReverseItem,
    MatchReverseSkipped,
    MatchStats,
)
from app.services.gap_service import build_gap_matrix
from app.schemas.position import JobProfileParsed
from app.services.evidence_builder import build_match_evidence
from app.services.explanation_service import ExplanationOutcome, ExplanationService
from app.services.matching_rules import (
    HardRuleConfig,
    aggregate_status,
    compute_data_completeness,
)
from app.services.position_profile_service import PositionProfileService

logger = logging.getLogger(__name__)

# *kwq* 新增：向量召回最低相似度（COSINE，低于该值的候选视为噪声丢弃）
MIN_VECTOR_SCORE = 0.2

# *kwq* 新增：单次 SQL 召回上限。
# 原因：全库召回必须设上限，否则数据量上来后一次请求会拉全表；
#      超限时会在 errors 中明确记录「已截断」，不静默少算。
DEFAULT_RECALL_LIMIT = 2000

# *kwq* 新增：解释并发度。
# 原因：批量解释逐个串行会让 20 个候选人的跑批拖到分钟级；
#      但并发过高会打爆模型配额，因此限流 3。
EXPLAIN_CONCURRENCY = 3

# *kwq* 新增：匹配链路统一的四态判定口径（B-5 暂定值，待业务确认）。
# 修改原因（对应实测发现的 P1 风险）：
#   talents.work_years 是 NOT NULL default 0，数据库层面无法区分「应届 0 年」与「档案未填」。
#   若沿用规则模块默认值（zero_work_years_as_review=False），
#   一个完全没填档案的人会被判定为「年限不足」直接淘汰 —— 这正是业务明令禁止的
#   「把缺失当不合格」错误淘汰。匹配入口统一改为保守口径：0 年 -> NEEDS_REVIEW（保留待核实）。
# 影响范围：仅匹配链路（run / explain）；规则层默认值未改动，其他调用方行为不变。
MATCH_RULE_CONFIG = HardRuleConfig(zero_work_years_as_review=True)


class MatchingState(TypedDict, total=False):
    """匹配流水线状态（LangGraph State）。

    total=False：节点只返回增量字段，未产出的键允许缺省。
    """

    # ---- 入参
    position_id: int
    talent_ids: list[int] | None
    top_k: int
    min_score: float
    strategy: str
    with_explanation: bool
    requirement_version: int | None
    # *kwq* 新增：单次请求权重覆盖（None 表示不覆盖，沿用岗位配置）
    weights: dict[str, float] | None
    batch_id: str
    matched_by: int | None

    # ---- 中间产物
    position_title: str
    profile: JobProfileParsed
    profile_version: int
    position_view: dict[str, Any]
    # *kwq* 新增（REV-001）：岗位对象预取缓存（避免每个岗位重复 get_by_id 查库）。
    position_cache: dict[int, Any]
    recall_mode: str
    candidates: list[Any]
    scored: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    stats: dict[str, int]
    packs: dict[int, MatchEvidencePack]
    explanations: dict[int, ExplanationOutcome]

    # ---- 输出
    items: list[MatchItem]
    errors: list[str]


# ------------------------------------------------------------------ 工具


def _normalize_weights(weights: dict[str, Any]) -> dict[str, float] | None:
    """校验并归一化四维权重；非法返回 None（由调用方决定回落默认还是拒绝）。

    归一化到 1.0，避免配置写 4/3/2/1 这类「比例」导致总分超过 100。
    """
    if not isinstance(weights, dict):
        return None
    parsed: dict[str, float] = {}
    for key in MATCH_DEFAULT_WEIGHTS:
        try:
            value = float(weights.get(key))
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        parsed[key] = value
    total = sum(parsed.values())
    if total <= 0:
        return None
    return {key: round(value / total, 6) for key, value in parsed.items()}


def _weights_from(position: Position) -> dict[str, float]:
    """从岗位 requirements.weights 取权重；非法或缺省时回落默认权重。"""
    raw = position.requirements if isinstance(position.requirements, dict) else {}
    weights = raw.get("weights") if isinstance(raw, dict) else None
    return _normalize_weights(weights) or dict(MATCH_DEFAULT_WEIGHTS)


def _position_view(position: Position, profile: JobProfileParsed) -> dict[str, Any]:
    """构造打分/判定共用的岗位视图。

    关键：结构化要求一律取「已确认画像」，而不是 positions.requirements 的历史残留，
          否则会出现「判定用新画像、打分用旧技能标签」的两套口径。
    """
    return {
        "id": position.id,
        "title": position.title,
        "skill_tags": list(profile.required_skills),
        "requirements": profile.model_dump(mode="json"),
        "weights": _weights_from(position),
    }


def defaulted_dimensions(talent: Any, profile: JobProfileParsed | None = None) -> list[str]:
    """列出「因档案缺数据而使用默认分」的维度（用于提示结论可信度）。

    profile 为空时（历史结果还原场景）只按档案字段判断，不假设岗位要求。
    """
    def _value(key: str) -> Any:
        if talent is None:
            return None
        if isinstance(talent, dict):
            return talent.get(key)
        return getattr(talent, key, None)

    result: list[str] = []
    if not _value("education"):
        result.append("education")
    if not _value("skill_tags"):
        result.append("skill")
    if (profile is not None and profile.min_work_years is not None) and not _value("work_years"):
        result.append("experience")
    if not _value("potential_level"):
        result.append("quality")
    return result


# ------------------------------------------------------------------ 流水线


class MatchingPipeline:
    """匹配流水线（每个方法对应一个 LangGraph 节点）。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        position_repo: PositionRepository | None = None,
        talent_repo: TalentRepository | None = None,
        profile_service: PositionProfileService | None = None,
        explanation_service: ExplanationService | None = None,
        recall_limit: int = DEFAULT_RECALL_LIMIT,
    ) -> None:
        self.db = db
        self.position_repo = position_repo or PositionRepository()
        self.talent_repo = talent_repo or TalentRepository()
        self.profile_service = profile_service or PositionProfileService(db, self.position_repo)
        self.explanation_service = explanation_service or ExplanationService()
        self.recall_limit = max(1, int(recall_limit))

    # ---------------------------------------------------------------- 节点 1
    async def load_context(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：加载匹配依据。
        输入：岗位ID、画像版本、可选权重。
        输出：岗位标题、确认画像、版本和评分视图。
        步骤：检查岗位 → 读取确认画像 → 应用权重；关闭岗位拒绝。
        """
        position_id = int(state["position_id"])
        # *kwq* 修改（REV-001）：优先用调用方预取的岗位对象（反向匹配逐岗位循环时避免 N 次重复查库），
        # 未提供缓存时行为与原来完全一致。
        cached = (state.get("position_cache") or {}).get(position_id)
        position = cached if cached is not None else await self.position_repo.get_by_id(self.db, position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={position_id}）")
        if position.status == "closed":
            raise BusinessRuleError("岗位已关闭，不能执行匹配")

        profile, version = await self.profile_service.require_confirmed(
            position_id, state.get("requirement_version")
        )
        errors = list(state.get("errors") or [])
        if position.status == "paused":
            errors.append("岗位处于暂停状态，本次结果仅供预研参考")

        position_view = _position_view(position, profile)

        # *kwq* 新增：单次请求权重覆盖（试算不改岗位配置）。
        # 归一化后直接替换 position_view["weights"]，下游 filter_score / 持久化自动生效。
        override = _normalize_weights(state.get("weights") or {})
        if override:
            position_view["weights"] = override

        return {
            "position_title": position.title,
            "profile": profile,
            "profile_version": version,
            "position_view": position_view,
            "errors": errors,
        }

    # ---------------------------------------------------------------- 节点 2
    async def recall(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：选出本次参与评估的人才。
        输入：人才ID范围或岗位画像。
        输出：candidates、recall_mode和提示。
        步骤：指定ID带状态回表；否则尝试向量通道，再降级SQL。
        """
        talent_ids = state.get("talent_ids")
        if talent_ids:
            # 指定候选人：直接回表（语义是「点名评估」，无需向量召回）
            # 步骤1：点名评估也检查人才状态，禁止推荐离职人员。
            candidates = await self.talent_repo.list_candidates(
                self.db, talent_ids=talent_ids, statuses=("active", "reserve")
            )
            return {
                "candidates": list(candidates),
                "recall_mode": "sql_fallback",
                "errors": list(state.get("errors") or []),
            }

        # 全库召回：先尝试向量通道（集合存在且非空才启用，否则如实降级）
        vector_hits = await self._try_vector_recall(state)
        if vector_hits:
            order = {int(tid): index for index, (tid, _score) in enumerate(vector_hits)}
            candidates = [
                talent
                for talent in await self.talent_repo.get_by_ids(
                    self.db, [tid for tid, _score in vector_hits]
                )
                if getattr(talent, "status", None) in ("active", "reserve")
                and not getattr(talent, "is_deleted", False)
            ]
            if candidates:
                candidates.sort(key=lambda t: order.get(int(t.id), 1 << 30))
                return {
                    "candidates": candidates,
                    "recall_mode": "vector",
                    "errors": list(state.get("errors") or []),
                }

        candidates = await self.talent_repo.list_candidates(
            self.db,
            talent_ids=None,
            statuses=("active", "reserve"),
            limit=self.recall_limit,
        )
        errors = list(state.get("errors") or [])
        if len(candidates) >= self.recall_limit:
            errors.append(
                f"候选池达到召回上限 {self.recall_limit} 人，可能存在未参与计算的人员"
            )

        # Milvus 未部署 / 未接入向量化时只能走 SQL 召回；这里不做「伪向量」标记
        return {
            "candidates": list(candidates),
            "recall_mode": "sql_fallback",
            "errors": errors,
        }

    # *kwq* 新增：向量召回通道（Milvus + Embedding 全链路就绪才启用）。
    # 设计原则：任一环节不可用（未启用 / pymilvus 缺失 / 集合不存在 / 集合为空 /
    #          向量化失败）都返回 None，上层走 SQL 兜底；失败原因写进 errors，
    #          绝不伪造 vector 标记。
    async def _try_vector_recall(self, state: MatchingState) -> list[tuple[int, float]] | None:
        """向量召回：返回 [(talent_id, score)]（按相似度倒序），不可用返回 None。"""
        from app.config.settings import settings

        if not settings.MILVUS_ENABLED:
            return None
        try:
            from app.adapters.vector_store.talent_vector_store import TalentVectorStore
            from app.ai.rag.embedding_service import EmbeddingService

            adapter = TalentVectorStore()
            collection = "talent_vectors"
            # 合并 lz 分支后，向量适配器改为「按知识分类路由、复用 knowledge_chunks」的实现，
            # 不再提供 talent_vectors 的集合探测能力（has_collection / stats）与 output_fields 检索。
            # 这里做能力探测：不具备即视为向量通道不可用，直接回退 SQL 召回。
            # 匹配结果口径不变，也不会把「接口不存在」误记成召回失败而污染 errors。
            if not (hasattr(adapter, "has_collection") and hasattr(adapter, "stats")):
                return None
            if not await adapter.has_collection(collection):
                return None
            stats = await adapter.stats(collection)
            if int((stats or {}).get("row_count") or 0) <= 0:
                return None

            view = state.get("position_view") or {}
            profile = state.get("profile")
            skills = ",".join(view.get("skill_tags") or [])
            query_text = f"{view.get('title') or ''} {skills}".strip() or "岗位画像"
            if profile is not None and hasattr(profile, "required_skills"):
                skills = ",".join(list(getattr(profile, "required_skills") or []) or [skills])
                query_text = f"{view.get('title') or ''} {skills}".strip()

            vector = await EmbeddingService().embed_query(query_text)
            hits = await adapter.search(
                collection,
                vector,
                top_k=self.recall_limit,
                output_fields=["talent_id"],
            )
            result: list[tuple[int, float]] = []
            for hit in hits:
                tid = hit.get("talent_id") or hit.get("id")
                if tid is None:
                    continue
                score = float(hit.get("score") or 0)
                if score < MIN_VECTOR_SCORE:
                    continue
                result.append((int(tid), score))
            return result or None
        except Exception as exc:  # noqa: BLE001 - 向量通道任何失败都降级并留痕
            errors = list(state.get("errors") or [])
            errors.append(f"向量召回不可用，回退 SQL：{type(exc).__name__}: {exc}")
            state["errors"] = errors
            logger.warning("recall.vector_fallback err=%s", exc)
            return None

    # ---------------------------------------------------------------- 节点 3
    async def filter_score(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：条件判定、评分并筛选输出。
        输入：岗位评分视图、候选人、策略。
        输出：scored、excluded和统计。
        步骤：执行策略 → 统计四态 → 应用最低分及数量上限。
        """
        candidates = list(state.get("candidates") or [])
        if not candidates:
            return {
                "scored": [],
                "excluded": [],
                "stats": {
                    "candidates": 0,
                    "excluded_by_hard": 0,
                    "unknown": 0,
                    "needs_review": 0,
                    "output": 0,
                },
            }

        strategy_name = state.get("strategy") or "hybrid"
        strategy = MatchStrategyFactory.create(
            strategy_name,
            weights=(state.get("position_view") or {}).get("weights"),
            rule_config=MATCH_RULE_CONFIG,
        )
        scored = await strategy.match(state.get("position_view"), candidates)
        excluded = list(getattr(strategy, "excluded", []) or [])

        # 输出侧过滤：min_score 只影响展示，不改变分数与排序
        min_score = float(state.get("min_score") or 0)
        top_k = max(1, int(state.get("top_k") or 20))
        # *kwq* 修改（MATCH-008 实测修复）：top_k 必须在「输出集合」上生效。
        # 修改原因：原实现只在 build_evidence 里截断（省解释调用），assemble 仍遍历全部
        #          scored，导致 top_k=1 时照样返回全量候选人（实测 33 条）。
        #          这不仅是展示问题，还会让 stats.output 与真实返回条数不一致。
        filtered = [item for item in scored if float(item.get("score") or 0) >= min_score][:top_k]

        # *kwq* 修改（MATCH-019）：四态计数改为「以逐项 hard_results 为准」。
        # 修改原因：原实现只读策略给出的 has_unknown / needs_review 布尔标记，
        #          而 soft 策略不做硬判定、不产出这两个标记，导致 stats 里
        #          unknown=0 / needs_review=0 —— 数字看着像「没人待补全」，
        #          实际是「根本没算」，属于误导性统计。
        def _has_state(entry: dict[str, Any], target: str) -> bool:
            for raw in entry.get("hard_results") or []:
                status = raw.get("status")
                if isinstance(status, HardStatus):
                    status = status.value
                if str(status) == target:
                    return True
            return False

        stats = {
            "candidates": len(candidates),
            "excluded_by_hard": len(excluded),
            "unknown": sum(1 for item in scored if _has_state(item, "UNKNOWN")),
            "needs_review": sum(1 for item in scored if _has_state(item, "NEEDS_REVIEW")),
            "output": len(filtered),
        }
        return {"scored": filtered, "excluded": excluded, "stats": stats}

    # ---------------------------------------------------------------- 节点 4
    async def build_evidence(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：为输出候选人构建事实依据。
        输入：排序结果、人才档案和岗位画像。
        输出：按人才ID索引的证据包。
        步骤：关联人才 → 还原四态 → 调用纯函数构建器。
        """
        scored = list(state.get("scored") or [])
        if not scored:
            return {"packs": {}}

        top_k = max(1, int(state.get("top_k") or 20))
        by_id = {}
        for talent in state.get("candidates") or []:
            tid = getattr(talent, "id", None) or (talent.get("id") if isinstance(talent, dict) else None)
            if tid is not None:
                by_id[int(tid)] = talent

        position_id = int(state["position_id"])
        packs: dict[int, MatchEvidencePack] = {}
        for item in scored[:top_k]:
            talent_id = int(item["talent_id"])
            talent = by_id.get(talent_id)
            if talent is None:
                continue
            hard_results = [
                HardConditionResult.model_validate(raw) for raw in (item.get("hard_results") or [])
            ]
            packs[talent_id] = build_match_evidence(
                state.get("position_view"),
                talent,
                hard_results=hard_results,
                dimension_scores=item.get("dimensions") or {},
                position_id=position_id,
                talent_id=talent_id,
                requirement_version=int(state.get("profile_version") or 1),
            )
        return {"packs": packs}

    # ---------------------------------------------------------------- 节点 5
    async def explain(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：并发生成并校验解释。
        输入：证据包及外部调用开关。
        输出：按人才ID索引的解释结果及错误。
        步骤：限制并发 → 调解释服务 → 汇总成功结果与异常。
        """
        packs: dict[int, MatchEvidencePack] = dict(state.get("packs") or {})
        if not packs:
            return {"explanations": {}}

        use_external = bool(state.get("with_explanation", True))
        semaphore = asyncio.Semaphore(EXPLAIN_CONCURRENCY)
        errors = list(state.get("errors") or [])

        async def _one(talent_id: int, pack: MatchEvidencePack) -> tuple[int, ExplanationOutcome]:
            async with semaphore:
                outcome = await self.explanation_service.generate(pack, use_external=use_external)
                return talent_id, outcome

        results = await asyncio.gather(
            *[_one(tid, pack) for tid, pack in packs.items()], return_exceptions=True
        )

        explanations: dict[int, ExplanationOutcome] = {}
        for item in results:
            if isinstance(item, BaseException):
                # 解释失败不能拖垮整批：记录原因，该候选人稍后用规则兜底
                errors.append(f"解释生成异常：{type(item).__name__}: {item}")
                continue
            talent_id, outcome = item
            explanations[talent_id] = outcome
            errors.extend(outcome.errors)

        return {"explanations": explanations, "errors": errors}

    # ---------------------------------------------------------------- 节点 6
    async def assemble(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：组装对外候选人报告。
        输入：评分、四态、证据、解释和淘汰结果。
        输出：MatchItem列表、统计和ExcludedItem列表。
        步骤：关联数据 → 分配排名 → 填入完整度、来源和解释。
        """
        scored = list(state.get("scored") or [])
        packs = state.get("packs") or {}
        explanations = state.get("explanations") or {}
        by_id = {}
        for talent in state.get("candidates") or []:
            tid = getattr(talent, "id", None) or (talent.get("id") if isinstance(talent, dict) else None)
            if tid is not None:
                by_id[int(tid)] = talent

        items: list[MatchItem] = []
        stats = dict(state.get("stats") or {})
        for index, entry in enumerate(scored, start=1):
            talent_id = int(entry["talent_id"])
            talent = by_id.get(talent_id)
            outcome = explanations.get(talent_id)
            pack = packs.get(talent_id)
            explanation = outcome.explanation if outcome else None

            hard_results = [
                HardConditionResult.model_validate(raw) for raw in (entry.get("hard_results") or [])
            ]
            # *kwq* 新增（GAP-001）：结构化能力差距矩阵。
            # 复用四态判定与画像/档案原值，纯函数计算；soft 策略（无 hard_results）返回空矩阵。
            gap_rows: list[GapRow] = []
            if hard_results and talent is not None and state.get("profile"):
                gap_rows = build_gap_matrix(
                    state["profile"],
                    talent,
                    hard_results,
                    (state.get("position_view") or {}).get("weights"),
                ).rows
            items.append(
                MatchItem(
                    talent_id=talent_id,
                    talent_name=(getattr(talent, "name", None) if talent is not None else None),
                    # *kwq* 新增（MATCH-017/018）：名次与整体判定由后端统一给出。
                    rank=index,
                    decision=(aggregate_status(hard_results) if hard_results else HardStatus.PASS),
                    score=float(entry.get("score") or 0),
                    dimension_scores={
                        str(k): float(v) for k, v in (entry.get("dimensions") or {}).items()
                    },
                    defaulted_dimensions=(
                        defaulted_dimensions(talent, state["profile"])
                        if (talent is not None and state.get("profile"))
                        else []
                    ),
                    data_completeness=(
                        pack.data_completeness
                        if pack is not None
                        else compute_data_completeness(talent or {})
                    ),
                    matched_skills=[str(s) for s in (entry.get("highlights") or [])],
                    highlights=[claim.text for claim in (explanation.highlights if explanation else [])],
                    gaps=[claim.text for claim in (explanation.gaps if explanation else [])],
                    hard_results=hard_results,
                    suggestion=(explanation.summary[:512] if explanation else None),
                    explanation_source=(explanation.source if explanation else None),
                    gap_matrix=gap_rows,
                )
            )

        excluded = [
            ExcludedItem(
                talent_id=int(raw.get("talent_id") or 0),
                talent_name=raw.get("name"),
                status=HardStatus(raw.get("status") or "FAIL"),
                reasons=[str(item) for item in (raw.get("reasons") or [])],
            )
            for raw in (state.get("excluded") or [])
            if raw.get("talent_id")
        ]

        stats["output"] = len(items)
        return {"items": items, "stats": stats, "excluded": excluded}

    # ---------------------------------------------------------------- 双向匹配
    # *kwq* 新增：人才找岗位（需求 §06 双向匹配统一设计）。
    # 实现口径：复用正向链路全部节点（load_context -> recall -> filter_score ->
    #          build_evidence -> explain -> assemble），每个岗位做一次「单岗位 × 单人才」
    #          评估，保证双向打分口径严格一致；不做任何「反向专用」的另算逻辑。
    # 淘汰透明：画像未确认 / 岗位关闭的岗位进 skipped，硬条件 FAIL 的岗位带原因进
    #          excluded —— 不静默丢弃，与正向「为什么没出现」的设计一致。
    async def run_reverse(self, state: MatchingState) -> dict[str, Any]:
        """
        功能：给定人才反查适配岗位。
        输入：人才ID、岗位范围、策略和输出条件。
        输出：岗位排名、淘汰及跳过原因。
        步骤：读可用人才 → 逐岗位复用正向节点 → 排序；本方法不落库。
        """
        talent_id = int(state["talent_id"])

        talents = await self.talent_repo.list_candidates(self.db, talent_ids=[talent_id])
        if not talents:
            raise NotFoundError(f"人才不存在或不可参与匹配（id={talent_id}）")
        talent = talents[0]

        position_ids = state.get("position_ids")
        if position_ids:
            requested = [int(pid) for pid in position_ids if int(pid) > 0]
            positions = await self.position_repo.get_by_ids(self.db, requested)
            # 指定 ID 的岗位不做状态过滤（closed 会由 load_context 抛错进 skipped）
            positions = [p for p in positions if int(p.id) in set(requested)]
        else:
            positions = list(
                await self.position_repo.list(
                    self.db,
                    filters={"status": "open"},
                    include_deleted=False,
                    limit=self.recall_limit,
                )
            )

        reverse_items: list[tuple[Position, MatchItem, int]] = []
        reverse_excluded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        # *kwq* 修改（REV-001）：逐岗位收集实际权重，不再用「最后一个岗位」覆盖全局。
        # 原因：多岗位权重配置不同时，响应里的单个 weights_used 会被最后一人覆盖，
        #      调用方据此复算分数会得到与 items 不一致的结果（口径漂移）。
        weights_seen: list[dict[str, float]] = []
        recall_mode = "sql_fallback"
        errors: list[str] = list(state.get("errors") or [])
        # *kwq* 新增（REV-001）：岗位对象预取，供 load_context 复用，省掉 N 次 get_by_id。
        position_cache = {int(p.id): p for p in positions}

        # 说明：这里保持串行执行。单个 AsyncSession 不支持并发使用，
        #      用 gather 并发跑同一会话的 DB 查询会触发驱动层冲突；
        #      优化点改为「减少每轮查询次数」而非「并发查询」。
        for position in positions:
            sub_state: dict[str, Any] = {
                "position_id": int(position.id),
                "talent_ids": [talent_id],
                "top_k": 1,
                "min_score": 0.0,
                "strategy": str(state.get("strategy") or "hybrid"),
                "with_explanation": bool(state.get("with_explanation", False)),
                "requirement_version": state.get("requirement_version"),
                "weights": state.get("weights"),
                "batch_id": str(state.get("batch_id") or ""),
                "matched_by": state.get("matched_by"),
                "position_cache": position_cache,
                "errors": [],
            }
            try:
                for node_name in (
                    "load_context",
                    "recall",
                    "filter_score",
                    "build_evidence",
                    "explain",
                    "assemble",
                ):
                    delta = await getattr(self, node_name)(sub_state)  # type: ignore[arg-type]
                    if delta:
                        sub_state.update(delta)
            except (NotFoundError, BusinessRuleError) as exc:
                skipped.append(
                    {
                        "position_id": int(position.id),
                        "position_title": position.title,
                        "reason": exc.message,
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001
                # *kwq* 修改：单岗位的非业务异常（如画像 dimensions 存脏 JSON 触发
                # ValidationError、向量通道意外错误）不再中断整批反查，
                # 与「一个岗位失败不拖垮全量」的设计意图一致；原因截断到 256 与 Schema 对齐。
                skipped.append(
                    {
                        "position_id": int(position.id),
                        "position_title": position.title,
                        "reason": f"岗位处理异常：{exc}"[:256],
                    }
                )
                continue

            errors.extend(list(sub_state.get("errors") or []))
            # *kwq* 修改（REV-001）：收集每个岗位实际生效的权重（去重后判断是否统一）。
            effective_weights = (sub_state.get("position_view") or {}).get("weights")
            if isinstance(effective_weights, dict) and effective_weights not in weights_seen:
                weights_seen.append(dict(effective_weights))
            recall_mode = str(sub_state.get("recall_mode") or recall_mode)

            items = list(sub_state.get("items") or [])
            if items:
                reverse_items.append((position, items[0], int(sub_state.get("profile_version") or 1)))
            else:
                # 人才被硬条件淘汰（如学历不足）：给出逐条原因
                excluded = list(sub_state.get("excluded") or [])
                reverse_excluded.append(
                    {
                        "position_id": int(position.id),
                        "position_title": position.title,
                        "reason": "；".join(
                            str(r) for r in (
                                # assemble产出ExcludedItem模型；兼容字典状态，保留原始淘汰原因。
                                (excluded[0].get("reasons") if isinstance(excluded[0], dict) else excluded[0].reasons)
                                if excluded else []
                            ) or ["未通过硬性条件"]
                        )[:256],
                    }
                )

        # 统一按分数倒序 + min_score / top_k 在「岗位集合」上生效（与正向同规则）
        min_score = float(state.get("min_score") or 0)
        top_k = max(1, int(state.get("top_k") or 10))
        reverse_items.sort(key=lambda pair: float(pair[1].score), reverse=True)
        picked = [pair for pair in reverse_items if float(pair[1].score) >= min_score][:top_k]

        items_out = [
            MatchReverseItem(
                rank=index,
                position_id=int(position.id),
                position_title=position.title,
                requirement_version=version,
                decision=pair_item.decision,
                score=float(pair_item.score),
                dimension_scores=dict(pair_item.dimension_scores),
                data_completeness=float(pair_item.data_completeness or 0),
                matched_skills=list(pair_item.matched_skills),
                highlights=list(pair_item.highlights),
                gaps=list(pair_item.gaps),
                hard_results=list(pair_item.hard_results),
                suggestion=pair_item.suggestion,
                explanation_source=pair_item.explanation_source,
            )
            for index, (position, pair_item, version) in enumerate(picked, start=1)
        ]
        # 被淘汰岗位也要带上画像版本与排名外的适配岗位——版本号从评估结果取不到时回退 1
        excluded_out = [MatchReverseSkipped.model_validate(raw) for raw in reverse_excluded]
        skipped_out = [MatchReverseSkipped.model_validate(raw) for raw in skipped]

        # *kwq* 新增（REV-001）：权重语义收敛。
        # 只有一个权重口径时才回传 weights_used；多口径时置空并给出说明，
        # 避免调用方拿着「最后一个岗位的权重」去复算全部岗位分数。
        weights_used: dict[str, float] | None = weights_seen[0] if len(weights_seen) == 1 else None
        weights_note: str | None = None
        if len(weights_seen) > 1:
            weights_note = (
                f"本次评估的 {len(weights_seen)} 个岗位权重配置不同，weights_used 不适用；"
                "各岗位分数按各自画像配置的权重计算，如需统一口径请通过 weights 参数覆盖后重试"
            )

        return {
            "talent": talent,
            "positions_evaluated": len(reverse_items) + len(reverse_excluded),
            "skipped": skipped_out,
            "excluded": excluded_out,
            "items": items_out,
            "recall_mode": recall_mode,
            "weights_used": weights_used,
            "weights_note": weights_note,
            "errors": errors,
        }


__all__ = [
    "DEFAULT_RECALL_LIMIT",
    "EXPLAIN_CONCURRENCY",
    "MatchingPipeline",
    "MatchingState",
    "defaulted_dimensions",
]
