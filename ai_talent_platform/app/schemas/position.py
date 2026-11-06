"""
模块：app/schemas/position.py
职责：岗位管理相关请求 / 响应结构。

标签：[Agent组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. PositionCreate    title、code、organization_id、level(初/中/高/专家)、
                     headcount(编制人数)、description(岗位说明书全文)、
                     requirements(结构化要求：学历/年限/技能列表)、status
2. PositionUpdate    全部可选字段
3. PositionQuery     keyword、organization_id、level、status、created_at 区间
4. PositionResponse  id、title、code、organization_id、organization_name、level、
                     headcount、status、requirements、skill_tags、created_at
5. PositionRequirement  结构化岗位要求子模型：education、experience_years、
                     skills: list[str]、certificates: list[str]、soft_skills: list[str]
实现要点：
- requirements 建议使用嵌套 Pydantic 模型而非裸 dict，保证 AI 匹配时的字段稳定；
- 岗位说明书解析（AI 提取 requirements）走 Service -> ai/agents/position 流程，
  Router 只接收解析结果或触发解析任务 ID。
=========================================================================
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import BaseSchema

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。

# ============================================================================
# *kwq* 新增（MATCH-001）：JD 智能理解产出结构。
# 说明：本轮只实现 JD 解析链路所需的最小契约（ConditionItem / JobProfileParsed）；
#      PositionCreate 等增删改查 Schema 属岗位管理范围，本任务不动，避免越界。
# 影响范围：仅新增模型，未修改文件内其他内容，不影响既有调用方。
# ============================================================================


# *kwq* 新增：JD 条件条目模型。
# 用途：承载「必要条件 / 优先条件」的单个条目，保留原文与标准化结果，便于人工确认与追溯。
# 设计原因：AI 解析出的条件必须能回到 JD 原文（source_span），否则 HR 无法判断是否被正确理解。
class ConditionItem(BaseSchema):
    """岗位条件条目（必要条件 / 优先条件的最小单元）。"""

    text: str = Field(..., min_length=1, max_length=500, description="条件原文表述")
    normalized: str | None = Field(
        None, max_length=200, description="标准化后的取值（如本科/5年/Python），无法标准化时为 None"
    )
    source_span: str | None = Field(None, max_length=500, description="JD 原文片段，供人工追溯")
    confidence: float = Field(0.0, ge=0, le=1, description="解析置信度，0~1")


# *kwq* 新增：岗位画像模型（JD 解析 + Python 标准化后的唯一产物）。
# 用途：Dify 提取结果的落地结构，也是 HR 确认、写入 position_requirements 的输入。
# 设计原因：
#   1) extra="forbid" 继承自 BaseSchema —— 拒绝 Dify 塞入未声明字段，防脏数据入库；
#   2) min_education / min_work_years 允许为 None，语义是「JD 未提及」，
#      禁止在此层补默认值（缺失必须显式进入 missing_fields，交由业务决定）；
#   3) required_* 与 preferred_* 严格分桶：优先条件永不参与硬性淘汰。
# [岗位匹配接入] JD画像契约供解析、人工确认、匹配共享；不是岗位CRUD全集。
class JobProfileParsed(BaseSchema):
    """岗位画像（结构化后的岗位要求）。"""

    job_title: str = Field(..., min_length=1, max_length=128, description="岗位名称")
    responsibilities: list[str] = Field(default_factory=list, description="岗位职责描述")

    required_conditions: list[ConditionItem] = Field(
        default_factory=list, description="必要条件（可参与硬性过滤）"
    )
    preferred_conditions: list[ConditionItem] = Field(
        default_factory=list, description="优先条件（只加分，不淘汰）"
    )

    required_skills: list[str] = Field(default_factory=list, description="必备技能（硬性）")
    preferred_skills: list[str] = Field(default_factory=list, description="优先技能（软性加分）")

    min_education: str | None = Field(None, max_length=20, description="最低学历；None 表示 JD 未提及")
    min_work_years: int | None = Field(
        None, ge=0, le=50, description="最低工作年限；None 表示 JD 未提及，禁止补值"
    )
    location: str | None = Field(None, max_length=128, description="工作地点")
    salary_range: str | None = Field(None, max_length=64, description="薪资范围（原文）")

    missing_fields: list[str] = Field(default_factory=list, description="缺失字段名列表（Python 计算）")
    conflicts: list[str] = Field(default_factory=list, description="冲突描述列表（Python 计算）")
    review_required: bool = Field(True, description="是否需要 HR 人工确认")

    source: Literal["dify", "llm_fallback", "manual"] = Field(
        "dify", description="产出来源：Dify 工作流 / LLM 降级 / 人工录入"
    )
    prompt_version: str = Field("v1", max_length=20, description="解析所用提示词版本，便于回溯与 A/B")

    # *kwq* 新增：技能列表清洗校验。
    # 原因：Dify 可能返回空串或带空格项，直接参与集合匹配会污染结果（空串命中等）。
    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def _clean_skills(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        return cleaned

    # *kwq* 新增：存在缺失或冲突时强制要求人工确认。
    # 原因：V1.4 要求「未确认的岗位画像不得作为正式匹配依据」，
    #      有缺失/冲突的画像必须 review_required=True，防止被误当成可生效画像。
    @model_validator(mode="after")
    def _force_review_when_incomplete(self) -> "JobProfileParsed":
        if self.missing_fields or self.conflicts:
            self.review_required = True
        return self


# ============================================================================
# *kwq* 新增（MATCH-011）：JD 解析 / 岗位画像确认 / 版本查询契约。
# 说明：JD 原文进入系统后必须先解析成 JobProfileParsed，再由 HR 显式确认才生效；
#      未确认的画像只作为草稿返回，不参与匹配（业务规则见 position_profile_service）。
# 影响范围：纯新增模型，不影响已有岗位相关接口。
# ============================================================================


# *kwq* 新增：JD 解析请求。
# 原因：限制长度下限，避免把「招人」两个字丢给 Dify 后返回一份看似完整的编造画像。
class JdParseRequest(BaseSchema):
    """JD 智能解析请求。"""

    jd_text: str = Field(..., min_length=20, max_length=20000, description="岗位说明书原文", examples=["岗位名称：Python工程师。任职要求：本科及以上，3年以上经验，熟悉Python与FastAPI；Redis优先。"])
    job_title: str | None = Field(
        None, max_length=128, description="岗位名称（岗位表已有则无需传，仅用于校验解析结果）"
    )


# *kwq* 新增：岗位画像确认请求（HR 可先修改再确认）。
# 原因：确认是一次「生效动作」，必须携带完整画像，而不是只传一个 version —— 
#      否则无法保证确认的内容与 HR 看到的完全一致。
class JobProfileConfirmRequest(BaseSchema):
    """岗位画像确认请求：仅提交解析响应data.profile，外层包装profile；不要复制message等响应字段。"""

    model_config = {"json_schema_extra": {"examples": [{
        "profile": {"job_title": "Python工程师", "required_skills": ["Python", "FastAPI"],
                    "preferred_skills": ["Redis"], "min_education": "本科", "min_work_years": 3,
                    "source": "manual"}, "note": "人工核对后确认；岗位ID须真实存在"
    }]}}

    profile: JobProfileParsed = Field(..., description="待确认的完整岗位画像（可先人工修订）")
    expected_version: int | None = Field(
        None, ge=1, description="期望的当前版本号（乐观并发校验，防止覆盖他人确认）"
    )
    note: str | None = Field(None, max_length=200, description="确认备注（写入版本元数据）")


# *kwq* 新增：岗位画像查询响应。
# 原因：前端需要同时知道「画像内容 / 版本号 / 是否已确认」，
#      未确认时 confirmed=False，匹配接口会拒绝执行。
class JobProfileView(BaseSchema):
    """岗位画像视图（含版本与确认状态）。"""

    position_id: int = Field(..., gt=0, description="岗位 ID")
    position_title: str = Field(..., max_length=128, description="岗位名称")
    version: int = Field(..., ge=1, description="画像版本号")
    confirmed: bool = Field(False, description="是否已由 HR 确认（未确认不得用于正式匹配）")
    profile: JobProfileParsed = Field(..., description="结构化岗位画像")
    confirmed_by: int | None = Field(None, description="确认人用户 ID")
    confirmed_at: str | None = Field(None, max_length=32, description="确认时间（ISO8601）")


# *kwq* 新增：JD 解析响应。
# 原因：必须把「实际使用的解析通道」返回给前端 —— 规则兜底和 Dify 解析的可信度不同，
#      前端要据此提示 HR 重点核对缺失字段。
class JdParseResponse(BaseSchema):
    """JD 解析结果（草稿，未生效）。"""

    channel: Literal["dify", "rule_fallback"] = Field(..., description="实际解析通道")
    profile: JobProfileParsed = Field(..., description="结构化画像草稿（需 HR 确认后生效）")
    message: str = Field("解析完成", max_length=200, description="提示语（含缺失字段说明）")
