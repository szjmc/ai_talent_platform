"""
功能：检查解释是否有证据支持并清除可识别的越界结论。
输入：MatchExplanation、MatchEvidencePack、校验配置。
输出：清洗解释、剔除记录、待核实事项和证据包问题。
流程：证据自审 → 引用检查 → 缺失/术语/数值/规则检查 → 清洗输出。
边界：确定性检查不是全面语义证明；同义改写等仍需人工抽检。

标签：[Agent组] [P1]（证据包自检与一致性校验）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from app.core.exceptions import ParamError
from app.services.explanation_policy import unsupported_rule
from app.schemas.matching import (
    Evidence,
    ExplanationClaim,
    HardConditionResult,
    HardStatus,
    MatchEvidencePack,
    MatchExplanation,
    RejectedClaim,
)

logger = logging.getLogger(__name__)

# 证据 ID 格式：EVID-{position_id}-{talent_id}-{seq}（与 matching_rules.build_evidence_id 一致）
_EVID_RE = re.compile(r"^EVID-(\d+)-(\d+)-(\d+)$")

# *kwq* 新增：解释文本中的「年限」表述提取（"5 年" / "3.5 years"）。
# 用途：解释里写的年限不得高于档案真实年限，这是少数能被机器精确核对的事实。
_YEAR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:年|years?)")

# *kwq* 新增：解释文本中的能力术语（黑名单，命中且证据不支持即判越界）。
# 为什么用黑名单而不是白名单：解释是自然语言，白名单会把所有正常表述都判违规；
# 黑名单只盯「一旦出现就必然需要硬证据」的能力断言，误杀率低、可解释性强。
DEFAULT_BLOCKING_TERMS: tuple[str, ...] = (
    "高并发",
    "高性能",
    "分布式",
    "微服务",
    "架构设计",
    "架构能力",
    "高可用",
    "云原生",
    "容器化",
    "技术选型",
    "团队管理",
    "项目管理",
    "带团队",
    "从0到1",
    "0到1",
    "亿级",
    "海量数据",
    "大数据量",
    "系统重构",
    "性能调优",
    "全栈",
    "数据治理",
    "领域驱动",
    "技术攻坚",
    "平台化",
    "中台",
)

# *kwq* 新增：表述强度词（灰名单，命中不判违规，只提示人工核实）。
# 原因：「精通」「资深」属于程度副词，靠证据无法证实，但也谈不上编造；
#      直接剔除会让解释失去可读性，所以降级为待核实提示。
DEFAULT_SOFTENING_TERMS: tuple[str, ...] = (
    "精通",
    "深度掌握",
    "深入了解",
    "资深",
    "专家级",
    "卓越",
    "极强",
    "极为丰富",
    "全面掌握",
    "深刻理解",
)

# *kwq* 新增：硬条件项 -> 人才档案字段（与 evidence_builder 的映射保持一致，用于矛盾检测）。
_ITEM_TO_FIELD: dict[str, str] = {
    "min_education": "education",
    "min_work_years": "work_years",
    "required_skills": "skill_tags",
}

# *kwq* 新增：解释分区名。
# 用途：同一句结论放在 highlights 还是 gaps，校验口径不同
#      （highlights 是正面断言，必须最严格；gaps/risks 允许描述「无法判断」）。
_Section = Literal["highlights", "gaps", "risks"]

# *kwq* 新增：结论文本 -> 硬条件项的关键词映射（用于「与硬条件冲突」检查）。
_CONDITION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "min_education": ("学历", "本科", "硕士", "博士", "大专", "专科", "研究生", "学士", "统招"),
    "min_work_years": ("工作年限", "从业年限", "年工作经验", "年经验", "经验年限"),
    "required_skills": ("必备技能", "核心技能", "技能要求"),
}


# ------------------------------------------------------------------ 配置


# *kwq* 新增：校验口径配置。
# 设计原因：「哪些词算高危能力术语」本质是业务口径，不能写死在判断逻辑里；
#          做成可注入配置后，业务调整只改配置，不改代码（也便于 MATCH-013 做用例集）。
@dataclass(frozen=True)
class VerifyConfig:
    """证据校验器配置（默认值不含业务判断，只控制拦截强度）。"""

    blocking_terms: tuple[str, ...] = DEFAULT_BLOCKING_TERMS
    softening_terms: tuple[str, ...] = DEFAULT_SOFTENING_TERMS
    max_pending: int = 50                  # pending_verification 上限，防止无意义刷屏
    max_rejected: int = 50                 # rejected_claims 上限（审计留证，不做无限增长）
    rewrite_summary_on_violation: bool = True  # 总结违规时改写为事实型总结（否则留空并待核实）
    years_tolerance: float = 0.01          # 年限比较容差（避免浮点误差误判）


DEFAULT_VERIFY_CONFIG = VerifyConfig()


# ------------------------------------------------------------------ 结果结构


# *kwq* 新增：单条结论的校验结论（内部使用，不对外暴露）。
@dataclass(frozen=True)
class _Outcome:
    """单条结论的处理结果。

    action:
        pass    保留在解释对应分区
        reject  移出解释并写入 rejected_claims
        pending 移出解释并写入 pending_verification
    """

    action: Literal["pass", "reject", "pending"]
    rejected: RejectedClaim | None = None
    pending: str | None = None


# *kwq* 新增：校验结果（对外）。
# 用途：同时给出「清洗后的解释」和「三份清单」，让上层既能直接落库，也能做质量统计。
@dataclass
class VerificationResult:
    """证据校验结果。"""

    explanation: MatchExplanation                 # 清洗后的解释（已剔除违规结论）
    passed: list[ExplanationClaim]                # 通过校验的结论（三个分区合并）
    rejected: list[RejectedClaim]                 # 被拒绝的结论
    pending: list[str]                            # 待人工核实事项
    pack_issues: list[str]                        # 证据包自身的质量问题（非解释的问题）

    def as_tuple(self) -> tuple[MatchExplanation, list[RejectedClaim], list[str]]:
        """按 V1.0 方案约定的 (cleaned, rejected, needs_review) 三元组返回。"""
        return self.explanation, list(self.rejected), list(self.pending)

    @property
    def pass_rate(self) -> float:
        """结论通过率（审计指标：解释质量的可量化代理指标）。"""
        total = len(self.passed) + len(self.rejected)
        return 1.0 if total == 0 else round(len(self.passed) / total, 4)


# ------------------------------------------------------------------ 基础工具


def _is_blank(value: Any) -> bool:
    """判断字段是否「等同于没有值」（与 evidence_builder 口径一致）。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _value_text(value: Any) -> str:
    """把证据原始值压成可检索文本（用于术语白名单比对）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    return str(value)


def _dedup_texts(items: Iterable[str], limit: int | None = None) -> list[str]:
    """文本去重（忽略大小写与首尾空白），保持原顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _clip(text: Any, limit: int) -> str:
    """截断到指定长度（Schema 对 RejectedClaim.text 有 500 字符上限）。"""
    value = str(text or "").strip()
    return value[:limit] if len(value) > limit else value


