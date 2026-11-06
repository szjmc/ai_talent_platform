"""
模块：app/schemas/matching.py
职责：人岗匹配相关请求 / 响应结构。

标签：[Agent组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. MatchRequest        position_id、talent_ids(可空，空则全库检索)、top_k、
                       strategy(hard/soft/hybrid)、min_score
2. MatchItem           talent_id、talent_name、score(0-100)、
                       dimension_scores(技能/经验/学历/素质)、
                       highlights: list[str]、gaps: list[str]、suggestion
3. MatchResponse       position_id、position_title、matched_at、items: list[MatchItem]
4. MatchExplainRequest position_id、talent_id
5. MatchExplainResponse 匹配维度明细、评分依据、解释文本（AI 生成）
实现要点：
- 硬性条件（学历/年限/证书）先过滤，软性能力用向量相似度加权；
- 匹配过程与结果写入 matching_records 表，支持历史回溯；
- 解释文本由 AI 生成但必须引用真实字段值，防止幻觉（prompt 中约束）。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema
# *kwq* 新增（REVIEW-002）：复用复核状态 Literal，避免两处各写一套导致漂移。
# 依赖方向是单向的（matching → matching_review），不会形成循环导入。
from app.schemas.matching_review import ReviewStatusLiteral
from app.schemas.position import JobProfileParsed

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。

# ============================================================================
# *kwq* 新增（MATCH-001）：匹配链路数据结构契约（四态 / 证据 / 解释 / 跑批响应）。
# 说明：本轮只实现 V1.0 方案点名的 9 个核心模型，以及 MatchRunResponse 必需的
#      3 个从属模型（MatchItem / MatchStats / ExcludedItem），不实现
#      MatchRequest、MatchExplainRequest 等接口入参（属 MATCH-010 接口任务）。
# 影响范围：纯新增，未修改文件内其他内容，不影响既有调用方。
# ============================================================================


# *kwq* 新增：硬性条件四态枚举。
# 用途：区分「满足 / 不满足 / 信息缺失 / 需人工判断」，替代原来只有布尔两态的判断。
# 取值大写：以《第一阶段正式开发任务书》给定示例为准（V1.0 方案文档中为小写，已按任务书统一）。
# 关键约束：UNKNOWN 不得被当成 PASS，也不得直接淘汰（见 app/services/matching_rules.py）。
class HardStatus(str, Enum):
    """硬性条件判定状态。"""

    PASS = "PASS"                # 有明确证据证明满足
    FAIL = "FAIL"                # 有充分证据证明不满足
    UNKNOWN = "UNKNOWN"          # 人才档案缺少必要信息
    NEEDS_REVIEW = "NEEDS_REVIEW"  # 条件模糊，需人工判断


# *kwq* 新增：匹配证据模型（防编造的核心）。
# 用途：每条证据携带结构化 ID + 来源字段 + 原始值，供 LLM 引用、供 Python 校验。
# 设计原因：只靠「字符串包含」无法验证事实，必须让解释引用 evidence_id，
#          再由 Python 校验 ID 是否存在、能力术语是否越界。
class Evidence(BaseSchema):
    """单条匹配证据（可追溯到具体表字段与原始值）。"""

    evidence_id: str = Field(
        ...,
        pattern=r"^EVID-\d+-\d+-\d+$",
        description="证据 ID，格式 EVID-{position_id}-{talent_id}-{seq}",
    )
    claim: str = Field(..., min_length=1, max_length=200, description="该证据支撑的结论（短句）")
    source_table: str = Field(..., min_length=1, max_length=64, description="来源表，如 talents")
    source_field: str = Field(..., min_length=1, max_length=64, description="来源字段，如 skill_tags")
    source_value: Any = Field(..., description="原始值，原样保留供校验比对")
    requirement_ref: str = Field(..., min_length=1, max_length=64, description="对应的岗位要求字段名")
    # *kwq* 新增（MATCH-004）：证据性质，区分「档案事实 / 代码推导 / 依据缺失」。
    # 修改原因：MATCH-004 要求「区分事实证据与推断」，且缺失字段必须显式标记为 missing，
    #          否则解释层无法判断某条证据能不能用来下能力结论（缺失值最容易被打扮成优势）。
    # 影响范围：纯新增可选字段，默认 "fact"，既有构造方式无需改动；
    #          当前仓库内 Evidence 尚无其他生产者（本文件为本轮唯一使用方）。
    evidence_type: Literal["fact", "derived", "missing"] = Field(
        "fact", description="证据性质：fact 档案原值 / derived 代码推导 / missing 依据缺失"
    )
    capacity_terms: list[str] = Field(
        default_factory=list,
        description="该证据允许被解释成的能力术语白名单（越界检测用）",
    )


# *kwq* 新增：单项硬条件判定结果。
# 用途：替代原来「reasons + continue 丢弃」的做法，让每个条件都有状态、原因与证据。
# 设计原因：reason 设为必填 —— 任务书要求「每项判断必须提供原因」，
#          没有原因就无法向 HR 解释「为什么这个人没出现」。
class HardConditionResult(BaseSchema):
    """单项硬条件四态判定结果。"""

    item: str = Field(..., min_length=1, max_length=64, description="条件名，如 min_education")
    required: Any = Field(None, description="岗位要求值（原值）")
    actual: Any = Field(None, description="人才实际值（原值，缺失时为 None）")
    status: HardStatus = Field(..., description="四态判定结果")
    reason: str = Field(..., min_length=1, max_length=200, description="判定原因（必填，供审计与展示）")
    evidence_id: str | None = Field(
        None, pattern=r"^EVID-\d+-\d+-\d+$", description="支撑该判定的证据 ID（UNKNOWN 时可为 None）"
    )


# *kwq* 新增：证据包（送入 Dify 的唯一输入）。
# 用途：把「岗位要求 + 四态结果 + 维度分 + 结构化证据」打包，替代把简历全文丢给大模型。
# 设计原因：切断模型自由发挥的原料 —— 输入里没有的东西，模型编不出来；
#          即便编出来，也会在 verify 阶段因 evidence_id 不存在被剔除。
class MatchEvidencePack(BaseSchema):
    """匹配证据包（Dify 解释工作流的入参）。"""

    position_id: int = Field(..., gt=0, description="岗位 ID；示例1须先在当前库确认存在", examples=[1])
    talent_id: int = Field(..., gt=0, description="人才 ID")
    requirement_version: int = Field(1, ge=1, description="本次匹配使用的岗位画像版本")
    job_profile_summary: JobProfileParsed = Field(..., description="岗位画像（结构化要求）")
    hard_results: list[HardConditionResult] = Field(
        default_factory=list, description="四态判定明细"
    )
    dimension_scores: dict[str, float] = Field(
        default_factory=dict, description="维度分：skill/experience/education/quality"
    )
    data_completeness: float = Field(
        0.0, ge=0, le=1, description="档案完整度，用于提示「该结论建立在缺失数据上」"
    )
    evidences: list[Evidence] = Field(default_factory=list, description="结构化证据集合")


# *kwq* 新增：解释结论条目。
# 用途：约束 LLM 输出的每一条结论都必须挂证据。
# 设计原因：evidence_ids 设 min_length=1 —— 在 Schema 层就拦住「无证据结论」，
#          比事后用关键词匹配补救更可靠。
class ExplanationClaim(BaseSchema):
    """解释中的单条结论（必须引用至少 1 条证据）。"""

    text: str = Field(..., min_length=1, max_length=500, description="结论文本")
    evidence_ids: list[str] = Field(
        ..., min_length=1, description="引用的证据 ID 列表（至少 1 条，不允许空）"
    )
    claim_type: Literal["fact", "inference"] = Field(
        "fact", description="事实陈述 / 推断（推断类需人工复核）"
    )


# *kwq* 新增：被剔除的无依据结论。
# 用途：记录校验器剔除了什么、为什么剔除，供 prompt 迭代与审计。
# 设计原因：不能静默删除 —— 剔除记录是「解释质量」的唯一可量化指标。
class RejectedClaim(BaseSchema):
    """证据校验未通过而被剔除的结论。"""

    text: str = Field(..., min_length=1, max_length=500, description="被剔除的原文")
    # *kwq* 修改（MATCH-005）：reason 枚举新增 "missing_evidence_misuse"。
    # 修改原因：MATCH-004 产出的 missing 类证据（档案无该字段）只能用于说明「信息缺失」，
    #          一旦被用来证明人才具备某种能力，就是另一类错误，与「无证据」「越界」性质不同。
    #          若不单列，审计时无法区分「模型编了证据 ID」和「模型拿缺失当具备」，prompt 迭代会失焦。
    # 影响范围：Literal 取值的向后兼容扩展（新增合法值），既有 4 个取值的语义与写入方均不变；
    #          当前仓库内 RejectedClaim 仅有证据校验器一个生产者，无其他模块受影响。
    reason: Literal[
        "no_evidence",
        "evidence_not_found",
        "term_out_of_scope",
        "conflict_with_hard_result",
        "missing_evidence_misuse",
    ] = Field(..., description="剔除原因")
    detail: str | None = Field(None, max_length=300, description="补充说明（如越界的能力术语）")


# *kwq* 新增：匹配解释模型（LLM 输出 + 校验后的最终结构）。
# 用途：统一 Dify / LLM 降级 / 规则兜底三种产物的结构，前端不必区分来源。
# 设计原因：source 字段必填 —— 让「AI 解释失败降级」对上层可见，不允许伪装成 AI 产物。
class MatchExplanation(BaseSchema):
    """匹配解释（含来源标识与剔除记录）。"""

    summary: str = Field(..., min_length=1, max_length=1000, description="综合匹配结论")
    highlights: list[ExplanationClaim] = Field(default_factory=list, description="核心优势")
    gaps: list[ExplanationClaim] = Field(default_factory=list, description="能力差距")
    risks: list[ExplanationClaim] = Field(default_factory=list, description="潜在风险")
    interview_suggestions: list[str] = Field(default_factory=list, description="建议面试问题")
    pending_verification: list[str] = Field(
        default_factory=list, description="待核实事项（含 UNKNOWN 项与被剔除项）"
    )
    rejected_claims: list[RejectedClaim] = Field(
        default_factory=list, description="校验器剔除的无依据结论"
    )
    source: Literal["dify", "llm_fallback", "rule_fallback"] = Field(
        "rule_fallback", description="解释来源：Dify / LLM 降级 / 规则兜底"
    )
    prompt_version: str = Field("v1", max_length=20, description="生成该解释的提示词版本")


# *kwq* 新增（从属模型）：单个候选人匹配条目。
# 用途：MatchRunResponse.items 的元素，兼顾「分数 + 四态明细 + 事实技能 + 解释性文字」。
# 设计原因：拆出 matched_skills 与 highlights —— 原 MatchStrategy.shape() 把技能名塞进
#          highlights，而接口文档示例 highlights 是自然语言句子，两者语义冲突，必须分离。
# ============================================================================
# *kwq* 新增（GAP-001）：结构化能力差距矩阵。
# 设计原因：原 MatchItem.gaps 只是解释层拼接的文本（f"{item.item}：{item.reason}"），
#          无法回答「差几项、哪项致命、差在哪个维度」。
# 红线约束：
#   1) status 直接复用四态口径（PASS/FAIL/UNKNOWN/NEEDS_REVIEW），不新造评分体系；
#   2) actual 只取档案原值，取不到写 None 并标 missing_data，严禁推测能力等级；
#   3) evidence_ids 引用既有证据链 ID，不新增证据、不断裂引用；
#   4) 全部字段带默认值，旧批次快照（matching_batches.response）回放天然兼容。
# ============================================================================
GapSeverity = Literal["blocking", "major", "minor", "missing_data"]
GapStatus = Literal["PASS", "PARTIAL", "FAIL", "UNKNOWN"]


class GapRow(BaseSchema):
    """能力差距矩阵单行：一个岗位要求 × 人才实际情况的对照。"""

    dimension: str = Field(..., max_length=16, description="所属维度：skill/experience/education/quality")
    item: str = Field(..., min_length=1, max_length=64, description="条目名，如技能名 / min_education / min_work_years")
    requirement: Any = Field(..., description="岗位要求值（画像原值，不做加工）")
    actual: Any = Field(None, description="人才实际值（档案原值；缺失时为 None，禁止补值）")
    status: GapStatus = Field(
        "UNKNOWN",
        description=(
            "对照结论：PASS=符合 / PARTIAL=部分符合（含需人工复核） / "
            "FAIL=不符合 / UNKNOWN=待核实（数据缺失）"
        ),
    )
    hit: bool | None = Field(
        None, description="是否命中（仅技能类条目有意义；非技能条目为 None）"
    )
    weight: float = Field(0.0, ge=0, le=1, description="该行归一化权重（同维度内均分，全表合计 1.0）")
    severity: GapSeverity = Field(
        "missing_data",
        description="差距严重程度：blocking=致命 / major=重要 / minor=次要 / missing_data=数据缺失",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="关联证据 ID（引用既有证据链，不新增）"
    )
    source_field: str = Field("", max_length=64, description="数据来源字段，如 talents.skill_tags")
    reason: str = Field("", max_length=200, description="差距原因（事实陈述，不含推断性能力描述）")


class GapMatrix(BaseSchema):
    """能力差距矩阵（单候选人 × 单岗位画像）。"""

    rows: list[GapRow] = Field(default_factory=list, description="差距行（严重度优先排序）")
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="汇总计数：total/pass/partial/fail/unknown/blocking/missing_data",
    )


class MatchItem(BaseSchema):
    """单个候选人的匹配结果。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名")
    # *kwq* 新增（MATCH-017）：显式排名。
    # 原因：接口只保证 items 按分数倒序，前端若用数组下标当名次，一旦分页 / min_score
    #      过滤 / 前端再排序，名次就会错位（截图里出现 #1 却排第三）。
    rank: int = Field(0, ge=0, description="本次结果中的名次，从 1 开始；0 表示未计算")
    # *kwq* 新增（MATCH-018）：整体四态结论。
    # 原因：原输出只有 hard_results 逐项状态，调用方须自己按「FAIL > NEEDS_REVIEW > UNKNOWN
    #      > PASS」聚合，容易出现各端口径不一致；这里由后端统一给出并可追溯。
    decision: HardStatus = Field(
        HardStatus.PASS, description="整体判定：FAIL / NEEDS_REVIEW / UNKNOWN / PASS"
    )
    score: float = Field(..., ge=0, le=100, description="综合匹配分 0-100")
    dimension_scores: dict[str, float] = Field(default_factory=dict, description="四维分数")
    defaulted_dimensions: list[str] = Field(
        default_factory=list, description="因数据缺失而使用默认值的维度（提示结论可信度）"
    )
    data_completeness: float = Field(0.0, ge=0, le=1, description="档案完整度 0~1")
    matched_skills: list[str] = Field(default_factory=list, description="命中的技能（事实，非解释）")
    highlights: list[str] = Field(default_factory=list, description="优势说明（解释性文字）")
    gaps: list[str] = Field(default_factory=list, description="能力差距")
    hard_results: list[HardConditionResult] = Field(default_factory=list, description="四态判定明细")
    suggestion: str | None = Field(None, max_length=512, description="适配建议")
    explanation_source: Literal["dify", "llm_fallback", "rule_fallback"] | None = Field(
        None, description="解释来源；未生成解释时为 None"
    )
    # *kwq* 新增（GAP-001）：结构化能力差距矩阵。
    # 兼容性：默认空列表 —— 旧批次快照无此键，model_validate 回放时自动补默认，不报错。
    gap_matrix: list[GapRow] = Field(
        default_factory=list, description="结构化能力差距矩阵（严重度优先排序；旧批次为空）"
    )


