# 此处声明 Web RAG 数据结构，接口是否对外开放以总路由注册为准。
"""A/B共用入库契约，避免两处定义漂移。
标签：[RAG组] [P1]
"""
from app.schemas.rag import IngestRequest, IngestResponse
from pydantic import BaseModel
class UploadResponse(BaseModel):
    file_id: str
    object_name: str
    status: str
    message: str