def _evidence_terms(evidence: Evidence) -> list[str]:
    """某条证据允许被解释成的术语集合。

    口径（关键）：
        - missing 类证据**一律不授权任何能力术语** —— 档案里没有该字段，
          就不存在「可以据此说他有某能力」的可能。即使构建器误填了 capacity_terms，这里也不采信。
        - fact / derived 类证据：capacity_terms + source_value 原文，两者都算「证据自己说过的话」。
    """
    if evidence.evidence_type == "missing":
        return []
    terms: list[str] = list(evidence.capacity_terms or [])
    raw = _value_text(evidence.source_value)
    if raw:
        terms.append(raw)
    return [term for term in (str(item).strip() for item in terms) if term]


def _allowed_text(evidences: Sequence[Evidence]) -> str:
    """把一组证据的授权术语拼成一个小写检索串。"""
    parts: list[str] = []
    for evidence in evidences:
        parts.extend(_evidence_terms(evidence))
    return " ".join(parts).lower()


def _hits(text: str, terms: Iterable[str]) -> list[str]:
    """返回 text 中出现的术语（大小写不敏感）。"""
    lowered = text.lower()
    return [term for term in terms if term and term.lower() in lowered]


# ------------------------------------------------------------------ A. 证据包自审


def _belongs(evidence: Evidence, pack: MatchEvidencePack) -> bool:
    """证据 ID 是否属于当前 (岗位, 人才) —— 防止跨候选人串用证据。"""
    match = _EVID_RE.match(str(evidence.evidence_id or ""))
    if not match:
        return False
    position_id, talent_id, _seq = match.groups()
    return int(position_id) == int(pack.position_id) and int(talent_id) == int(pack.talent_id)


