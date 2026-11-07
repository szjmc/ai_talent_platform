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


class DocumentEdit(BaseModel):
    """以文档为入口的编辑请求（调用方不传 revision_id / expected_version）。

    后端自动以该文档「当前已发布且生效」的版本为修改基础，另存为新草稿；
    title / category 省略时沿用当前生效版本，避免调用方感知版本细节。
    """
    title: str | None = Field(None, min_length=1, max_length=255, description='省略时沿用当前生效版本的标题')
    category: CategoryLiteral | None = Field(None, description='省略时沿用当前生效版本的分类')
    body: str = Field(min_length=1, max_length=400000, description='完整替换的新正文')


class RevisionAction(BaseModel):
    expected_version: int = Field(ge=1)