# *kwq* 新增（从属模型）：跑批统计。
# 用途：让「筛掉多少人、多少人待补全」可被核对，防止只报结果不报分母。
class MatchStats(BaseSchema):
    """匹配跑批统计。"""

    candidates: int = Field(0, ge=0, description="参与判定的候选人数")
    excluded_by_hard: int = Field(0, ge=0, description="硬条件 FAIL 被淘汰人数")
    unknown: int = Field(0, ge=0, description="存在 UNKNOWN 项的人数（保留，不淘汰）")
    needs_review: int = Field(0, ge=0, description="存在 NEEDS_REVIEW 项的人数")
    output: int = Field(0, ge=0, description="最终返回人数")


# *kwq* 新增（从属模型）：被淘汰候选人。
# 用途：回答「这个人为什么没出现在结果里」，替代原先静默 continue 丢弃。
class ExcludedItem(BaseSchema):
    """被硬性条件淘汰的候选人。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名")
    status: HardStatus = Field(HardStatus.FAIL, description="淘汰时的判定状态（当前仅 FAIL 会进入）")
    reasons: list[str] = Field(default_factory=list, description="淘汰原因（逐条，非空）")


# *kwq* 新增：匹配跑批响应。
# 用途：POST /matchings/run 与 GET /matchings/batches/{batch_id} 的统一返回结构。
# 设计原因：在既有契约字段基础上只做「新增」，未改动 position_id/position_title/items 等已有字段名。
class MatchRunResponse(BaseSchema):
    """匹配跑批结果。"""

    batch_id: str = Field(..., min_length=8, max_length=32, description="跑批 ID（uuid hex）")
    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称")
    requirement_version: int = Field(1, ge=1, description="本次匹配使用的画像版本")
    recall_mode: Literal["vector", "sql_fallback"] = Field(
        "sql_fallback", description="召回方式：向量召回 / SQL 降级（如实标注，禁止虚报）"
    )
    matched_at: datetime | None = Field(None, description="匹配时间")
    stats: MatchStats = Field(default_factory=MatchStats, description="跑批统计")
    items: list[MatchItem] = Field(default_factory=list, description="候选人结果（按分数倒序）")
    excluded: list[ExcludedItem] = Field(default_factory=list, description="被淘汰候选人及原因")
    # *kwq* 新增：返回本次实际生效的权重（请求覆盖或岗位配置归一化后），便于前端展示与结果复算。
    weights_used: dict[str, float] | None = Field(
        None, description="本次打分实际使用的四维权重（归一化后）；未返回表示使用内置默认"
    )


# ============================================================================
# *kwq* 新增（MATCH-010）：接口层请求 / 列表 / 历史 契约。
# 说明：补齐 POST /matchings/run、POST /matchings/explain、GET 列表所需入参，
#      以及历史查询的轻量返回结构；不改动上面已冻结的响应字段。
# 影响范围：纯新增模型，无既有调用方。
# ============================================================================


# *kwq* 新增：执行匹配请求体。
# 设计原因：
#   1) talent_ids 为空表示「全库召回」，语义必须显式，不能靠缺省值猜；
#   2) strategy 仅暴露已注册的三种策略，避免前端传未知字符串导致工厂回落；
#   3) with_explanation 允许关闭解释（批量试算时省掉外部调用），
#      关闭时 explanation_source 必须为 None，不允许伪造解释。
class MatchRunRequest(BaseSchema):
    """执行匹配请求。每次运行新增历史批次；false解释仍有规则兜底。"""

    model_config = {"json_schema_extra": {"examples": [{
        "position_id": 1, "talent_ids": [1, 2, 3, 4, 5], "top_k": 5,
        "min_score": 0, "strategy": "hybrid", "with_explanation": True
    }]}}

    position_id: int = Field(..., gt=0, description="岗位 ID")
    talent_ids: list[int] | None = Field(
        None, description="指定候选人 ID 列表；为空表示按岗位条件全库召回"
    )
    top_k: int = Field(20, ge=1, le=200, description="返回候选人数上限")
    min_score: float = Field(0.0, ge=0, le=100, description="最低综合分（仅过滤输出，不改变排序）")
    strategy: Literal["hard", "soft", "hybrid"] = Field(
        "hybrid", description="匹配策略：hard 只做硬过滤 / soft 只打分 / hybrid 先硬后软（默认）"
    )
    with_explanation: bool = Field(
        True,
        description="是否调用外部模型（Dify/LLM）生成解释；false 时仅用确定性规则生成，不产生外部调用与费用",
    )
    requirement_version: int | None = Field(
        None, ge=1, description="指定岗位画像版本；为空使用最新已确认版本"
    )
    # *kwq* 新增：权重可配（需求「权重可配」项）。
    # 设计原因：权重此前只藏在岗位 requirements.weights 里，调用方无法临时试算不同权重；
    #          这里允许单次请求覆盖，但不落盘到岗位配置（试算不改配置）。
    weights: dict[str, float] | None = Field(
        None,
        description=(
            "四维权重覆盖（skill/experience/education/quality），服务端自动归一化；"
            "为空时使用岗位 requirements.weights，岗位也未配置则用内置默认"
        ),
    )

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        """权重入参校验：非法直接 422，不做静默回落（静默回落会让「试算」结果失真）。"""
        if value is None:
            return value
        allowed = {"skill", "experience", "education", "quality"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"未知权重维度 {sorted(unknown)}，允许的维度：{sorted(allowed)}")
        missing = allowed - set(value)
        if missing:
            # 四维必须完整传入：部分传参会让 _normalize_weights 静默回落岗位/默认权重，
            # 试算结果与调用方意图不符且无提示，违背「非法直接 422」的本类约定。
            raise ValueError(f"缺少权重维度 {sorted(missing)}，四维权重必须完整传入：{sorted(allowed)}")
        for key, raw in value.items():
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"权重 {key} 不是数字：{raw!r}") from exc
            if number < 0:
                raise ValueError(f"权重 {key} 不能为负数")
        if sum(float(raw) for raw in value.values()) <= 0:
            raise ValueError("权重总和必须大于 0")
        return value


# *kwq* 新增：匹配解释请求体。
# 原因：解释必须锚定「某岗位 × 某人才」的一次真实判定，因此两个 ID 都必填，
#      不接受只传人才 ID（否则解释会脱离岗位要求，变成简历点评）。
class MatchExplainRequest(BaseSchema):
    """生成 / 重新生成匹配解释的请求。"""

    position_id: int = Field(..., gt=0, description="岗位 ID")
    talent_id: int = Field(..., gt=0, description="人才 ID")
    requirement_version: int | None = Field(
        None, ge=1, description="指定岗位画像版本；为空使用最新已确认版本"
    )


# *kwq* 新增：历史匹配记录条目（GET /matchings/talents/{id}）。
# 原因：历史查询只需要定位批次与分数，不返回整包证据，避免响应体随历史条数膨胀。
class MatchHistoryItem(BaseSchema):
    """单条历史匹配记录（按批次）。"""

    batch_id: str = Field(..., description="跑批 ID")
    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称（可能已删除）")
    # *kwq* 新增（MATCH-016）：列表页需要看到「是谁」，否则同一岗位多批次记录无法区分。
    # 原因：端到端验收时 GET /matchings 首条 talent_name 为空，HR 只能看到分数和批次号。
    talent_id: int | None = Field(None, gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名（档案已删除时为空）")
    score: float = Field(..., ge=0, le=100, description="综合匹配分")
    strategy: str | None = Field(None, max_length=16, description="匹配策略")
    requirement_version: int | None = Field(None, ge=1, description="当时的岗位画像版本")
    matched_at: datetime | None = Field(None, description="匹配时间")
    explanation_source: str | None = Field(
        None, max_length=16, description="解释来源；未生成解释时为 None"
    )


# ============================================================================
# *kwq* 新增（双向匹配）：人才找岗位。
# 需求依据：需求输出版 §06「双向匹配统一设计」——同一个打分内核，两个方向。
# 实现口径：硬条件判定方向不变（仍是「人才是否满足岗位要求」），
#          变的是召回方向（全量开放岗位）与输出形态（这个人适合哪些岗位）。
# ============================================================================
class MatchReverseRequest(BaseSchema):
    """双向匹配请求（给定人才，反查合适岗位）。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    position_ids: list[int] | None = Field(
        None,
        description="限定参与评估的岗位 ID 列表；为空表示评估全部开放岗位（有已确认画像的）",
    )
    top_k: int = Field(10, ge=1, le=200, description="返回岗位数上限")
    min_score: float = Field(0.0, ge=0, le=100, description="最低综合分（仅过滤展示）")
    strategy: Literal["hard", "soft", "hybrid"] = Field(
        "hybrid", description="匹配策略：hard 只做硬过滤 / soft 只打分 / hybrid 先硬后软（默认）"
    )
    # *kwq* 默认 False：一次反查可能评估几十个岗位，批量解释成本高；
    #       规则解释（rule_fallback）不产生外部调用，关掉的是 Dify/LLM 通道。
    with_explanation: bool = Field(
        False, description="是否调用外部模型（Dify/LLM）生成解释；false 时仅用确定性规则"
    )
    weights: dict[str, float] | None = Field(
        None,
        description="四维权重覆盖（skill/experience/education/quality），服务端自动归一化",
    )

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        """复用 MatchRunRequest 的权重校验规则（与正向完全同口径）。"""
        return MatchRunRequest._validate_weights(value)  # noqa: SLF001 - 同模块内复用校验逻辑