# *kwq* 新增：证据包自审。
# 用途：先判断「送进来的证据本身可不可信」，而不是默认它有罪或无罪。
#      发现的问题既写日志也回传给上层，避免「解释被大量剔除却查不出原因」。
def audit_pack(pack: MatchEvidencePack) -> list[str]:
    """审计证据包内部一致性，返回问题描述列表（空列表=通过）。

    检查项：
        1. evidence_id 是否重复；
        2. evidence_id 前缀是否属于当前 (岗位, 人才)；
        3. fact 类证据是否真的带原始值（无值的 fact 是假事实）；
        4. missing 类证据是否被授予了能力术语（等于给「无数据」发许可证）；
        5. hard_results.evidence_id 是否都能在 evidences 中找到（判定与证据的链接是否断裂）。
    """
    issues: list[str] = []
    seen: set[str] = set()

    for evidence in pack.evidences:
        eid = str(evidence.evidence_id or "")
        if eid in seen:
            issues.append(f"证据 ID 重复：{eid}")
        seen.add(eid)

        if not _belongs(evidence, pack):
            issues.append(f"证据归属不符：{eid} 不属于岗位 {pack.position_id} / 人才 {pack.talent_id}")

        if evidence.evidence_type == "fact" and _is_blank(evidence.source_value):
            issues.append(f"fact 证据缺少原始值：{eid}（来源 {evidence.source_table}.{evidence.source_field}）")

        if evidence.evidence_type == "missing" and evidence.capacity_terms:
            issues.append(
                f"missing 证据带有能力术语：{eid} -> {evidence.capacity_terms[:3]}（校验时将被忽略，建议构建器置空）"
            )

    for result in pack.hard_results:
        if result.evidence_id and result.evidence_id not in seen:
            issues.append(f"硬条件 {result.item} 指向不存在的证据：{result.evidence_id}")

    # *kwq* 新增（MATCH-005 复核补充）：判定与证据自相矛盾检测。
    # 原因：MATCH-004 复核时复现出一个真实问题 —— 传入 ORM 对象（无 to_dict）时，
    #      四态判定能读到属性（判 PASS），但证据构建读不到字段值（同时产出 missing 证据），
    #      同一个包里既有「学历满足：硕士」又有「档案缺少最高学历」，解释层必然自相矛盾。
    #      这类矛盾不能静默通过，必须在自审阶段暴露出来。
    missing_fields = {item.source_field for item in pack.evidences if item.evidence_type == "missing"}
    for result in pack.hard_results:
        field = _ITEM_TO_FIELD.get(result.item)
        if not field:
            continue
        if result.status in (HardStatus.PASS, HardStatus.FAIL) and field in missing_fields:
            issues.append(
                f"证据自相矛盾：{result.item} 判定为 {result.status.value}，"
                f"但证据包同时声明字段 {field} 缺失（判定与证据数据源不一致）"
            )

    if issues:
        logger.warning(
            "evidence_pack.audit position_id=%s talent_id=%s issues=%s",
            pack.position_id,
            pack.talent_id,
            len(issues),
        )
    return issues


