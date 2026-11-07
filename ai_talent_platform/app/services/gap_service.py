"""
标签：[Dify组] [P1]
功能：结构化能力差距矩阵（GAP-001）。
输入：岗位画像、人才档案、四态判定结果、维度权重。
输出：GapMatrix（逐条目「要求 vs 实际」对照 + 严重度 + 证据引用 + 汇总）。
流程：解析四态行 → 拆技能行 → 组装维度行 → 归一化权重 → 排序汇总。
边界：纯函数无 IO；status 复用四态口径，不新造评分；缺失数据标 UNKNOWN，禁止推测；
     hard_results 为空（soft 策略不做硬判定）时返回空矩阵，不平行实现第二套判定逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.skills.matching_skill import normalize_skills
from app.schemas.matching import (
    GapRow,
    GapMatrix,
    HardConditionResult,
    HardStatus,
    MatchCompareCommonGap,
    MatchCompareGapStat,
)
from app.schemas.position import JobProfileParsed

logger = logging.getLogger(__name__)

# 严重度展示优先级（越小越靠前）
_SEVERITY_ORDER = {"blocking": 0, "missing_data": 1, "major": 2, "minor": 3}

# HardStatus -> GapStatus（四态 → 矩阵口径，NEEDS_REVIEW 归入「部分符合/需复核」）
_STATUS_MAP = {
    HardStatus.PASS: "PASS",
    HardStatus.FAIL: "FAIL",
    HardStatus.UNKNOWN: "UNKNOWN",
    HardStatus.NEEDS_REVIEW: "PARTIAL",
}

# 四态结果 -> 严重度（聚合行的默认映射）
_AGG_SEVERITY = {
    HardStatus.PASS: "minor",
    HardStatus.FAIL: "blocking",
    HardStatus.UNKNOWN: "missing_data",
    HardStatus.NEEDS_REVIEW: "major",
}

_DIM_SOURCE_FIELD = {
    "min_education": ("education", "talents.education"),
    "min_work_years": ("experience", "talents.work_years"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    """ORM / dict 统一转 dict（与 evidence_builder 同口径，不引入新依赖）。"""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {}


def _evidence_ids_of(result: HardConditionResult | None) -> list[str]:
    if result is not None and result.evidence_id:
        return [result.evidence_id]
    return []


def _row(
    *,
    dimension: str,
    item: str,
    requirement: Any,
    actual: Any,
    status: str,
    severity: str,
    weight: float,
    evidence_ids: list[str],
    source_field: str,
    reason: str,
    hit: bool | None = None,
) -> GapRow:
    return GapRow(
        dimension=dimension,
        item=item,
        requirement=requirement,
        actual=actual,
        status=status,
        hit=hit,
        weight=round(weight, 6),
        severity=severity,
        evidence_ids=evidence_ids,
        source_field=source_field,
        reason=reason[:200],
    )


def _skill_rows(
    aggregate: HardConditionResult | None,
    profile_required: list[str],
    weights: dict[str, float],
) -> list[GapRow]:
    """必备技能逐技能拆行。

    关键口径：状态完全沿用聚合行的四态结论（判定器已按 all/any/partial_review
    模式裁决），这里只做「集合差拆分」，绝不平行实现第二套判定。
    """
    required = [str(s).strip() for s in (aggregate.required if aggregate is not None else profile_required) if str(s).strip()]
    if not required:
        return []

    # 判定器比较时统一小写，required/actual 均为小写形式；
    # 展示用名称优先还原画像里的原始大小写（profile_required 为准）。
    original_by_lower = {str(s).strip().lower(): str(s).strip() for s in profile_required}

    # actual: None 表示档案无技能字段；[] 表示有字段但为空；list 表示命中集合
    raw_actual = aggregate.actual if aggregate is not None else None
    hit_set = {str(s).strip().lower() for s in (raw_actual or []) if str(s).strip()}
    agg_status = aggregate.status if aggregate is not None else HardStatus.UNKNOWN
    evidence_ids = _evidence_ids_of(aggregate)
    weight_each = weights.get("skill", 0.0) / len(required)

    rows: list[GapRow] = []
    for raw_name in required:
        key = raw_name.strip().lower()
        skill = original_by_lower.get(key, raw_name)
        if raw_actual is None:
            # 档案缺少技能字段：待核实，绝不推测「不具备」
            rows.append(_row(
                dimension="skill", item=skill, requirement=skill, actual=None,
                status="UNKNOWN", severity="missing_data", weight=weight_each,
                evidence_ids=evidence_ids, source_field="talents.skill_tags",
                reason="人才档案缺少技能标签，无法判断是否具备该技能", hit=None,
            ))
            continue
        if key in hit_set:
            rows.append(_row(
                dimension="skill", item=skill, requirement=skill, actual=skill,
                status="PASS", severity="minor", weight=weight_each,
                evidence_ids=evidence_ids, source_field="talents.skill_tags",
                reason=f"命中必备技能「{skill}」", hit=True,
            ))
            continue
        # 未命中：状态由聚合行决定（FAIL 且必备技能零命中=不符合；FAIL 且部分命中=部分符合；
        #         NEEDS_REVIEW=部分符合待复核）。注意：聚合行 actual 是人才全量技能集合，
        #         「零命中」必须按必备技能命中数计算，而不是人才是否有任何技能。
        if agg_status == HardStatus.FAIL:
            hits_in_required = sum(1 for r in required if r.strip().lower() in hit_set)
            missing_all = hits_in_required == 0
            rows.append(_row(
                dimension="skill", item=skill, requirement=skill, actual=None,
                status="FAIL", severity="blocking" if missing_all else "major",
                weight=weight_each, evidence_ids=evidence_ids,
                source_field="talents.skill_tags",
                reason=f"档案技能未命中必备技能「{skill}」", hit=False,
            ))
        elif agg_status == HardStatus.NEEDS_REVIEW:
            rows.append(_row(
                dimension="skill", item=skill, requirement=skill, actual=None,
                status="PARTIAL", severity="major", weight=weight_each,
                evidence_ids=evidence_ids, source_field="talents.skill_tags",
                reason=f"部分命中需人工复核，缺少「{skill}」", hit=False,
            ))
        else:  # any 口径下聚合 PASS 但存在未命中：不淘汰，标部分符合
            rows.append(_row(
                dimension="skill", item=skill, requirement=skill, actual=None,
                status="PARTIAL", severity="minor", weight=weight_each,
                evidence_ids=evidence_ids, source_field="talents.skill_tags",
                reason=f"未命中「{skill}」，按当前口径不构成淘汰", hit=False,
            ))
    return rows


def _preferred_rows(profile: JobProfileParsed, talent_data: dict[str, Any], weights: dict[str, float]) -> list[GapRow]:
    """优先技能行：命中=PASS，未命中=部分符合（不参与淘汰，绝不判 FAIL）。

    权重恒为 0：skill 维度权重只由必备技能行分摊，优先技能仅作信息展示，
    避免同一维度权重被重复分摊导致全表权重和突破 1.0。
    """
    preferred = [str(s).strip() for s in profile.preferred_skills if str(s).strip()]
    if not preferred:
        return []
    raw_tags = talent_data.get("skill_tags")
    talent_set = normalize_skills(raw_tags)

    rows: list[GapRow] = []
    for skill in preferred:
        if raw_tags is None:
            rows.append(_row(
                dimension="skill", item=f"优先技能：{skill}", requirement=skill, actual=None,
                status="UNKNOWN", severity="missing_data", weight=0.0,
                evidence_ids=[], source_field="talents.skill_tags",
                reason="档案缺少技能标签，无法判断优先技能", hit=None,
            ))
            continue
        hit = skill.lower() in talent_set
        rows.append(_row(
            dimension="skill", item=f"优先技能：{skill}", requirement=skill,
            actual=skill if hit else None,
            status="PASS" if hit else "PARTIAL", severity="minor",
            weight=0.0, evidence_ids=[], source_field="talents.skill_tags",
            reason="命中优先技能（优先条件，不参与淘汰）" if hit else "未命中优先技能（不参与淘汰，仅供参考）",
            hit=hit,
        ))
    return rows


def _dimension_row(
    item_name: str,
    aggregate: HardConditionResult | None,
    talent_data: dict[str, Any],
    weights: dict[str, float],
) -> GapRow | None:
    """学历 / 年限单行：直接沿用聚合四态，缺失数据标待核实。"""
    mapping = _DIM_SOURCE_FIELD.get(item_name)
    if mapping is None:
        return None
    dimension, source_field = mapping
    if aggregate is None:
        return None  # 画像未设定该要求：不出行，权重由归一化回收

    status = _STATUS_MAP.get(aggregate.status, "UNKNOWN")
    severity = _AGG_SEVERITY[aggregate.status]
    dim_weight = weights.get(dimension, 0.0)
    reason = aggregate.reason or ""
    if aggregate.status == HardStatus.UNKNOWN:
        reason = f"档案缺少 {source_field.split('.')[-1]}，无法判定：{reason}"[:200]
    return _row(
        dimension=dimension, item=item_name,
        requirement=aggregate.required, actual=aggregate.actual,
        status=status, severity=severity, weight=dim_weight,
        evidence_ids=_evidence_ids_of(aggregate), source_field=source_field,
        reason=reason,
    )


def _quality_row(talent_data: dict[str, Any], weights: dict[str, float]) -> GapRow:
    """素质行：岗位画像暂无素质要求字段，仅展示档案现状（缺失标待核实，不评分）。"""
    actual = talent_data.get("potential_level")
    if actual is None:
        return _row(
            dimension="quality", item="potential_level", requirement=None, actual=None,
            status="UNKNOWN", severity="missing_data", weight=weights.get("quality", 0.0),
            evidence_ids=[], source_field="talents.potential_level",
            reason="档案缺少潜力等级，素质维度使用默认分（不计为能力结论）",
        )
    return _row(
        dimension="quality", item="potential_level", requirement=None, actual=actual,
        status="PASS", severity="minor", weight=weights.get("quality", 0.0),
        evidence_ids=[], source_field="talents.potential_level",
        reason="档案已提供潜力等级（岗位未设定素质要求，仅作参考）",
    )


def build_gap_matrix(
    profile: JobProfileParsed,
    talent: Any,
    hard_results: list[HardConditionResult],
    weights: dict[str, float] | None,
) -> GapMatrix:
    """构建结构化能力差距矩阵（纯函数，无 IO）。

    参数：
        profile:      已确认岗位画像（要求的唯一来源）
        talent:       人才 ORM / dict（实际值的唯一来源）
        hard_results: 四态判定明细（状态的唯一来源；为空返回空矩阵，不自行判定）
        weights:      归一化四维权重（None 时全 0，仅展示对照不展示权重占比）

    保证：
        - 不修改入参；不产生任何新 evidence_id，只引用既有 ID；
        - 权重归一化到 1.0（画像未设定的维度权重回收后均摊）；
        - 缺失数据一律 UNKNOWN / missing_data，禁止推测。
    """
    talent_data = _as_dict(talent)
    safe_weights = {k: float(v) for k, v in (weights or {}).items()}

    by_item: dict[str, HardConditionResult] = {}
    extra_results: list[HardConditionResult] = []
    for result in hard_results or []:
        if result.item in ("min_education", "min_work_years") and result.item not in by_item:
            by_item[result.item] = result
        elif result.item == "required_skills" and "required_skills" not in by_item:
            by_item["required_skills"] = result
        else:
            extra_results.append(result)

    if not hard_results:
        # soft 策略不做硬判定：无可判定依据，返回空矩阵（诚实降级，不平行造判定）
        return GapMatrix(rows=[], summary={"total": 0})

    rows: list[GapRow] = []
    rows.extend(_skill_rows(by_item.get("required_skills"), list(profile.required_skills), safe_weights))
    rows.extend(_preferred_rows(profile, talent_data, safe_weights))

    edu_row = _dimension_row("min_education", by_item.get("min_education"), talent_data, safe_weights)
    if edu_row is not None:
        rows.append(edu_row)
    years_row = _dimension_row("min_work_years", by_item.get("min_work_years"), talent_data, safe_weights)
    if years_row is not None:
        rows.append(years_row)
    rows.append(_quality_row(talent_data, safe_weights))

    # 非结构化条件行（required_condition 等）：信息展示，权重 0
    for result in extra_results:
        rows.append(_row(
            dimension="other", item=result.item[:64],
            requirement=result.required, actual=result.actual,
            status=_STATUS_MAP.get(result.status, "UNKNOWN"),
            severity=_AGG_SEVERITY.get(result.status, "missing_data"),
            weight=0.0, evidence_ids=_evidence_ids_of(result),
            source_field="profile.required_conditions",
            reason=result.reason or "",
        ))

    # 权重归一化：有效行（weight>0）合计拉平到 1.0；画像未设定的维度权重自然回收
    positive_total = sum(r.weight for r in rows if r.weight > 0)
    if positive_total > 0:
        for row in rows:
            if row.weight > 0:
                row.weight = round(row.weight / positive_total, 6)

    rows.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 9), -r.weight, r.item))

    summary = {
        "total": len(rows),
        "pass": sum(1 for r in rows if r.status == "PASS"),
        "partial": sum(1 for r in rows if r.status == "PARTIAL"),
        "fail": sum(1 for r in rows if r.status == "FAIL"),
        "unknown": sum(1 for r in rows if r.status == "UNKNOWN"),
        "blocking": sum(1 for r in rows if r.severity == "blocking"),
        "missing_data": sum(1 for r in rows if r.severity == "missing_data"),
    }
    logger.info(
        "gap_matrix.built rows=%s fail=%s unknown=%s",
        summary["total"], summary["fail"], summary["unknown"],
    )
    return GapMatrix(rows=rows, summary=summary)


__all__ = ["build_gap_matrix", "compare_gap_overview"]


# ============================================================================
# *kwq* 新增（CMP-002）：多人缺口横向概览。
# 需求依据：HR 选人时真正要回答的是「这几个人共同缺什么」——如果一项要求
#   全员都不满足，问题多半在岗位要求或市场供给，而不是候选人不行。
# 红线：
#   1) 只对已有 gap_matrix 做计数与分组，不重算评分、不重新判定；
#   2) 只认 FAIL / PARTIAL 且 severity 为 blocking / major 的条目为「缺口」，
#      数据缺失（missing_data）单独计数，不混入缺口，避免把「没数据」说成「不行」。
# ============================================================================
_GAP_STATUSES: frozenset[str] = frozenset({"FAIL", "PARTIAL"})
_GAP_SEVERITIES: frozenset[str] = frozenset({"blocking", "major"})

#: 共同缺口最多返回条数（前端一屏放不下更多，返回全量反而稀释重点）
_COMMON_GAP_LIMIT = 10


def compare_gap_overview(
    rows_by_talent: dict[int, list[GapRow]],
) -> tuple[list[MatchCompareGapStat], list[MatchCompareCommonGap]]:
    """把多人的差距矩阵聚合成「每人分布 + 共同缺口」。

    输入：{talent_id: [GapRow, ...]}（顺序即对比行的顺序）
    输出：(gap_stats, common_gaps)
        gap_stats    与输入顺序一致，便于调用方按行对齐；
        common_gaps  只保留 2 人及以上都缺的条目，按涉及人数降序、同数按条目名稳定排序。
    """
    stats: list[MatchCompareGapStat] = []
    # (dimension, item) -> 不满足该条目的人才 ID 列表
    missing_map: dict[tuple[str, str], list[int]] = {}

    for talent_id, rows in rows_by_talent.items():
        counters = {
            "total": len(rows), "pass_count": 0, "partial": 0, "fail": 0, "unknown": 0,
            "blocking": 0, "major": 0, "minor": 0, "missing_data": 0,
        }
        for row in rows:
            status = str(row.status)
            severity = str(row.severity)
            if status == "PASS":
                counters["pass_count"] += 1
            elif status == "PARTIAL":
                counters["partial"] += 1
            elif status == "FAIL":
                counters["fail"] += 1
            else:
                counters["unknown"] += 1

            # *kwq* 修复（QA-001）：严重度只对「未满足项」计数。
            # 原实现无差别累加，而矩阵里 PASS 行的 severity 固定为 minor，
            # 导致「符合项」被算成「次要缺口」——HR 会看到「5 项符合 3 项轻微不足」
            # 却只有 1 项真缺口，数字自相矛盾。
            # 口径：blocking/major/minor 只统计 FAIL/PARTIAL；
            #      missing_data 只统计 UNKNOWN（数据缺失是「不知道」，不是缺口）。
            if status in _GAP_STATUSES:
                if severity in ("blocking", "major", "minor"):
                    counters[severity] += 1
            elif status == "UNKNOWN" and severity == "missing_data":
                counters["missing_data"] += 1

            if status in _GAP_STATUSES and severity in _GAP_SEVERITIES:
                missing_map.setdefault((str(row.dimension), str(row.item)), []).append(
                    int(talent_id)
                )

        stats.append(MatchCompareGapStat(talent_id=int(talent_id), **counters))

    total_people = len(rows_by_talent)
    common: list[MatchCompareCommonGap] = []
    for (dimension, item), talent_ids in missing_map.items():
        if len(talent_ids) < 2:
            continue
        common.append(
            MatchCompareCommonGap(
                dimension=dimension,
                item=item,
                missing_for=sorted(set(talent_ids)),
                all_missing=len(set(talent_ids)) >= total_people > 0,
            )
        )
    # 全员缺的排最前（决策信号最强），其次按涉及人数降序，最后按条目名保证稳定
    common.sort(key=lambda gap: (not gap.all_missing, -len(gap.missing_for), gap.dimension, gap.item))
    return stats, common[:_COMMON_GAP_LIMIT]
