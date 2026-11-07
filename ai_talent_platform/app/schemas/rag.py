# 兼容入库使用文件 ID 和对象存储定位信息，问题向量接口只负责向量化。
"""标签：[RAG组] [P1]
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

from app.core.rag_constants import CategoryLiteral

class IngestRequest(BaseModel):
    file_id: str = Field(..., description="A角色生成的唯一文件ID")
    bucket_name: str = Field(..., description="MinIO 桶名")
    object_name: str = Field(..., description="MinIO 对象名")
    file_name: str = Field(..., description="原始文件名（带后缀）")
    category: CategoryLiteral = Field(
        ..., description="知识类别：resume/job_description/assessment/policy/training/case/development_plan"
    )

class IngestResponse(BaseModel):
    file_id: str

class IndexDeleteRequest(BaseModel):
    """删除文档索引请求：文件ID采用数字字符串，分类只允许平台约定的英文标识。"""

    # 这里是 files 表主键，不是 knowledge_docs 主键，也不是任意 UUID。
    file_id: str = Field(pattern=r"^[1-9][0-9]*$")
    category: CategoryLiteral


class EmbedQueryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="需要转向量的查询文本")

class EmbedQueryResponse(BaseModel):
    text: str
    embedding: List[float]
    dim: int

class DocumentResult(BaseModel):
    chunk_id: str
    file_id: str
    content: str
    source: str
    category: str
    retrieval_type: Literal["semantic", "bm25", "hybrid"] = "semantic"
    score: Optional[float] = None

class SourceResult(BaseModel):
    file_id: str
    chunk_id: str
    source: str

class ChatRequest(BaseModel):
    question: str
    category: Optional[CategoryLiteral] = None
    top_k: int = 5
    allowed_file_ids: Optional[List[str]] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceResult]
    request_id: str