def _build_index(pack: MatchEvidencePack) -> tuple[dict[str, Evidence], list[str]]:
    """构造「可用证据」索引：只把通过自审的证据放进索引。

    为什么要过滤：校验器的全部信任都建立在这张索引上。
        若把归属不符 / 无原始值的证据也放进去，等于承认它可以支撑结论，
        那前面所有检查都白做。
    """
    index: dict[str, Evidence] = {}
    issues: list[str] = []
    for evidence in pack.evidences:
        eid = str(evidence.evidence_id or "")
        if not _belongs(evidence, pack):
            issues.append(f"证据不可用（归属不符）：{eid}")
            continue
        if evidence.evidence_type == "fact" and _is_blank(evidence.source_value):
            issues.append(f"证据不可用（fact 无原始值）：{eid}")
            continue
        index[eid] = evidence
    return index, issues


# ------------------------------------------------------------------ D. 事实一致性


def _claimed_years(text: str) -> float | None:
    """从结论文本中提取「年限」数字（取最大值，避免同时出现 3 年和 5 年时判偏松）。"""
    values: list[float] = []
    for match in _YEAR_RE.finditer(text or ""):
        try:
            values.append(float(match.group(1)))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _talent_years(pack: MatchEvidencePack) -> float | None:
    """取人才真实工作年限：优先硬条件 actual，其次 work_years 证据的原始值。"""
    for result in pack.hard_results:
        if result.item == "min_work_years" and result.actual is not None:
            try:
                return float(result.actual)
            except (TypeError, ValueError):
                break
    for evidence in pack.evidences:
        if evidence.source_field == "work_years" and evidence.evidence_type != "missing":
            try:
                return float(evidence.source_value)
            except (TypeError, ValueError):
                continue
    return None


def _claim_conditions(text: str) -> set[str]:
    """判断一句结论涉及哪些硬条件项（用于与四态判定结果比对）。"""
    conditions: set[str] = set()
    for condition, keywords in _CONDITION_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            conditions.add(condition)
    # 年限还有「5 年后端经验」这类写法，关键词表覆盖不到，用数字+经验兜底
    if "经验" in text and _claimed_years(text) is not None:
        conditions.add("min_work_years")
    return conditions


def _hard_map(pack: MatchEvidencePack) -> dict[str, HardConditionResult]:
    """硬条件项 -> 判定结果（同名多条件时保留第一条，避免相互覆盖）。"""
    mapping: dict[str, HardConditionResult] = {}
    for result in pack.hard_results:
        mapping.setdefault(result.item, result)
    return mapping


# ------------------------------------------------------------------ B/C/D. 单条结论校验


def _reject(text: str, reason: str, detail: str | None = None) -> _Outcome:
    """构造一条「拒绝」结果。"""
    return _Outcome(
        action="reject",
        rejected=RejectedClaim(text=_clip(text, 500), reason=reason, detail=_clip(detail, 300) or None),  # type: ignore[arg-type]
    )


