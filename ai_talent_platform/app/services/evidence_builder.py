"""
功能：将岗位、人才原值和计算结果组成可追溯证据。
输入：岗位、人才、四态结果、维度分、画像版本。
输出：MatchEvidencePack结构化证据包。
流程：统一数据形态 → 分配证据ID → 收集事实/推导/缺失 → 自检。
边界：纯函数无IO；不引用AI画像摘要，不把岗位要求当人才事实。

标签：[Agent组] [P1]（匹配证据包构建，MATCH-004）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.ai.skills.matching_skill import normalize_skills
from app.core.exceptions import ParamError
from app.schemas.matching import (
    Evidence,
    HardConditionResult,
    HardStatus,
    MatchEvidencePack,
)
from app.schemas.position import ConditionItem, JobProfileParsed
from app.services.matching_rules import (
    DEFAULT_HARD_RULE_CONFIG,
    HardRuleConfig,
    build_evidence_id,
    build_profile_view,
    compute_data_completeness,
    judge_hard_conditions,
)

logger = logging.getLogger(__name__)

# *kwq* 新增：硬条件项 -> 人才档案字段的映射（决定证据的 source_table / source_field）。
# 原因：任务书要求每条证据保留「来源表 + 来源字段 + 原始值」，
#      而 HardConditionResult 只带 actual（已归一化），拿不到原始表达，
#      必须回到人才数据里取原值。
_TALENT_ITEM_FIELDS: dict[str, str] = {
    "min_education": "education",
    "min_work_years": "work_years",
    "required_skills": "skill_tags",
}

# *kwq* 新增：维度 -> 事实来源字段（维度分证据的溯源依据）。
# 原因：compute_match_score 的四个维度各自建立在一个档案字段上，
#      解释时必须能回答「这个分数是拿哪个字段算出来的、字段是不是空的」。
_DIMENSION_SOURCES: dict[str, tuple[str, str]] = {
    "skill": ("talents", "skill_tags"),
    "experience": ("talents", "work_years"),
    "education": ("talents", "education"),
    "quality": ("talents", "potential_level"),
}

_DIMENSION_LABELS: dict[str, str] = {
    "skill": "技能",
    "experience": "经验",
    "education": "学历",
    "quality": "素质",
}

# *kwq* 新增：关键档案字段的中文名（缺失证据的可读描述用）。
_PROFILE_FIELD_LABELS: dict[str, str] = {
    "education": "最高学历",
    "work_years": "工作年限",
    "skill_tags": "技能标签",
    "potential_level": "潜力等级",
}

# *kwq* 新增：明确排除的证据来源。
# 原因：portrait_summary 由大模型生成（见 models/talent.py 注释「画像摘要」），
#      用它当证据等于「模型生成的话证明模型自己的结论」，是典型的循环论证。
_EXCLUDED_SOURCES = frozenset({"portrait_summary"})

_MAX_CAPACITY_TERMS = 20
_MAX_REQUIREMENT_REF = 64

# *kwq* 新增：职责文本中的英文技术词提取（用于「职责里的 API」对「技能里的 FastAPI」这类包含关系）。
# 说明：只提取长度 >= 2 的英文/数字词，中文不参与（中文分词歧义大，硬切会造出假命中）。
_ASCII_TOKEN = re.compile(r"[a-z0-9+#.]{2,}")


# *kwq* 新增：证据构建配置（可注入，便于后续按业务口径调整而不用改代码）。
@dataclass(frozen=True)
class EvidenceBuildConfig:
    """证据构建口径（默认值不涉及业务判断，仅控制证据颗粒度与长度）。"""

    max_claim_length: int = 200          # Evidence.claim 上限（与 Schema 一致）
    max_responsibility_evidence: int = 5  # 岗位职责证据最多产出条数，避免证据包膨胀
    include_responsibility: bool = True   # 是否构建岗位职责相关证据
    include_dimension: bool = True        # 是否构建维度分来源证据
    include_missing_fields: bool = True    # 是否构建档案缺失字段证据


DEFAULT_EVIDENCE_CONFIG = EvidenceBuildConfig()


# ------------------------------------------------------------------ 基础工具


def _as_dict(value: Any) -> dict[str, Any]:
    """ORM 对象 / Pydantic 模型 / dict 统一转 dict（与 strategy/matching_rules 口径一致）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, JobProfileParsed):
        return value.model_dump()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, dict):
            return result
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump()
        if isinstance(result, dict):
            return result
    return {}


