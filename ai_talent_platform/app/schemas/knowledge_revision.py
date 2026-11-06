# expected_version 来自当前版本的最新响应。编辑请求正文是完整替换内容，服务会另存新版本。
"""文档草稿、预览与发布请求。
标签：[RAG组] [P1]
"""
from pydantic import BaseModel, Field
from app.core.rag_constants import CategoryLiteral


class RevisionEdit(BaseModel):
    expected_version: int = Field(ge=1, description='读取详情返回的乐观锁version')
    title: str = Field(min_length=1, max_length=255)
    category: CategoryLiteral
    body: str = Field(min_length=1, max_length=400000)


class RevisionAction(BaseModel):
    expected_version: int = Field(ge=1)