def _verify_claim(
    claim: ExplanationClaim,
    section: _Section,
    index: dict[str, Evidence],
    hard: dict[str, HardConditionResult],
    pack: MatchEvidencePack,
    config: VerifyConfig,
) -> _Outcome:
    """校验单条解释结论（顺序：引用 -> 缺失滥用 -> 术语 -> 数值 -> 硬条件冲突）。"""
    text = str(claim.text or "").strip()
    if not text:
        return _reject("（空结论）", "no_evidence", "结论文本为空")

    # 步骤：拒绝模型自创业务阈值，不能用真实证据ID给虚构规则背书。
    if unsupported_rule(text):
        return _reject(text, "term_out_of_scope", "输入契约未授权模型制定业务阈值或录用概率")

    # ---- B1. 引用存在性
    ids = [str(item).strip() for item in (claim.evidence_ids or []) if str(item).strip()]
    if not ids:
        return _reject(text, "no_evidence", "未引用任何证据 ID")

    unknown = [eid for eid in ids if eid not in index]
    if unknown:
        return _reject(text, "evidence_not_found", f"证据不存在或不可用：{'、'.join(unknown[:5])}")

    cited = [index[eid] for eid in ids]

    # ---- C1. missing 证据不得用于证明能力
    # 说明：gaps / risks 里引用 missing 证据是合法的（那正是在描述「无法判断」），
    #      但 highlights 是正面断言，全部依据只有 missing 时必须剔除。
    if section == "highlights" and all(item.evidence_type == "missing" for item in cited):
        return _reject(
            text,
            "missing_evidence_misuse",
            "仅引用缺失类证据（档案无该数据），不能据此得出人才的正面能力结论",
        )

    # ---- C2. 能力术语越界
    allowed = _allowed_text(cited)
    out_of_scope = [term for term in _hits(text, config.blocking_terms) if term.lower() not in allowed]
    if out_of_scope:
        return _reject(
            text,
            "term_out_of_scope",
            f"越界能力术语：{'、'.join(out_of_scope[:5])}；引用证据不足以支撑该表述",
        )

    # ---- D1. 年限数值不得高于档案
    claimed_years = _claimed_years(text)
    actual_years = _talent_years(pack)
    if (
        claimed_years is not None
        and actual_years is not None
        and claimed_years > actual_years + config.years_tolerance
    ):
        return _reject(
            text,
            "conflict_with_hard_result",
            f"解释称 {claimed_years:g} 年经验，档案记录为 {actual_years:g} 年",
        )

    # ---- D2. 正面结论不得与 FAIL / UNKNOWN 冲突，NEEDS_REVIEW 下沉人工
    if section == "highlights":
        for condition in _claim_conditions(text):
            result = hard.get(condition)
            if result is None:
                continue
            if result.status == HardStatus.FAIL:
                return _reject(
                    text,
                    "conflict_with_hard_result",
                    f"{condition} 判定为 FAIL：{result.reason}",
                )
            if result.status == HardStatus.UNKNOWN:
                return _reject(
                    text,
                    "conflict_with_hard_result",
                    f"{condition} 缺少档案数据（UNKNOWN），不能据此下正面结论",
                )
            if result.status == HardStatus.NEEDS_REVIEW:
                return _Outcome(action="pending", pending=f"待人工核实（条件无法机读）：{_clip(text, 80)}")

    # ---- 灰名单：表述强度偏高，保留但提示核实
    soft = _hits(text, config.softening_terms)
    if soft:
        return _Outcome(
            action="pass",
            pending=f"表述强度待核实（{'、'.join(soft[:3])}）：{_clip(text, 80)}",
        )

    # ---- 推断类结论保留，但必须人工确认（Schema 的 claim_type 契约）
    if claim.claim_type == "inference":
        return _Outcome(action="pass", pending=f"推断类结论需人工确认：{_clip(text, 80)}")

    return _Outcome(action="pass")


# ------------------------------------------------------------------ 总结校验


def _factual_summary(pack: MatchEvidencePack) -> str:
    """生成事实型总结（仅由四态判定计数与完整度拼装，不含任何能力判断）。

    为什么需要：模型写的 summary 是自由文本、不挂 evidence_id，
        一旦出现越界术语就无法「剔除某一句」，只能整体替换。
        替换品必须是纯事实，否则等于用编造掩盖编造。
    """
    counts = {status: 0 for status in HardStatus}
    for result in pack.hard_results:
        counts[result.status] += 1
    total = sum(counts.values())
    if total == 0:
        return f"本次匹配未产出硬性条件判定结果；档案完整度 {pack.data_completeness:.0%}。"
    return (
        f"硬性条件共 {total} 项：通过 {counts[HardStatus.PASS]} 项、"
        f"不通过 {counts[HardStatus.FAIL]} 项、信息缺失 {counts[HardStatus.UNKNOWN]} 项、"
        f"待人工判断 {counts[HardStatus.NEEDS_REVIEW]} 项；"
        f"档案完整度 {pack.data_completeness:.0%}。"
    )