def _is_blank(value: Any) -> bool:
    """判断字段是否「等同于没有值」（None / 空串 / 空列表 / 空字典）。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _clip(text: Any, limit: int) -> str:
    """截断到指定长度，且保证非空（Schema 要求 min_length=1）。"""
    value = str(text or "").strip()
    if not value:
        return "（无描述）"
    return value[:limit]


def _dedup_terms(terms: Iterable[Any], limit: int = _MAX_CAPACITY_TERMS) -> list[str]:
    """能力术语去重（忽略大小写）并限制数量，避免证据包过大。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in terms:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


# *kwq* 新增（MATCH-004）：证据 ID 分配器。
# 用途：让「四态判定结果」与「证据」用同一套编号，下游才能按 ID 校验引用是否成立。
class _EvidenceIdAllocator:
    """证据 ID 分配器：保证同一 (岗位, 人才) 内 ID 唯一且格式一致。

    ID 规则沿用 matching_rules.build_evidence_id：EVID-{position_id}-{talent_id}-{seq}。
    关键点：必须复用 MATCH-002 已分配的 evidence_id，并在其最大序号之后继续编号，
    否则 HardConditionResult.evidence_id 会指向另一条证据（链接断裂，校验必然误判）。
    """

    def __init__(self, position_id: int, talent_id: int) -> None:
        self.position_id = int(position_id)
        self.talent_id = int(talent_id)
        self._prefix = f"EVID-{self.position_id}-{self.talent_id}-"
        self._used: set[str] = set()
        self._seq = 0

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _seq_of(evidence_id: str) -> int:
        try:
            return int(evidence_id.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            return 0

    # ------------------------------------------------------------------ 对外
    def is_own(self, evidence_id: str | None) -> bool:
        """该 ID 是否属于当前 (岗位, 人才) 组合（防止跨对象串用 ID）。"""
        return bool(evidence_id) and str(evidence_id).startswith(self._prefix)

    def reserve(self, evidence_id: str | None) -> str | None:
        """登记一个既有 ID；不属于本组合或已占用时返回 None（交由 allocate 重新分配）。"""
        if not self.is_own(evidence_id) or evidence_id in self._used:
            return None
        assert evidence_id is not None
        self._used.add(evidence_id)
        self._seq = max(self._seq, self._seq_of(evidence_id))
        return evidence_id

    def allocate(self) -> str:
        """分配一个新 ID（从已用最大序号继续，保证不与既有 ID 冲突）。"""
        while True:
            self._seq += 1
            evidence_id = build_evidence_id(self.position_id, self.talent_id, self._seq)
            if evidence_id and evidence_id not in self._used:
                self._used.add(evidence_id)
                return evidence_id

    @property
    def used(self) -> set[str]:
        return set(self._used)


# ------------------------------------------------------------------ 岗位画像


# *kwq* 新增（MATCH-004）：岗位 -> 岗位画像的转换器。
# 用途：证据包里必须带「当时依据的岗位要求」，而 position 可能是 ORM / requirements JSON。
# 约束：结构化部分复用 MATCH-002 的 build_profile_view，不另写一套解析逻辑。
def build_job_profile(position: Any, rule_config: HardRuleConfig | None = None) -> JobProfileParsed:
    """把岗位（ORM / dict / JobProfileParsed）统一成结构化画像。

    说明：结构化部分复用 matching_rules.build_profile_view（与四态判定同一口径），
          不在这里另写一套解析规则，避免「判定依据」和「证据描述」两套逻辑漂移。
    注意：不做任何补值 —— JD 未提及的字段保持 None。
    """
    if isinstance(position, JobProfileParsed):
        return position

    cfg = rule_config or DEFAULT_HARD_RULE_CONFIG
    data = _as_dict(position)
    view = build_profile_view(position, cfg)

    requirements = data.get("requirements")
    req: dict[str, Any] = requirements if isinstance(requirements, dict) else {}

    title = data.get("title") or data.get("job_title") or req.get("job_title")
    if not title or not str(title).strip():
        raise ParamError("岗位缺少 title，无法构建岗位画像证据（禁止用占位名称代替真实岗位）")

    raw_years = view.min_work_years
    min_work_years = None if raw_years is None else min(50, max(0, int(raw_years)))

    responsibilities = req.get("responsibilities") or data.get("responsibilities") or []
    if isinstance(responsibilities, str):
        responsibilities = [responsibilities]
    responsibilities = [str(item).strip() for item in responsibilities if str(item).strip()]

    preferred_conditions = [
        ConditionItem(text=str(skill).strip(), normalized=str(skill).strip())
        for skill in view.preferred_skills
        if str(skill).strip()
    ]

    missing_fields = [str(item) for item in (req.get("missing_fields") or []) if str(item).strip()]
    conflicts = [str(item) for item in (req.get("conflicts") or []) if str(item).strip()]

    salary_range = req.get("salary_range")
    if not salary_range and (data.get("salary_min") is not None or data.get("salary_max") is not None):
        salary_range = f"{data.get('salary_min')}-{data.get('salary_max')}"

    return JobProfileParsed(
        job_title=str(title).strip(),
        responsibilities=responsibilities,
        required_conditions=[
            ConditionItem(text=text) for text in view.condition_texts if text.strip()
        ],
        preferred_conditions=preferred_conditions,
        required_skills=view.required_skills,
        preferred_skills=view.preferred_skills,
        min_education=view.min_education,
        min_work_years=min_work_years,
        location=req.get("location") or data.get("location"),
        salary_range=str(salary_range) if salary_range else None,
        missing_fields=missing_fields,
        conflicts=conflicts,
        review_required=bool(req.get("review_required") or missing_fields or conflicts),
        source=req.get("source") if req.get("source") in {"dify", "llm_fallback", "manual"} else "manual",
        prompt_version=str(req.get("prompt_version") or "v1")[:20],
    )


# ------------------------------------------------------------------ 证据：硬条件


def _capacity_terms(
    result: HardConditionResult,
    talent_data: dict[str, Any],
    profile: JobProfileParsed | None = None,
) -> list[str]:
    """计算某条证据「允许被解释成的能力术语」白名单。

    口径：只收录证据自身文本里出现过的能力名词（要求项 + 人才实际值），
         解释里出现白名单外的能力术语即视为越界（由 MATCH-005/007 的校验器执行）。
    注意：技能名优先取岗位画像的原始写法（result.required 已被四态判定归一化为小写，
         直接使用会让证据里出现 "python" 这种非原始值，与 source_value 大小写不一致）。
    """
    item = result.item
    if item == "min_education":
        return _dedup_terms([result.required, result.actual])
    if item == "min_work_years":
        terms: list[str] = ["工作年限"]
        for value in (result.actual, result.required):
            try:
                terms.append(f"{float(value):g}年")
            except (TypeError, ValueError):
                continue
        return _dedup_terms(terms)
    if item == "required_skills":
        required = list(profile.required_skills) if profile is not None else result.required
        if not isinstance(required, list):
            required = []
        raw_skills = talent_data.get("skill_tags")
        actual = raw_skills if isinstance(raw_skills, list) else []
        return _dedup_terms([*required, *actual])
    # 非结构化条件：没有可验证的能力术语，白名单必须为空（否则等于给了编造许可）
    return []


# *kwq* 新增（MATCH-004）：四态判定结果 -> 证据。
# 关键：source_value 取人才档案原始值，而不是 HardConditionResult.actual（已被归一化）。
def _evidence_from_hard_result(
    result: HardConditionResult,
    talent_data: dict[str, Any],
    evidence_id: str,
    config: EvidenceBuildConfig,
    profile: JobProfileParsed | None = None,
) -> Evidence:
    """把一条四态判定结果转成证据。

    关键：source_value 取人才档案的**原始字段值**（保留大小写与顺序），
          而不是 HardConditionResult.actual（已归一化为小写集合），
          否则「原始值」这一要求无法满足，也无法向 HR 展示真实记录。
    """
    field = _TALENT_ITEM_FIELDS.get(result.item)
    if field:
        source_table, source_field = "talents", field
        raw_value = talent_data.get(field)
        source_value: Any = raw_value if raw_value is not None else result.actual
    else:
        # 非结构化条件：依据来自 JD 原文，不是人才档案
        source_table, source_field = "positions", "required_conditions"
        source_value = result.required if result.required is not None else result.actual

    if result.status == HardStatus.UNKNOWN or _is_blank(source_value):
        evidence_type = "missing"
    elif result.item == "required_condition" and result.status == HardStatus.NEEDS_REVIEW:
        # 条件不可机读 -> 判断依据缺失（不是「人才不满足」，也不是「人才满足」）
        evidence_type = "missing"
    else:
        evidence_type = "derived"

    # *kwq* 修改（MATCH-008 实测修复）：missing 证据一律不授予能力术语。
    # 修改原因：education 为 UNKNOWN 时，本函数把「岗位要求值 本科」写进了 capacity_terms，
    #          于是审计报「missing 证据带有能力术语」—— 语义上等于给「档案无学历」
    #          这张证据发了「可以据此说候选人具备本科」的许可证，与防编造目标冲突。
    #          校验器虽然已对 missing 强制忽略术语（_evidence_terms 返回空），
    #          但构建器不应依赖下游兜底，应在源头置空。
    # 影响范围：仅 missing 类证据的 capacity_terms 由非空变空；fact/derived 不变。
    return Evidence(
        evidence_id=evidence_id,
        claim=_clip(result.reason, config.max_claim_length),
        source_table=source_table,
        source_field=source_field,
        source_value=source_value,
        requirement_ref=result.item[:_MAX_REQUIREMENT_REF],
        evidence_type=evidence_type,
        capacity_terms=[] if evidence_type == "missing" else _capacity_terms(result, talent_data, profile),
    )


# ------------------------------------------------------------------ 证据：优先技能


def _preferred_skill_evidences(
    profile: JobProfileParsed,
    talent_data: dict[str, Any],
    allocator: _EvidenceIdAllocator,
    config: EvidenceBuildConfig,
) -> list[Evidence]:
    """优先技能证据：只记录「命中」，未命中不构成证据（缺失≠不具备）。"""
    preferred = [str(item).strip() for item in profile.preferred_skills if str(item).strip()]
    if not preferred:
        return []

    talent_set = normalize_skills(talent_data.get("skill_tags"))
    if not talent_set:
        return [
            Evidence(
                evidence_id=allocator.allocate(),
                claim=_clip(
                    f"人才档案缺少技能标签，无法判断是否具备优先技能：{'/'.join(preferred[:5])}",
                    config.max_claim_length,
                ),
                source_table="talents",
                source_field="skill_tags",
                source_value=None,
                requirement_ref="preferred_skills",
                evidence_type="missing",
                capacity_terms=[],
            )
        ]

    evidences: list[Evidence] = []
    for skill in preferred:
        if skill.lower() not in talent_set:
            continue
        evidences.append(
            Evidence(
                evidence_id=allocator.allocate(),
                claim=_clip(f"命中优先技能「{skill}」（优先条件，不参与淘汰；是否加分待业务确认）", config.max_claim_length),
                source_table="talents",
                source_field="skill_tags",
                source_value=skill,
                requirement_ref="preferred_skills",
                evidence_type="fact",
                capacity_terms=[skill],
            )
        )
    return evidences


# ------------------------------------------------------------------ 证据：岗位职责


def _responsibility_evidences(
    profile: JobProfileParsed,
    talent_data: dict[str, Any],
    allocator: _EvidenceIdAllocator,
    config: EvidenceBuildConfig,
) -> list[Evidence]:
    """岗位职责相关证据：仅按「人才已有技能标签与职责文本的确定性重合」构建。

    局限（必须如实告知，不允许包装成「已实现职责语义匹配」）：
        这里做的是字符串包含判断，不是语义匹配；
        人才档案中不存在职责经历的结构化字段，无法证明「做过该职责」，
        因此本函数只产出「职责涉及的能力，档案里确有其技能标签」这类可核验事实。
        副作用提示：英文短词包含关系可能误命中（如 java 与 javascript），
        故只在英文词上做包含判断，并把命中的原始技能标签作为 source_value 一并留证。
    """
    if not config.include_responsibility:
        return []
    responsibilities = [str(item).strip() for item in profile.responsibilities if str(item).strip()]
    if not responsibilities:
        return []

    raw_skills = talent_data.get("skill_tags")
    talent_terms = {
        str(item).strip().lower(): str(item).strip()
        for item in (raw_skills or [])
        if str(item).strip()
    }
    if not talent_terms:
        # 技能为空时，职责证据无法构建；缺口由「缺失字段证据」统一表达，这里不重复产出
        return []

    evidences: list[Evidence] = []
    for index, text in enumerate(responsibilities[: config.max_responsibility_evidence]):
        lowered = text.lower()
        tokens = set(_ASCII_TOKEN.findall(lowered))
        # *kwq* 修改（F-6 修复）：英文技能改为「词元精确相等」，不再用包含关系。
        # 修改原因：原实现 `term in lowered` / `token in term` 会把 java 命中到 javascript、
        #          go 命中到 django，产出「职责涉及 Java，档案已包含」这类虚假事实证据，
        #          而证据是解释的唯一依据，假证据会直接导致假结论。
        #          中文没有分词歧义问题，仍保留子串判断（如「数据分析」）。
        # 影响范围：只影响职责证据的命中判定；技能类硬条件判定不受影响。
        hits = []
        for term, origin in talent_terms.items():
            is_ascii = bool(re.fullmatch(r"[a-z0-9+#.\-]+", term))
            if (term in tokens) if is_ascii else (term in lowered):
                hits.append(origin)
        if not hits:
            continue
        evidences.append(
            Evidence(
                evidence_id=allocator.allocate(),
                claim=_clip(
                    f"职责「{text[:40]}」涉及能力 {'、'.join(hits[:5])}，人才技能标签已包含该项",
                    config.max_claim_length,
                ),
                source_table="talents",
                source_field="skill_tags",
                source_value=_dedup_terms(hits),
                requirement_ref=f"responsibilities[{index}]"[:_MAX_REQUIREMENT_REF],
                evidence_type="fact",
                capacity_terms=_dedup_terms(hits),
            )
        )
    return evidences


# ------------------------------------------------------------------ 证据：维度分


def _dimension_evidences(
    talent_data: dict[str, Any],
    dimension_scores: dict[str, float],
    allocator: _EvidenceIdAllocator,
    config: EvidenceBuildConfig,
) -> list[Evidence]:
    """维度分来源证据：说明每个分数建立在哪个档案字段上、该字段是否为空。

    为什么必须产出：compute_match_score 在字段缺失时会用默认值（技能 80 / 素质 70），
        如果不标注，解释层会把「默认分」说成「能力良好」，这正是最难发现的一类幻觉。
    """
    if not config.include_dimension or not dimension_scores:
        return []

    evidences: list[Evidence] = []
    for dimension, score in dimension_scores.items():
        table, field = _DIMENSION_SOURCES.get(str(dimension), ("derived", "compute_match_score"))
        label = _DIMENSION_LABELS.get(str(dimension), str(dimension))
        field_value = talent_data.get(field) if table == "talents" else None
        blank = table == "talents" and _is_blank(field_value)

        if blank:
            claim = (
                f"{label}维度得分 {float(score):g}/100，但档案无「{_PROFILE_FIELD_LABELS.get(field, field)}」，"
                "该分数为默认值，不能作为能力结论"
            )
        else:
            claim = f"{label}维度得分 {float(score):g}/100，依据 {table}.{field}"

        evidences.append(
            Evidence(
                evidence_id=allocator.allocate(),
                claim=_clip(claim, config.max_claim_length),
                source_table=table,
                source_field=field,
                source_value=field_value if not blank else None,
                requirement_ref=f"dimension.{dimension}"[:_MAX_REQUIREMENT_REF],
                evidence_type="missing" if blank else "derived",
                capacity_terms=[],
            )
        )
    return evidences


# ------------------------------------------------------------------ 证据：缺失字段


# *kwq* 新增（MATCH-004）：档案缺失字段证据。
# 用途：把「没有数据」变成显式证据，防止解释层把缺失当成满足或当成优秀。
def _missing_field_evidences(
    talent_data: dict[str, Any],
    existing: list[Evidence],
    allocator: _EvidenceIdAllocator,
    config: EvidenceBuildConfig,
) -> list[Evidence]:
    """档案缺失字段证据：显式声明「某项结论无法给出」，替代编造。

    去重：同一 source_field 已有 missing 证据时不再重复产出（避免证据包堆重复项）。
    """
    if not config.include_missing_fields:
        return []

    covered = {item.source_field for item in existing if item.evidence_type == "missing"}
    evidences: list[Evidence] = []
    for field, label in _PROFILE_FIELD_LABELS.items():
        if field in _EXCLUDED_SOURCES or field in covered:
            continue
        if not _is_blank(talent_data.get(field)):
            continue
        evidences.append(
            Evidence(
                evidence_id=allocator.allocate(),
                claim=_clip(f"人才档案缺少「{label}」（{field}），相关结论无法给出", config.max_claim_length),
                source_table="talents",
                source_field=field,
                source_value=None,
                requirement_ref="talent_profile",
                evidence_type="missing",
                capacity_terms=[],
            )
        )
    return evidences


# ------------------------------------------------------------------ 对外主函数


# *kwq* 新增（MATCH-004 主入口）：构建匹配证据包。
# 调用方：MATCH-005（Dify 解释）、MATCH-007（证据校验）、匹配 Service 组装 MatchItem。
def build_match_evidence(
    position: Any,
    talent: Any,
    hard_results: list[HardConditionResult] | None = None,
    dimension_scores: dict[str, float] | None = None,
    *,
    position_id: int | None = None,
    talent_id: int | None = None,
    requirement_version: int = 1,
    config: EvidenceBuildConfig | None = None,
    rule_config: HardRuleConfig | None = None,
) -> MatchEvidencePack:
    """构建匹配证据包（纯函数，无 IO）。

    参数：
        position:         岗位数据（ORM / dict / JobProfileParsed），用于取岗位要求
        talent:           人才数据（ORM / dict），用于取档案原始值
        hard_results:     MATCH-002 输出的四态判定结果；传 None 时内部调用 judge_hard_conditions
        dimension_scores: 维度分（compute_match_score 的 dimensions），用于标注分数来源
        position_id / talent_id: 缺失时从数据里取；仍取不到则报错（无 ID 无法生成可校验的证据 ID）
        requirement_version: 本次匹配使用的岗位画像版本（供 MATCH-009 版本追溯）

    返回：
        MatchEvidencePack，可直接作为 MATCH-005（Dify 解释）与 MATCH-007（校验）的输入。

    保证：
        - 不修改入参（hard_results 中的 evidence_id 通过 model_copy 回填，原对象不变）；
        - 证据 ID 唯一且沿用 MATCH-002 已分配的序号，链接不断裂；
        - 不产出任何无来源的结论：缺失一律标记 missing，不补值不推测。
    """
    cfg = config or DEFAULT_EVIDENCE_CONFIG
    talent_data = _as_dict(talent)
    position_data = _as_dict(position)

    pid = position_id or position_data.get("id") or position_data.get("position_id")
    tid = talent_id or talent_data.get("id") or talent_data.get("talent_id")
    if pid is None or tid is None:
        raise ParamError("构建匹配证据需要 position_id 与 talent_id（缺少 ID 无法生成可校验的证据编号）")

    profile = build_job_profile(position, rule_config)

    if hard_results is None:
        # 复用四态判定器，不重复实现判定逻辑（任务书禁止重写 MATCH-002）
        hard_results = judge_hard_conditions(
            talent,
            position,
            config=rule_config,
            position_id=int(pid),
            talent_id=int(tid),
        )

    allocator = _EvidenceIdAllocator(int(pid), int(tid))

    # 第一遍：登记既有 evidence_id（保证 ID 链接；不合法/已占用的重新分配）
    resolved_ids: list[str] = []
    for item in hard_results:
        reserved = allocator.reserve(item.evidence_id)
        resolved_ids.append(reserved or "")

    # 第二遍：为没有合法 ID 的结果分配新号（序号接在最大值之后，绝不与既有冲突）
    resolved_ids = [eid or allocator.allocate() for eid in resolved_ids]

    evidences: list[Evidence] = [
        _evidence_from_hard_result(item, talent_data, eid, cfg, profile)
        for item, eid in zip(hard_results, resolved_ids)
    ]

    evidences.extend(_preferred_skill_evidences(profile, talent_data, allocator, cfg))
    evidences.extend(_responsibility_evidences(profile, talent_data, allocator, cfg))
    evidences.extend(
        _dimension_evidences(
            talent_data,
            {str(k): float(v) for k, v in (dimension_scores or {}).items() if _is_number(v)},
            allocator,
            cfg,
        )
    )
    evidences.extend(_missing_field_evidences(talent_data, evidences, allocator, cfg))

    _assert_unique(evidences)

    # 回填 evidence_id（不改入参对象）：让下游拿到「判定结果 -> 证据」的直连关系
    updated_results = [
        item if item.evidence_id == eid else item.model_copy(update={"evidence_id": eid})
        for item, eid in zip(hard_results, resolved_ids)
    ]

    pack = MatchEvidencePack(
        position_id=int(pid),
        talent_id=int(tid),
        requirement_version=max(1, int(requirement_version)),
        job_profile_summary=profile,
        hard_results=updated_results,
        dimension_scores={str(k): float(v) for k, v in (dimension_scores or {}).items() if _is_number(v)},
        data_completeness=compute_data_completeness(talent),
        evidences=evidences,
    )

    logger.info(
        "evidence_pack.built position_id=%s talent_id=%s evidences=%s missing=%s",
        pack.position_id,
        pack.talent_id,
        len(pack.evidences),
        sum(1 for item in pack.evidences if item.evidence_type == "missing"),
    )
    return pack


def _is_number(value: Any) -> bool:
    """判断是否可转 float（维度分只接受数值，脏数据直接跳过而不是中断整批）。"""
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _assert_unique(evidences: list[Evidence]) -> None:
    """自检：证据 ID 必须唯一（重复会导致校验器把结论挂到错误证据上）。"""
    seen: set[str] = set()
    for item in evidences:
        if item.evidence_id in seen:
            raise ParamError(f"证据 ID 重复：{item.evidence_id}（证据构建逻辑异常，请检查分配器）")
        seen.add(item.evidence_id)
