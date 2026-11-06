"""
模块：app/schemas/dify.py
职责：Dify 工作流配置的请求体 / 响应体模型。

标签：[Dify组] [P1]
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.schemas.common import BaseSchema, ORMModel

_REQUIRED_FIELDS = ("workflow_name", "wf_apikey")
WORKFLOW_UPDATABLE_FIELDS = {
    "workflow_name", "wf_apikey", "wf_explanation", "wf_var", "updated_by"
}


class WorkflowInfoCreate(BaseSchema):
    """新增配置：系统字段（id / 时间戳 / is_deleted / version）不可由客户端指定。"""

    workflow_name: str = Field(..., min_length=1, max_length=100, description="工作流名称（业务标识）")
    wf_apikey: str = Field(..., min_length=1, max_length=255, description="工作流 API 密钥")
    wf_explanation: str | None = Field(None, max_length=2000, description="工作流说明，可空")
    wf_var: dict[str, Any] | list[Any] | None = Field(
        None, description="工作流所需变量（JSON 对象/数组），可空"
    )
    created_by: int | None = Field(None, gt=0, description="创建人用户 ID，可空")

    @field_validator(*_REQUIRED_FIELDS)
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空或纯空白字符")
        return v

    @field_validator("wf_explanation")
    @classmethod
    def _normalize_explanation(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class WorkflowInfoUpdate(BaseSchema):
    """更新配置：未传字段保持原值；可选字段传 null 表示清空。"""

    workflow_name: str | None = Field(None, min_length=1, max_length=100)
    wf_apikey: str | None = Field(None, min_length=1, max_length=255)
    wf_explanation: str | None = Field(None, max_length=2000, description="传 null 清空")
    wf_var: dict[str, Any] | list[Any] | None = Field(None, description="传 null 清空")
    updated_by: int | None = Field(None, gt=0, description="最后修改人用户 ID")
    version: int | None = Field(None, ge=1, description="乐观锁版本号，传入时做一致性校验")

    @field_validator(*_REQUIRED_FIELDS)
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("不能为空或纯空白字符")
        return v

    @field_validator("wf_explanation")
    @classmethod
    def _normalize_explanation(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _check_payload(self) -> "WorkflowInfoUpdate":
        provided = self.model_fields_set
        for name in _REQUIRED_FIELDS:
            if name in provided and getattr(self, name) is None:
                raise ValueError(f"{name} 为必填字段，不允许置空")
        if not (provided & WORKFLOW_UPDATABLE_FIELDS):
            raise ValueError("至少需要提供一个待更新字段")
        return self


class WorkflowInfoResponse(ORMModel):
    """单条配置响应（字段名与库表一致）。"""

    id: int = Field(..., description="记录唯一标识（主键）")
    workflow_name: str
    wf_apikey: str = Field(..., description="工作流 API 密钥，默认脱敏")
    wf_explanation: str | None = None
    wf_var: dict[str, Any] | list[Any] | None = None
    is_deleted: bool = False
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: int | None = None
    updated_by: int | None = None
    version: int = 1


# ------------------------------------------------------- 调用请求（阻塞/流式共用）
class WorkflowRequest(BaseSchema):
    """工作流调用请求。"""

    app: str = Field("prompt", min_length=1, description="workflow_info 中登记的工作流名称")
    inputs: dict[str, Any] = Field(..., description="工作流开始节点所需变量")
    user: str = Field("backend", description="终端用户标识")


class ChatRequest(BaseSchema):
    """聊天调用请求。"""

    app: str = Field("prompt", min_length=1, description="workflow_info 中登记的工作流名称")
    query: str = Field(..., min_length=1, description="提问内容")
    user: str = Field("backend", description="终端用户标识")
    conversation_id: str = Field("", description="多轮对话 ID，首次为空")
    inputs: dict[str, Any] = Field(default_factory=dict, description="附加输入变量")
