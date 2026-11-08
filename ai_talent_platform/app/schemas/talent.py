# 人才接口的入参与输出结构集中在此层。
"""
模块：app/schemas/talent.py
职责：人才档案相关请求 / 响应结构。

标签：[RAG组] [P1]

敏感字段在 Service 转响应时脱敏；请求模型拒绝未知字段，避免把非白名单
属性直接写入 ORM。状态值与 app.models.talent.Talent 注释及前端契约一致。
"""

"""gxd-人才档案、画像、语义检索、合并与RAG问答契约。"""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.talent_resume_schema import ResumeBasicInfo

def _clean_tags(value: list[str] | None) -> list[str] | None:
    """去空白、去重并保持 HR 录入顺序（历史实现见 fb8660e）。"""
    if value is None:
        return None
    return list(dict.fromkeys(item.strip() for item in value if item and item.strip()))


class TalentCreate(ResumeBasicInfo):
    name: str = Field(min_length=1, max_length=64)
    work_years: int = Field(0, ge=0, le=80)
    organization_id: int | None = Field(None, gt=0)
    skill_tags: list[str] = Field(default_factory=list, max_length=100)
    self_evaluation: str | None = Field(None, max_length=6000)
    country: str | None = Field(None, max_length=64, description="国家")
    country_code: str | None = Field(None, max_length=8, description="国家代码")
    province: str | None = Field(None, max_length=64, description="省份/州")
    work_city: str | None = Field(None, max_length=64, description="工作城市")
    job_level: str | None = Field(None, max_length=32, description="职级")
    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        """gxd-拒绝纯空白姓名，保留规范化姓名。"""
        if not value.strip():
            raise ValueError("姓名不能为空")
        return value.strip()
    @field_validator("skill_tags")
    @classmethod
    def normalize_skill_tags(cls, value):
        """标签规范化：去空白、去重、保序。"""
        return _clean_tags(value)

class TalentUpdate(ResumeBasicInfo):
    organization_id: int | None = Field(None, gt=0)
    skill_tags: list[str] | None = Field(None, max_length=100)
    self_evaluation: str | None = Field(None, max_length=6000)
    country: str | None = Field(None, max_length=64, description="国家")
    country_code: str | None = Field(None, max_length=8, description="国家代码")
    province: str | None = Field(None, max_length=64, description="省份/州")
    work_city: str | None = Field(None, max_length=64, description="工作城市")
    job_level: str | None = Field(None, max_length=32, description="职级")
    version: int | None = Field(None, ge=1, description="乐观锁版本号（不传则跳过版本校验）")
    @field_validator("skill_tags")
    @classmethod
    def normalize_skill_tags(cls, value):
        """标签规范化：去空白、去重、保序（None 表示不更新）。"""
        return _clean_tags(value)
    @model_validator(mode="after")
    def valid_patch(self):
        """gxd-区分未提交与显式清空，禁止清空必填姓名和工作年限。"""
        if "name" in self.model_fields_set and not self.name:
            raise ValueError("姓名不能为空")
        if "work_years" in self.model_fields_set and self.work_years is None:
            raise ValueError("工作年限不能为null")
        return self

class TalentQuery(BaseModel):
    keyword: str | None = None
    education: str | None = None
    organization_id: int | None = Field(None, gt=0)
    status: str | None = None
    skill: str | None = None
    work_years_min: int | None = Field(None, ge=0)
    work_years_max: int | None = Field(None, ge=0)

class TalentLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    category: Literal["skill", "experience", "quality", "position"]
    evidence: str = Field(min_length=1, max_length=3000)

class TalentPortrait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimensions: dict[str, float] = Field(default_factory=dict)
    labels: list[TalentLabel] = Field(default_factory=list, max_length=150)
    strengths: list[str] = Field(default_factory=list, max_length=30)
    weaknesses: list[str] = Field(default_factory=list, max_length=30)
    suggested_positions: list[str] = Field(default_factory=list, max_length=20)
    potential_level: str | None = None
    summary: str = Field("", max_length=512)
    warnings: list[str] = Field(default_factory=list)
    @field_validator("dimensions")
    @classmethod
    def valid_scores(cls, values):
        """gxd-校验维度名称与分值范围；缺少评价规则时业务层不采纳分数。"""
        if any(k not in {"skill", "experience", "quality", "potential"} or not 0 <= v <= 100 for k,v in values.items()):
            raise ValueError("画像维度或分值不合法")
        return values

class TalentSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    education: str | None = Field(None, max_length=20)
    work_years_min: int | None = Field(None, ge=0, le=80)
    work_years_max: int | None = Field(None, ge=0, le=80)
    organization_id: int | None = Field(None, gt=0)
    skill: str | None = Field(None, max_length=80)
    @model_validator(mode="after")
    def valid_range(self):
        """gxd-拒绝上下限倒置的工作年限筛选。"""
        if self.work_years_min is not None and self.work_years_max is not None and self.work_years_min > self.work_years_max:
            raise ValueError("工作年限下限不能超过上限")
        return self

class TalentSemanticSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=50)
    filters: TalentSearchFilters = Field(default_factory=TalentSearchFilters)

class TalentAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    talent_ids: list[int] = Field(min_length=1, max_length=50, description="明确指定单人或多人档案范围")
    top_k: int = Field(8, ge=1, le=20)
    @field_validator("talent_ids")
    @classmethod
    def valid_ids(cls, values):
        """gxd-校验正整数人才编号并去重。"""
        if any(v <= 0 for v in values):
            raise ValueError("人才编号必须大于0")
        return list(dict.fromkeys(values))

class TalentMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: int = Field(gt=0)
    source_ids: list[int] = Field(min_length=1, max_length=20)
    confirmed: bool = False
    @model_validator(mode="after")
    def valid_merge(self):
        """gxd-合并需显式确认，且主档不能出现在被合并档案中。"""
        if not self.confirmed or self.target_id in self.source_ids or any(x <= 0 for x in self.source_ids):
            raise ValueError("请确认合并，且源档案必须与主档不同")
        self.source_ids = list(dict.fromkeys(self.source_ids))
        return self

class TalentResponse(TalentCreate):
    # 响应模型：需要接受 ORM 对象/完整字段字典构造，覆盖请求模型的 extra="forbid"。
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    # ---- 合并恢复的响应字段（service._to_response 传入，历史定义见 fb8660e）----
    gender: str | None = None
    birth_date: datetime | date | None = None
    age: int | None = None
    id_card: str | None = Field(None, description="脱敏身份证号")
    organization_name: str | None = None
    certificates: list[str] = Field(default_factory=list)
    status: str | None = None
    potential_level: str | None = None
    portrait_summary: str | None = None
    source: str | None = None
    resume_id: int | None = None
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

class TalentDetailResponse(TalentResponse):
    resumes: list[dict] = Field(default_factory=list)


# ====================================================================== 合并兼容层
# 说明：合并 origin/develop 时补齐。develop 侧 talent_router / talent_service 引用
#      TalentPortraitResponse，但两侧 schemas/talent.py 均未定义（develop 重构未完成）。
#      字段与 talent_service._portrait_response 的构造参数一一对应。
from datetime import datetime


class TalentPortraitResponse(BaseModel):
    """人才画像响应（对应 TalentPortrait 模型的对外契约）。"""

    id: int
    talent_id: int
    version: int
    dimensions: dict = Field(default_factory=dict, description="画像维度分值")
    labels: list = Field(default_factory=list, description="AI 标签数组")
    strengths: list = Field(default_factory=list, description="优势列表")
    weaknesses: list = Field(default_factory=list, description="短板列表")
    suggested_positions: list = Field(default_factory=list, description="建议岗位")
    potential_level: str | None = Field(None, description="潜力等级")
    model_name: str | None = Field(None, description="生成模型")
    prompt_version: str | None = Field(None, description="提示词版本")
    generated_at: datetime | None = None
    created_at: datetime | None = None


class TalentStatsResponse(BaseModel):
    """人才统计数据（/talents/stats 接口契约，合并 origin/develop 时补齐）。"""

    total: int = Field(0, description="人才总数")
    by_organization: list = Field(default_factory=list, description="部门人才分布")
    by_education: list = Field(default_factory=list, description="学历分布")
    by_month: list = Field(default_factory=list, description="月度新增趋势（1-12月）")