def _verify_summary(
    summary: str,
    admissible: Sequence[Evidence],
    pack: MatchEvidencePack,
    config: VerifyConfig,
) -> tuple[str, RejectedClaim | None, list[str]]:
    """校验总结文本：只做「术语越界」与「年限夸大」两类可机读检查。

    局限（必须如实告知）：
        summary 不带 evidence_ids，无法做引用级校验；极性判断（「不符合学历要求」是正确表述）
        需要语义分析，本期不做，仅拦截明确的编造痕迹。该限制记入风险清单，不隐藏。
    """
    text = str(summary or "").strip()
    if not text:
        return _factual_summary(pack), None, []

    if unsupported_rule(text):
        rejected = RejectedClaim(text=_clip(text, 500), reason="term_out_of_scope",
                                 detail="总结新增了输入契约未授权的业务阈值或录用概率")
        return _factual_summary(pack), rejected, ["原总结包含未授权业务规则，已替换为事实描述"]

    allowed = _allowed_text(admissible)
    out_of_scope = [term for term in _hits(text, config.blocking_terms) if term.lower() not in allowed]
    claimed_years = _claimed_years(text)
    actual_years = _talent_years(pack)
    over_years = (
        claimed_years is not None
        and actual_years is not None
        and claimed_years > actual_years + config.years_tolerance
    )

    if not out_of_scope and not over_years:
        return _clip(text, 1000), None, []

    detail = (
        f"总结包含越界能力术语：{'、'.join(out_of_scope[:5])}"
        if out_of_scope
        else f"总结称 {claimed_years:g} 年经验，档案为 {actual_years:g} 年"
    )
    rejected = RejectedClaim(
        text=_clip(text, 500),
        reason="term_out_of_scope" if out_of_scope else "conflict_with_hard_result",
        detail=_clip(detail, 300),
    )
    replacement = _factual_summary(pack) if config.rewrite_summary_on_violation else "（原总结未通过证据校验，已隐藏）"
    return replacement, rejected, [f"原总结因「{detail}」被替换为事实型描述"]


# ------------------------------------------------------------------ 对外主函数


