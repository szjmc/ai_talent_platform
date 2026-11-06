"""
模块：app/schemas/resume.py
职责：简历上传与解析相关结构。

标签：[RAG组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. ResumeUploadResponse   file_id、resume_id、file_name、file_size、status="uploaded"
2. ResumeParseRequest     resume_id、force(是否重新解析)、talent_id(可空，解析后绑定)
3. ResumeParseResult      resume_id、status(parsing/success/failed)、
                          parsed(结构化内容：basic_info/education/work_experience/projects/skills)、
                          accuracy、warnings: list[str]、parsed_at
4. ResumeResponse         id、file_name、file_type、file_size、status、talent_id、created_at
5. ResumeQuery            keyword、status、talent_id
实现要点：
- 上传接口用 UploadFile，校验扩展名（settings.allowed_upload_extensions）与大小；
- 解析是耗时任务：接口立即返回 task_id（或 resume_id + status=parsing），
  真实解析在 ai/workflows/resume_workflow.py 中异步执行，结果写回 resume 表；
- parsed 结构必须与 models/resume.py 中 JSON 字段定义一一对应。
=========================================================================
"""

"""gxd-简历上传、结构化解析与任务输入输出；保留既有Schema命名。"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class ResumeBasicInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(None, max_length=64)
    mobile: str | None = Field(None, max_length=20)
    email: str | None = Field(None, max_length=128)
    education: str | None = Field(None, max_length=20)
    school: str | None = Field(None, max_length=128)
    major: str | None = Field(None, max_length=128)
    current_company: str | None = Field(None, max_length=128)
    current_position: str | None = Field(None, max_length=128)
    work_years: int | None = Field(None, ge=0, le=80)

class ResumeExperienceData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization: str | None = Field(None, max_length=128)
    title: str | None = Field(None, max_length=128)
    start_date: str | None = Field(None, max_length=32)
    end_date: str | None = Field(None, max_length=32)
    description: str = Field("", max_length=6000)
    evidence: str = Field("", max_length=3000, description="原文证据，不得生成不存在的引用")

class ResumeParsedData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basic_info: ResumeBasicInfo = Field(default_factory=ResumeBasicInfo)
    education: list[ResumeExperienceData] = Field(default_factory=list, max_length=30)
    work_experience: list[ResumeExperienceData] = Field(default_factory=list, max_length=100)
    projects: list[ResumeExperienceData] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values):
        """gxd-清理空技能与大小写重复，保留首次出现的展示名称。"""
        result = {}; aliases = {"python3": "Python", "python": "Python", "fastapi": "FastAPI"}
        for value in values:
            value = value.strip()
            if value:
                value = aliases.get(value.casefold(), value)
                result.setdefault(value.casefold(), value[:80])
        return list(result.values())

class ResumeParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_id: int | None = Field(None, gt=0)
    force: bool = False
    talent_id: int | None = Field(None, gt=0)

class ResumeBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    talent_id: int | None = Field(None, gt=0)
    create_talent: bool = False
    @model_validator(mode="after")
    def exclusive_target(self):
        """gxd-绑定已有档案与新建档案必须且只能选择一种。"""
        if bool(self.talent_id) == self.create_talent:
            raise ValueError("请选择talent_id或create_talent=true之一")
        return self

class ResumeUploadResponse(BaseModel):
    file_id: int | None = None
    resume_id: int
    file_name: str
    file_size: int
    status: str
    duplicated: bool = False

class ResumeParseResult(BaseModel):
    resume_id: int
    status: str
    parsed: ResumeParsedData | None = None
    accuracy: float | None = None
    warnings: list[str] = Field(default_factory=list)
    parsed_at: datetime | None = None

class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_name: str
    file_type: str | None
    file_size: int
    status: str
    talent_id: int | None
    created_at: datetime

class ResumeQuery(BaseModel):
    keyword: str | None = None
    status: Literal["uploaded", "parsing", "success", "failed"] | None = None
    talent_id: int | None = Field(None, gt=0)
