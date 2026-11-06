# 检索请求控制召回方式、分类、返回条数和重排。相似度阈值只作用于语义候选。
"""知识检索请求校验。
标签：[RAG组] [P1]。"""
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from app.core.rag_constants import CategoryLiteral

class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    category: CategoryLiteral | None = None
    top_k: int = Field(default=5, ge=1, le=25)
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    use_rerank: bool = False
    score_threshold: float = Field(default=-1, ge=-1, le=1)
    result_unit: Literal["auto", "document", "chunk"] = Field(
        default="auto",
        description="auto：指定单份文档时按切片，简历分类按文档，其余按切片；document：每份文档最多一条；chunk：允许同一文档多条",
    )
    doc_id: int | None = Field(default=None, gt=0, description="仅检索这份有权访问的文档")

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()