# *kwq* 新增（MATCH-005 主入口）：校验匹配解释。
# 调用方：MATCH-007 解释编排（无论 Dify / LLM / 规则兜底，产物都必须过这一关）、MATCH-008 匹配服务。
# 关键约束：本函数是纯函数（无 IO、不调模型），结果只依赖入参，可被单测完全覆盖。
def verify_explanation(
    explanation: MatchExplanation | dict[str, Any],
    pack: MatchEvidencePack,
    config: VerifyConfig | None = None,
) -> VerificationResult:
    """校验解释中的每一条结论，返回清洗后的解释与三份清单。

    参数：
        explanation: Dify / LLM / 规则兜底产出的解释（模型或 dict，dict 会按契约校验）
        pack:        MATCH-004 产出的证据包（唯一事实来源）
        config:      校验口径（术语表、上限、是否改写总结）

    返回：
        VerificationResult（explanation / passed / rejected / pending / pack_issues）
    """
    cfg = config or DEFAULT_VERIFY_CONFIG

    if isinstance(explanation, dict):
        explanation = MatchExplanation.model_validate(explanation)
    if not isinstance(explanation, MatchExplanation):
        raise ParamError("verify_explanation 需要 MatchExplanation 或可校验为它的 dict")
    if not isinstance(pack, MatchEvidencePack):
        raise ParamError("verify_explanation 需要 MatchEvidencePack（证据缺失时不允许校验解释）")

    pack_issues = audit_pack(pack)
    index, unusable = _build_index(pack)
    pack_issues.extend(unusable)
    hard = _hard_map(pack)

    passed: list[ExplanationClaim] = []
    rejected: list[RejectedClaim] = list(explanation.rejected_claims)
    pending: list[str] = list(explanation.pending_verification)

    kept: dict[str, list[ExplanationClaim]] = {"highlights": [], "gaps": [], "risks": []}
    sections: list[tuple[_Section, list[ExplanationClaim]]] = [
        ("highlights", explanation.highlights),
        ("gaps", explanation.gaps),
        ("risks", explanation.risks),
    ]

    for section, claims in sections:
        for claim in claims:
            outcome = _verify_claim(claim, section, index, hard, pack, cfg)
            if outcome.action == "pass":
                kept[section].append(claim)
                passed.append(claim)
                if outcome.pending:
                    pending.append(outcome.pending)
                continue
            if outcome.action == "pending":
                if outcome.pending:
                    pending.append(outcome.pending)
                continue
            if outcome.rejected is not None:
                rejected.append(outcome.rejected)

    summary, summary_rejected, summary_pending = _verify_summary(
        explanation.summary, [item for item in pack.evidences if item.evidence_id in index], pack, cfg
    )
    if summary_rejected is not None:
        rejected.append(summary_rejected)
    pending.extend(summary_pending)

    # 待核实与面试建议也是输出，不能绕过业务规则校验。
    safe_suggestions = []
    safe_pending = []
    for value, target in [(v, safe_suggestions) for v in explanation.interview_suggestions] + [(v, safe_pending) for v in pending]:
        if unsupported_rule(value):
            rejected.append(RejectedClaim(text=_clip(value, 500), reason="term_out_of_scope", detail="附加文本包含未授权业务规则"))
        else:
            target.append(value)

    cleaned = MatchExplanation(
        summary=summary,
        highlights=kept["highlights"],
        gaps=kept["gaps"],
        risks=kept["risks"],
        interview_suggestions=_dedup_texts(safe_suggestions, 10),
        pending_verification=_dedup_texts(safe_pending, cfg.max_pending),
        rejected_claims=rejected[: cfg.max_rejected],
        source=explanation.source,
        prompt_version=explanation.prompt_version,
    )

    result = VerificationResult(
        explanation=cleaned,
        passed=passed,
        rejected=list(cleaned.rejected_claims),
        pending=list(cleaned.pending_verification),
        pack_issues=_dedup_texts(pack_issues),
    )
    logger.info(
        "explanation.verified position_id=%s talent_id=%s passed=%s rejected=%s pending=%s source=%s",
        pack.position_id,
        pack.talent_id,
        len(passed),
        len(result.rejected),
        len(result.pending),
        explanation.source,
    )
    return result


# *kwq* 新增：按 V1.0 方案签名提供的薄封装。
# 用途：方案约定 verify(explanation, pack) -> (cleaned, rejected, needs_review)，
#      保留该签名便于 MATCH-007 按契约调用；实现只有一份，不做重复逻辑。
def verify(
    explanation: MatchExplanation | dict[str, Any],
    pack: MatchEvidencePack,
    config: VerifyConfig | None = None,
) -> tuple[MatchExplanation, list[RejectedClaim], list[str]]:
    """校验解释并返回 (清洗后的解释, 被拒绝结论, 待人工核实清单)。"""
    return verify_explanation(explanation, pack, config).as_tuple()


__all__ = [
    "DEFAULT_BLOCKING_TERMS",
    "DEFAULT_SOFTENING_TERMS",
    "DEFAULT_VERIFY_CONFIG",
    "VerificationResult",
    "VerifyConfig",
    "audit_pack",
    "verify",
    "verify_explanation",
]