class MatchReverseItem(BaseSchema):
    """双向匹配结果：一个适合该人才的岗位。"""

    rank: int = Field(..., ge=1, description="名次，从 1 开始")
    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称")
    requirement_version: int = Field(1, ge=1, description="评估时使用的画像版本")
    decision: HardStatus = Field(
        HardStatus.PASS, description="整体判定：FAIL / NEEDS_REVIEW / UNKNOWN / PASS"
    )
    score: float = Field(..., ge=0, le=100, description="综合匹配分 0-100")
    dimension_scores: dict[str, float] = Field(default_factory=dict, description="四维分数")
    data_completeness: float = Field(0.0, ge=0, le=1, description="档案完整度 0~1")
    matched_skills: list[str] = Field(default_factory=list, description="命中的技能（事实）")
    highlights: list[str] = Field(default_factory=list, description="优势说明")
    gaps: list[str] = Field(default_factory=list, description="能力差距")
    hard_results: list[HardConditionResult] = Field(default_factory=list, description="四态判定明细")
    suggestion: str | None = Field(None, max_length=512, description="适配建议")
    explanation_source: Literal["dify", "llm_fallback", "rule_fallback"] | None = Field(
        None, description="解释来源"
    )


class MatchReverseSkipped(BaseSchema):
    """未参与评估的岗位及原因（画像未确认 / 已关闭等）。"""

    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称")
    reason: str = Field(..., max_length=256, description="跳过原因")


