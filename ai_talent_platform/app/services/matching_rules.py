"""
功能：用确定性代码判断岗位硬条件。
输入：人才档案、岗位要求、规则配置。
输出：PASS/FAIL/UNKNOWN/NEEDS_REVIEW及原因。
流程：读取要求 → 核对档案 → 判断四态 → 汇总状态与完整度。
边界：UNKNOWN表示信息不足，不能直接当不合格；不调用模型或数据库。

标签：[Agent组] [P1]（匹配硬性规则与评分口径）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from app.ai.skills.matching_skill import education_level, normalize_skills
from app.schemas.matching import HardConditionResult, HardStatus
from app.schemas.position import JobProfileParsed

logger = logging.getLogger(__name__)

# *kwq* 新增：默认权重/惩罚常量（全部为【暂定值·待业务确认】，通过 HardRuleConfig 可覆盖）。
UNKNOWN_PENALTY_DEFAULT = 0.15      # B-2 待确认：UNKNOWN 对综合分的降权比例
PREFERRED_SKILL_BONUS_DEFAULT = 0.0  # B-4 待确认：优先技能加分（默认 0，即不自动加分）

# *kwq* 新增：判断「条件文本是否已被结构化字段覆盖」的关键词表。
# 用途：避免同一条要求既产生结构化判定又产生一条 NEEDS_REVIEW 重复项。
_EDUCATION_KEYWORDS = ("学历", "本科", "硕士", "博士", "大专", "专科", "中专", "高中", "学位")
_WORK_YEARS_KEYWORDS = ("工作经验", "工作年限", "年经验", "年以上", "年限", "工龄")


# *kwq* 新增：可配置的硬性判定口径。
# 原因：任务书明确 B-1~B-4 未确认，临时实现必须可配置，不得把临时口径伪装成最终业务规则。
@dataclass(frozen=True)
class HardRuleConfig:
    """四态判定口径配置（默认值均为暂定，待业务负责人确认后调整）。"""

    # B-1 待确认：必备技能是「全部命中才算满足」还是「命中其一即可」还是「部分命中转人工」。
    #   可选值：all（默认，严格按 V1.4「必要条件」语义）/ any / partial_review
    required_skill_mode: Literal["all", "any", "partial_review"] = "all"
    # 待确认：work_years=0 时，视为「应届（事实）」直接判 FAIL，还是「可能未填」转人工复核。
    zero_work_years_as_review: bool = False
    # B-2 待确认：存在 UNKNOWN 项时，综合分降权比例。
    unknown_penalty: float = UNKNOWN_PENALTY_DEFAULT
    # B-4 待确认：优先技能命中是否加分（默认 0，不加分，仅记录）。
    preferred_skill_bonus: float = PREFERRED_SKILL_BONUS_DEFAULT
    # 兼容开关：岗位没有 required_skills 时，是否把 positions.skill_tags 当作必备技能。
    #   默认 False —— 修复「skill_tags 全量当必备导致过度淘汰」的问题（复核报告问题①）。
    skill_tags_as_hard: bool = False


# *kwq* 新增：模块级默认配置（调用方可按需传入自己的配置覆盖）。
DEFAULT_HARD_RULE_CONFIG = HardRuleConfig()


@dataclass(frozen=True)
class _ProfileView:
    """岗位要求的统一内部视图（屏蔽 JobProfileParsed / requirements JSON / ORM 三种入参差异）。"""

    min_education: str | None
    min_work_years: float | None
    required_skills: list[str]
    preferred_skills: list[str]
    condition_texts: list[str]
    position_id: int | None


# ------------------------------------------------------------------ 基础工具


def _as_dict(value: Any) -> dict[str, Any]:
    """ORM 对象 / dict 统一转 dict（与 app/patterns/strategy.py 的处理方式保持一致）。"""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return result if isinstance(result, dict) else {}
    # ORM 对象兜底：读取已知列
    data: dict[str, Any] = {}
    for field in ("id", "talent_id", "name", "education", "work_years", "skill_tags", "potential_level"):
        if hasattr(value, field):
            data[field] = getattr(value, field)
    return data


def _to_float(value: Any) -> float | None:
    """安全转 float：None/空串/无法解析统一返回 None（不抛异常，避免一条脏数据中断整批）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _condition_texts(value: Any) -> list[str]:
    """把多种形态的条件集合统一成文本列表。

    支持：list[str] / list[dict{"text": ...}] / list[ConditionItem] / 单个 str。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    texts: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if text:
            texts.append(text)
    return texts


def build_profile_view(job_profile: Any, config: HardRuleConfig | None = None) -> _ProfileView:
    """把岗位要求（JobProfileParsed / positions.requirements JSON / ORM）统一成内部视图。"""
    cfg = config or DEFAULT_HARD_RULE_CONFIG

    # 路径一：JD 解析后的标准画像
    if isinstance(job_profile, JobProfileParsed):
        return _ProfileView(
            min_education=job_profile.min_education,
            min_work_years=(
                float(job_profile.min_work_years) if job_profile.min_work_years is not None else None
            ),
            required_skills=[str(s).strip() for s in job_profile.required_skills if str(s).strip()],
            preferred_skills=[str(s).strip() for s in job_profile.preferred_skills if str(s).strip()],
            condition_texts=_condition_texts(job_profile.required_conditions),
            position_id=None,
        )

    # 路径二：positions.requirements JSON / ORM 对象（历史数据结构）
    data = _as_dict(job_profile)
    requirements = data.get("requirements")
    req: dict[str, Any] = requirements if isinstance(requirements, dict) else {}

    min_education = req.get("min_education") or req.get("education") or data.get("min_education")

    raw_years = req.get("min_work_years")
    if raw_years is None:
        raw_years = req.get("experience_years")
    if raw_years is None:
        raw_years = data.get("min_work_years")

    required_skills = [str(s).strip() for s in (req.get("required_skills") or []) if str(s).strip()]
    # *kwq* 修改：原硬过滤在无 required_skills 时回落到 position.skill_tags 且要求 100% 命中。
    # 修改原因：JD 中「了解 X 优先」这类技能极易混进 skill_tags，导致全员被淘汰；
    #          默认改为「未显式声明必备技能则不做技能硬门槛」，如需旧行为把 skill_tags_as_hard 置 True。
    if not required_skills and cfg.skill_tags_as_hard:
        required_skills = [str(s).strip() for s in (data.get("skill_tags") or []) if str(s).strip()]

    preferred_skills = [str(s).strip() for s in (req.get("preferred_skills") or []) if str(s).strip()]

    condition_texts = _condition_texts(req.get("required_conditions")) or _condition_texts(
        req.get("conditions")
    )

    return _ProfileView(
        min_education=str(min_education).strip() if min_education else None,
        min_work_years=_to_float(raw_years),
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        condition_texts=condition_texts,
        position_id=data.get("id") or data.get("position_id"),
    )


def build_evidence_id(position_id: int | None, talent_id: int | None, seq: int) -> str | None:
    """生成结构化证据 ID：EVID-{position_id}-{talent_id}-{seq}。

    返回 None 表示入参缺少 ID（此时不生成证据，避免出现无法校验的假 ID）。
    """
    if position_id is None or talent_id is None:
        return None
    return f"EVID-{int(position_id)}-{int(talent_id)}-{int(seq)}"


# ------------------------------------------------------------------ 单项判定


def _judge_education(
    view: _ProfileView, talent: dict[str, Any], next_eid: Callable[[], str | None]
) -> HardConditionResult | None:
    """学历判定：缺失 -> UNKNOWN；无法识别 -> NEEDS_REVIEW；达标 -> PASS；不达标 -> FAIL。"""
    required = view.min_education
    if not required:
        # 岗位未提及学历要求 -> 不产生判定条目（不猜、不补值）
        return None

    actual = talent.get("education")
    if actual is None or str(actual).strip() == "":
        # *kwq* 新增：学历缺失判 UNKNOWN（原逻辑会算成 0 级并判「学历未达到」而淘汰）。
        return HardConditionResult(
            item="min_education",
            required=required,
            actual=None,
            status=HardStatus.UNKNOWN,
            reason=f"人才档案缺少学历信息，无法判断是否达到「{required}」",
            evidence_id=None,
        )

    required_level = education_level(required)
    if required_level == 0:
        return HardConditionResult(
            item="min_education",
            required=required,
            actual=actual,
            status=HardStatus.NEEDS_REVIEW,
            reason=f"岗位学历要求「{required}」无法映射为标准学历等级，需人工确认",
            evidence_id=None,
        )

    actual_level = education_level(actual)
    if actual_level == 0:
        return HardConditionResult(
            item="min_education",
            required=required,
            actual=actual,
            status=HardStatus.NEEDS_REVIEW,
            reason=f"人才学历「{actual}」无法映射为标准学历等级，需人工确认",
            evidence_id=None,
        )

    if actual_level >= required_level:
        return HardConditionResult(
            item="min_education",
            required=required,
            actual=actual,
            status=HardStatus.PASS,
            reason=f"学历满足：要求「{required}」，实际「{actual}」",
            evidence_id=next_eid(),
        )

    return HardConditionResult(
        item="min_education",
        required=required,
        actual=actual,
        status=HardStatus.FAIL,
        reason=f"学历不满足：要求「{required}」，实际「{actual}」",
        evidence_id=next_eid(),
    )


def _judge_work_years(
    view: _ProfileView,
    talent: dict[str, Any],
    next_eid: Callable[[], str | None],
    config: HardRuleConfig,
) -> HardConditionResult | None:
    """工作年限判定：缺失 -> UNKNOWN；无法解析 -> NEEDS_REVIEW；达标 -> PASS；不足 -> FAIL。"""
    required = view.min_work_years
    if required is None or required <= 0:
        return None

    raw = talent.get("work_years")
    if raw is None or raw == "":
        # *kwq* 新增：年限缺失判 UNKNOWN（原逻辑 float(None or 0)=0 -> 判「年限不足」淘汰）。
        return HardConditionResult(
            item="min_work_years",
            required=required,
            actual=None,
            status=HardStatus.UNKNOWN,
            reason=f"人才档案缺少工作年限信息，无法判断是否达到 {required:g} 年",
            evidence_id=None,
        )

    actual = _to_float(raw)
    if actual is None:
        return HardConditionResult(
            item="min_work_years",
            required=required,
            actual=raw,
            status=HardStatus.NEEDS_REVIEW,
            reason=f"工作年限「{raw}」不是有效数值，需人工确认",
            evidence_id=None,
        )

    if actual == 0 and config.zero_work_years_as_review:
        # 待确认口径：0 年可能是「应届」也可能是「解析缺失」，保守转人工而不直接淘汰。
        return HardConditionResult(
            item="min_work_years",
            required=required,
            actual=actual,
            status=HardStatus.NEEDS_REVIEW,
            reason=f"工作年限为 0，无法确认是应届生还是档案缺失（要求 {required:g} 年）",
            evidence_id=None,
        )

    if actual >= required:
        return HardConditionResult(
            item="min_work_years",
            required=required,
            actual=actual,
            status=HardStatus.PASS,
            reason=f"工作年限满足：要求 {required:g} 年，实际 {actual:g} 年",
            evidence_id=next_eid(),
        )

    return HardConditionResult(
        item="min_work_years",
        required=required,
        actual=actual,
        status=HardStatus.FAIL,
        reason=f"工作年限不满足：要求 {required:g} 年，实际 {actual:g} 年",
        evidence_id=next_eid(),
    )


def _judge_required_skills(
    view: _ProfileView,
    talent: dict[str, Any],
    next_eid: Callable[[], str | None],
    config: HardRuleConfig,
) -> HardConditionResult | None:
    """必备技能判定：技能字段缺失 -> UNKNOWN；命中 -> PASS；未命中 -> 按 B-1 口径处理。"""
    required = view.required_skills
    if not required:
        return None

    required_set = {str(s).strip().lower() for s in required if str(s).strip()}
    raw_skills = talent.get("skill_tags")
    talent_set = normalize_skills(raw_skills)

    if not talent_set:
        # *kwq* 新增：技能字段缺失判 UNKNOWN（原逻辑空集 issubset 恒 False -> 全员淘汰）。
        sample = "/".join(sorted(required_set)[:5])
        return HardConditionResult(
            item="required_skills",
            required=sorted(required_set),
            actual=None if raw_skills is None else [],
            status=HardStatus.UNKNOWN,
            reason=f"人才档案缺少技能标签，无法判断是否具备必备技能：{sample}",
            evidence_id=None,
        )

    missing = sorted(required_set - talent_set)
    hit_count = len(required_set) - len(missing)

    if not missing:
        return HardConditionResult(
            item="required_skills",
            required=sorted(required_set),
            actual=sorted(talent_set),
            status=HardStatus.PASS,
            reason=f"必备技能全部命中（{hit_count}/{len(required_set)}）",
            evidence_id=next_eid(),
        )

    # B-1 待确认：必备技能部分命中如何处置，默认按「全部命中才算满足」（all）。
    if config.required_skill_mode == "any":
        if hit_count > 0:
            return HardConditionResult(
                item="required_skills",
                required=sorted(required_set),
                actual=sorted(talent_set),
                status=HardStatus.PASS,
                reason=f"必备技能命中 {hit_count}/{len(required_set)}，按 any 口径视为满足",
                evidence_id=next_eid(),
            )
    elif config.required_skill_mode == "partial_review":
        return HardConditionResult(
            item="required_skills",
            required=sorted(required_set),
            actual=sorted(talent_set),
            status=HardStatus.NEEDS_REVIEW,
            reason=f"必备技能命中 {hit_count}/{len(required_set)}，缺：{'/'.join(missing)}，需人工确认",
            evidence_id=None,
        )

    return HardConditionResult(
        item="required_skills",
        required=sorted(required_set),
        actual=sorted(talent_set),
        status=HardStatus.FAIL,
        reason=f"缺少必备技能：{'/'.join(missing)}（命中 {hit_count}/{len(required_set)}）",
        evidence_id=next_eid(),
    )


def _is_covered_by_structured(text: str, view: _ProfileView) -> bool:
    """判断一条条件文本是否已被结构化字段（学历/年限/技能）覆盖，避免重复产生 NEEDS_REVIEW。"""
    lowered = text.lower()
    if view.min_education and any(keyword in text for keyword in _EDUCATION_KEYWORDS):
        return True
    if view.min_work_years and any(keyword in text for keyword in _WORK_YEARS_KEYWORDS):
        return True
    for skill in view.required_skills:
        skill_text = str(skill).strip().lower()
        if skill_text and skill_text in lowered:
            return True
    return False


def _judge_vague_conditions(
    view: _ProfileView, next_eid: Callable[[], str | None]
) -> list[HardConditionResult]:
    """非结构化必要条件判定：无法自动判定的转 NEEDS_REVIEW（不淘汰、不当作满足）。"""
    results: list[HardConditionResult] = []
    for text in view.condition_texts:
        if _is_covered_by_structured(text, view):
            continue
        results.append(
            HardConditionResult(
                item="required_condition",
                required=text,
                actual=None,
                status=HardStatus.NEEDS_REVIEW,
                reason=f"条件「{text}」为非结构化描述，无法自动判定，需人工复核",
                evidence_id=next_eid(),
            )
        )
    return results


# ------------------------------------------------------------------ 对外主函数


def judge_hard_conditions(
    talent: Any,
    job_profile: Any,
    config: HardRuleConfig | None = None,
    position_id: int | None = None,
    talent_id: int | None = None,
) -> list[HardConditionResult]:
    """对单个候选人执行硬性条件四态判定（纯函数，无 IO）。

    参数：
        talent:       人才数据（dict 或 ORM 对象），需含 education / work_years / skill_tags
        job_profile:  岗位要求（JobProfileParsed / positions.requirements JSON / ORM）
        config:       判定口径（默认 DEFAULT_HARD_RULE_CONFIG，口径未确认项见注释）
        position_id:  岗位 ID（用于生成证据 ID，未知时可不传）
        talent_id:    人才 ID（同上）

    返回：
        list[HardConditionResult]，每项含状态与原因；空列表表示岗位没有任何可硬性判定的条件。

    注意：
        - 只有 FAIL 允许淘汰；UNKNOWN / NEEDS_REVIEW 必须保留（见 aggregate_status / is_excluded）。
        - 本函数不修改入参，不读写数据库，不调用大模型。
    """
    cfg = config or DEFAULT_HARD_RULE_CONFIG
    data = _as_dict(talent)
    view = build_profile_view(job_profile, cfg)

    pid = position_id if position_id is not None else view.position_id
    tid = talent_id if talent_id is not None else (data.get("id") or data.get("talent_id"))

    seq = 0

    def next_eid() -> str | None:
        nonlocal seq
        seq += 1
        return build_evidence_id(pid, tid, seq)

    results: list[HardConditionResult] = []

    for judge in (
        _judge_education(view, data, next_eid),
        _judge_work_years(view, data, next_eid, cfg),
        _judge_required_skills(view, data, next_eid, cfg),
    ):
        if judge is not None:
            results.append(judge)

    results.extend(_judge_vague_conditions(view, next_eid))
    return results


# ------------------------------------------------------------------ 聚合工具


def _status_priority(status: HardStatus) -> int:
    """状态严重度（数值越大越严重），用于聚合出一个整体结论。"""
    return {
        HardStatus.PASS: 1,
        HardStatus.UNKNOWN: 2,
        HardStatus.NEEDS_REVIEW: 3,
        HardStatus.FAIL: 4,
    }[status]


def aggregate_status(results: list[HardConditionResult]) -> HardStatus:
    """把多项判定聚合为整体状态：FAIL > NEEDS_REVIEW > UNKNOWN > PASS。"""
    if not results:
        return HardStatus.PASS
    return max((item.status for item in results), key=_status_priority)


def is_excluded(results: list[HardConditionResult]) -> bool:
    """是否应被淘汰：仅当存在 FAIL 项。

    UNKNOWN / NEEDS_REVIEW 一律不淘汰 —— 这是本次修复的核心红线。
    """
    return any(item.status == HardStatus.FAIL for item in results)


def has_unknown(results: list[HardConditionResult]) -> bool:
    """是否存在信息缺失导致的 UNKNOWN。"""
    return any(item.status == HardStatus.UNKNOWN for item in results)


def has_needs_review(results: list[HardConditionResult]) -> bool:
    """是否存在需人工复核项。"""
    return any(item.status == HardStatus.NEEDS_REVIEW for item in results)


def count_by_status(results: list[HardConditionResult]) -> dict[str, int]:
    """按状态统计数量（用于跑批统计 MatchStats）。"""
    counts = {status.value: 0 for status in HardStatus}
    for item in results:
        counts[item.status.value] += 1
    return counts


def compute_data_completeness(talent: Any) -> float:
    """计算档案完整度（0~1）：学历 / 工作年限 / 技能标签 三项关键字段的有值比例。

    用途：提示「该候选人结论建立在缺失数据上」，避免默认值掩盖数据缺口。
    """
    data = _as_dict(talent)
    fields = ("education", "work_years", "skill_tags")
    present = 0
    for field in fields:
        value = data.get(field)
        if value is None or value == "" or value == []:
            continue
        present += 1
    return round(present / len(fields), 2)


def apply_unknown_penalty_by_flag(
    score: float,
    unknown_present: bool,
    config: HardRuleConfig | None = None,
) -> float:
    """按「是否存在 UNKNOWN」的布尔标记降权（供已序列化为 dict 的调用方复用同一口径）。

    为什么需要它：HybridStrategy 里硬条件结果已被序列化成 dict（经软匹配重算分数后），
    若各自写一遍 `score * (1 - penalty)`，口径会漂移，故统一收敛到本函数。
    """
    cfg = config or DEFAULT_HARD_RULE_CONFIG
    if not unknown_present:
        return score
    penalty = max(0.0, min(1.0, cfg.unknown_penalty))
    return round(score * (1 - penalty), 2)


def apply_unknown_penalty(
    score: float,
    results: list[HardConditionResult],
    config: HardRuleConfig | None = None,
) -> float:
    """存在 UNKNOWN 时对综合分降权（B-2 待确认，默认降 15%）。

    设计意图：UNKNOWN 不淘汰，但也不能和「证据齐全的 PASS」同分同权，
    否则缺档案的人会凭默认分排到前面。
    """
    return apply_unknown_penalty_by_flag(score, has_unknown(results), config)


def preferred_skill_hits(talent: Any, job_profile: Any, config: HardRuleConfig | None = None) -> list[str]:
    """计算优先技能命中项（B-4 未确认，默认不参与加分，仅用于记录与展示）。"""
    cfg = config or DEFAULT_HARD_RULE_CONFIG
    view = build_profile_view(job_profile, cfg)
    if not view.preferred_skills:
        return []
    talent_set = normalize_skills(_as_dict(talent).get("skill_tags"))
    return sorted({s for s in view.preferred_skills if s.strip().lower() in talent_set})
