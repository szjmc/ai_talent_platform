# 解析状态用于轮询，结构化字段经过响应契约约束。accuracy 可以为空。
"""简历请求/响应契约，不把内部文件路径暴露给客户端。
标签：[RAG组] [P1]。"""
from datetime import datetime,date
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
class Experience(BaseModel):
    company: str = Field(min_length=1,max_length=128)
    title: str | None = Field(default=None,max_length=128)
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None
class ParsedResume(BaseModel):
    basic_info: dict = Field(default_factory=dict)
    education: list[dict] = Field(default_factory=list)
    work_experience: list[Experience] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
class ResumeParseRequest(BaseModel):
    force: bool = False
class ResumeBindRequest(BaseModel):
    talent_id: int = Field(gt=0)
class ResumeResponse(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int
    file_id:int|None=None
    talent_id:int|None=None
    file_name:str
    file_type:str|None=None
    file_size:int
    status:Literal['uploaded','parsing','success','failed']
    parsed_at:datetime|None=None
    created_at:datetime
class ResumeUploadResponse(ResumeResponse):
    duplicate:bool=False
class ResumeParseResult(BaseModel):
    resume_id:int
    status:str
    parsed:ParsedResume|None=None
    accuracy:float|None=None
    warnings:list[str]=Field(default_factory=list)
    parsed_at:datetime|None=None
    error:str|None=None
class ResumeQuery(BaseModel):
    keyword:str|None=Field(default=None,max_length=255)
    status:Literal['uploaded','parsing','success','failed']|None=None
    talent_id:int|None=Field(default=None,gt=0)
