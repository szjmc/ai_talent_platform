"""人才档案请求与响应模型。

标签：[RAG组] [P1]

敏感字段在 Service 转响应时脱敏；请求模型拒绝未知字段，避免把非白名单
属性直接写入 ORM。状态值与 app.models.talent.Talent 注释及前端契约一致。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import BaseSchema, ORMModel


class GenderEnum(StrEnum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class TalentStatusEnum(StrEnum):
    ACTIVE = "active"
    LEFT = "left"
    RESERVE = "reserve"


class PotentialLevelEnum(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


def _clean_tags(value: list[str] | None) -> list[str]:
    """去空白、去重并保持 HR 录入顺序。"""
    return list(dict.fromkeys(item.strip() for item in value or [] if item.strip()))


class TalentCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=64)
    gender: GenderEnum = GenderEnum.UNKNOWN
    birth_date: date | None = None
    mobile: str | None = Field(None, pattern=r"^1[3-9]\d{9}$")
    email: EmailStr | None = None
    education: str | None = Field(None, max_length=20)
    school: str | None = Field(None, max_length=128)
    major: str | None = Field(None, max_length=128)
    work_years: int = Field(0, ge=0, le=60)
    current_company: str | None = Field(None, max_length=128)
    current_position: str | None = Field(None, max_length=128)
    organization_id: int | None = Field(None, gt=0)
    skill_tags: list[str] = Field(default_factory=list, max_length=50)
    certificates: list[str] = Field(default_factory=list, max_length=50)
    self_evaluation: str | None = Field(None, max_length=5000)
    status: TalentStatusEnum = TalentStatusEnum.ACTIVE
    source: str = Field("manual", pattern=r"^(manual|ai_resume)$")
    resume_id: int | None = Field(None, gt=0)

    _clean_skills = field_validator("skill_tags", "certificates")(lambda value: _clean_tags(value))


class TalentUpdate(BaseSchema):
    name: str | None = Field(None, min_length=1, max_length=64)
    gender: GenderEnum | None = None
    birth_date: date | None = None
    mobile: str | None = Field(None, pattern=r"^1[3-9]\d{9}$")
    email: EmailStr | None = None
    education: str | None = Field(None, max_length=20)
    school: str | None = Field(None, max_length=128)
    major: str | None = Field(None, max_length=128)
    work_years: int | None = Field(None, ge=0, le=60)
    current_company: str | None = Field(None, max_length=128)
    current_position: str | None = Field(None, max_length=128)
    organization_id: int | None = Field(None, gt=0)
    skill_tags: list[str] | None = Field(None, max_length=50)
    certificates: list[str] | None = Field(None, max_length=50)
    self_evaluation: str | None = Field(None, max_length=5000)
    status: TalentStatusEnum | None = None
    version: int | None = Field(None, ge=1, description="乐观锁版本；传入且不一致时返回 409")

    _clean_skills = field_validator("skill_tags", "certificates")(lambda value: None if value is None else _clean_tags(value))


class TalentQuery(BaseSchema):
    keyword: str | None = Field(None, max_length=64)
    education: str | None = Field(None, max_length=20)
    work_years_min: int | None = Field(None, ge=0, le=60)
    work_years_max: int | None = Field(None, ge=0, le=60)
    organization_id: int | None = Field(None, gt=0)
    status: TalentStatusEnum | None = None
    skill: str | None = Field(None, max_length=64)


class TalentPortraitResponse(ORMModel):
    id: int
    talent_id: int
    version: int
    dimensions: dict[str, float] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggested_positions: list[str] = Field(default_factory=list)
    potential_level: PotentialLevelEnum | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    generated_at: datetime | None = None
    created_at: datetime | None = None


class TalentResponse(ORMModel):
    id: int
    name: str
    gender: GenderEnum
    birth_date: date | None = None
    age: int | None = None
    mobile: str | None = Field(None, description="脱敏手机号")
    email: EmailStr | None = None
    id_card: str | None = Field(None, description="脱敏身份证号")
    education: str | None = None
    school: str | None = None
    major: str | None = None
    work_years: int
    current_company: str | None = None
    current_position: str | None = None
    organization_id: int | None = None
    organization_name: str | None = None
    skill_tags: list[str] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    self_evaluation: str | None = None
    status: TalentStatusEnum
    potential_level: PotentialLevelEnum | None = None
    portrait_summary: str | None = None
    source: str
    resume_id: int | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TalentDetailResponse(TalentResponse):
    portrait: TalentPortraitResponse | None = None


class TalentSemanticSearchRequest(BaseSchema):
    """预留给 Milvus 语义检索；当前路由未开放，避免返回伪检索结果。"""

    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(10, ge=1, le=100)
    filters: TalentQuery = Field(default_factory=TalentQuery)


class TalentStatsResponse(ORMModel):
    total: int
    by_organization: list[dict[str, int | None]]
    by_education: list[dict[str, str | int | None]]
    by_month: list[dict[str, int]]