class MatchReverseResponse(BaseSchema):
    """双向匹配结果。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名")
    positions_evaluated: int = Field(0, ge=0, description="实际完成评估的岗位数")
    skipped: list[MatchReverseSkipped] = Field(
        default_factory=list, description="未参与评估的岗位（含原因，不静默丢弃）"
    )
    recall_mode: Literal["vector", "sql_fallback"] = Field(
        "sql_fallback", description="召回方式（如实标注）"
    )
    weights_used: dict[str, float] | None = Field(
        None, description="本次打分实际使用的权重（归一化后）"
    )
    # *kwq* 新增（REV-001）：多岗位权重不一致时的口径说明。
    # 原因：一次反查会评估多个岗位，各自权重可能不同；原实现回传「最后一个岗位」的权重，
    #      调用方据此复算会得到与 items 不一致的分数（静默口径漂移）。
    weights_note: str | None = Field(
        None,
        max_length=300,
        description="权重口径说明；各岗位权重不同时 weights_used 为空并在此说明原因",
    )
    items: list[MatchReverseItem] = Field(
        default_factory=list, description="适合的岗位（按分数倒序）"
    )
    excluded: list[MatchReverseSkipped] = Field(
        default_factory=list, description="硬条件 FAIL 的岗位及逐条原因（回答「为什么不合适」）"
    )
    # *kwq* 新增：非阻断提示回传（岗位暂停、向量召回降级原因等）。
    # 原因：pipeline 逐岗位收集的 errors 此前只进日志，调用方看不到「为什么走 sql_fallback」，
    #      排查召回质量时缺少依据；该字段为增量可选字段，不改变既有契约。
    errors: list[str] = Field(
        default_factory=list, description="评估过程中的非阻断提示（岗位暂停、召回降级原因等）"
    )


# ============================================================================
# *kwq* 新增（CMP-001）：多候选人横向对比。
# 需求依据：HR 真实决策场景是「3 个候选人选谁」而非「一个人打多少分」。
# 设计红线：
#   1) 只读 MatchingBatch 快照，不重算分数、不产生新批次；
#   2) 所有数值原样取自快照 MatchItem，保证与单条结果逐字段一致（不做二次加工）；
#   3) 快照缺失的人才不静默丢弃，进 notes 点名说明。
# ============================================================================
class MatchCompareRequest(BaseSchema):
    """横向对比请求（同一批次的 2~8 名候选人）。"""

    batch_id: str = Field(..., min_length=8, max_length=32, description="批次 ID（uuid hex）")
    talent_ids: list[int] = Field(
        ..., min_length=2, max_length=8, description="对比人才 ID 列表（2~8 个，重复自动去重）"
    )
    include_gap: bool = Field(False, description="是否返回能力差距矩阵（需该批次已生成）")

    @field_validator("talent_ids")
    @classmethod
    def _dedupe_and_limit(cls, value: list[int]) -> list[int]:
        """去重并保序；去重后不足 2 人直接拒绝（对比至少需要两个人）。"""
        unique = list(dict.fromkeys(int(v) for v in value))
        if len(unique) < 2:
            raise ValueError("横向对比至少需要 2 名不同候选人")
        if len(unique) > 8:
            raise ValueError("单次横向对比最多 8 名候选人")
        return unique


class MatchCompareReview(BaseSchema):
    """对比行里的 HR 复核状态（来自 matching_reviews，与引擎判定完全独立）。

    为什么把「状态 + 原因 + 备注」一起带出来：
        HR 在短名单页看对比时，真正想知道的是「这个人我们走到哪一步了、
        上次为什么被淘汰」，而不是只有一个状态色块。少一次跳转就少一次流失。

    红线：这里不返回 computed 字段（如「是否还能改」），也不做状态推断——
        状态流转规则属于写接口的职责，读接口原样透传，避免两处规则漂移。
    """

    review_id: int = Field(..., description="复核记录 ID")
    review_status: ReviewStatusLiteral = Field(..., description="复核状态")
    status_label: str | None = Field(None, max_length=16, description="状态中文名")
    reason_code: str | None = Field(None, max_length=32, description="最近一次决策原因码")
    note: str | None = Field(None, max_length=512, description="最近一次决策备注")
    decided_by: int | None = Field(None, description="最近一次决策人用户 ID")
    decided_at: datetime | None = Field(None, description="最近一次决策时间")
    version: int = Field(1, ge=1, description="乐观锁版本号；提交决策时作为 expected_version")


class MatchCompareGapStat(BaseSchema):
    """单个候选人的缺口分布（从 gap_matrix 聚合，不重算任何评分）。"""

    talent_id: int = Field(..., description="人才 ID")
    total: int = Field(0, ge=0, description="矩阵总行数")
    pass_count: int = Field(0, ge=0, description="符合项数")
    partial: int = Field(0, ge=0, description="部分符合项数")
    fail: int = Field(0, ge=0, description="不符合项数")
    unknown: int = Field(0, ge=0, description="待核实项数")
    blocking: int = Field(0, ge=0, description="致命缺口数")
    major: int = Field(0, ge=0, description="重要缺口数")
    minor: int = Field(0, ge=0, description="次要缺口数")
    missing_data: int = Field(0, ge=0, description="因数据缺失无法判断的项数")


class MatchCompareCommonGap(BaseSchema):
    """多人共同/高频缺口：「不只他缺，好几个人都缺」。

    这个信息的决策价值高于分数排序：
        - all_missing=True → 岗位要求可能高于市场供给，该调预期而不是继续筛人；
        - 只有个别缺 → 那是候选人的真实短板，可以拿去做面试追问。
    """

    dimension: str = Field(..., max_length=16, description="所属维度")
    item: str = Field(..., min_length=1, max_length=64, description="条目名（技能名 / 条件名）")
    missing_for: list[int] = Field(
        default_factory=list, description="在该项上不满足的候选人 ID 列表"
    )
    all_missing: bool = Field(False, description="是否所有参与对比的候选人都不满足")


class MatchCompareRow(BaseSchema):
    """横向对比中的单个候选人（字段与 MatchItem 一一对应，原样取自快照）。"""

    talent_id: int = Field(..., gt=0, description="人才 ID")
    talent_name: str | None = Field(None, max_length=64, description="人才姓名")
    rank: int = Field(0, ge=0, description="原批次中的名次")
    score: float = Field(..., ge=0, le=100, description="综合匹配分（原值，不重算）")
    decision: HardStatus = Field(HardStatus.PASS, description="整体判定（原值）")
    dimension_scores: dict[str, float] = Field(default_factory=dict, description="四维得分（原值）")
    data_completeness: float = Field(0.0, ge=0, le=1, description="档案完整度（原值）")
    defaulted_dimensions: list[str] = Field(
        default_factory=list, description="因数据缺失使用默认分的维度"
    )
    hard_results: list[HardConditionResult] = Field(
        default_factory=list, description="关键条件四态明细（原值）"
    )
    matched_skills: list[str] = Field(default_factory=list, description="命中技能（事实）")
    highlights: list[str] = Field(default_factory=list, description="优势说明")
    gaps: list[str] = Field(default_factory=list, description="不足说明")
    suggestion: str | None = Field(None, max_length=512, description="适配建议")
    explanation_source: Literal["dify", "llm_fallback", "rule_fallback"] | None = Field(
        None, description="解释来源"
    )
    gap_matrix: list[GapRow] = Field(
        default_factory=list, description="结构化能力差距矩阵（include_gap=false 时为空）"
    )
    review: MatchCompareReview | None = Field(
        None, description="HR 复核状态；该候选人尚未建档时为 None（不等于『待复核』）"
    )


class MatchCompareResponse(BaseSchema):
    """横向对比结果（不落库、不产生新批次）。"""

    batch_id: str = Field(..., description="批次 ID")
    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str | None = Field(None, max_length=128, description="岗位名称")
    requirement_version: int = Field(1, ge=1, description="该批次使用的画像版本")
    matched_at: datetime | None = Field(None, description="该批次匹配时间")
    rows: list[MatchCompareRow] = Field(default_factory=list, description="候选人对比行（按原名次排序）")
    notes: list[str] = Field(
        default_factory=list, description="未参与对比的人才说明（不静默丢弃）"
    )
    # *kwq* 新增（CMP-002）：缺口横向概览。
    # 设计取舍：概览（计数 + 共同缺口）始终返回，明细（rows[].gap_matrix）受
    # include_gap 控制——概览是几百字节，明细可能是几十 KB，分开控制更实用。
    gap_stats: list[MatchCompareGapStat] = Field(
        default_factory=list, description="每位候选人的缺口分布（与 rows 同序）"
    )
    common_gaps: list[MatchCompareCommonGap] = Field(
        default_factory=list,
        description="出现在 2 人及以上身上的致命/重要缺口（按涉及人数降序，最多 10 条）",
    )
    gap_note: str | None = Field(None, max_length=256, description="缺口口径说明")
