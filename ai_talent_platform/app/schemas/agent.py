"""
模块：app/schemas/agent.py
职责：Agent 总路由 / 对话 / 任务派发的请求与响应模型。

标签：[Agent组] [P1]

设计要点：
1. 请求模型继承 BaseSchema（extra=forbid + str_strip_whitespace），响应模型继承 ORMModel；
2. conversation_id 对外统一为字符串数字（前端原样回传），内部转 int 主键；
3. 任务请求的 "async" 是 Python 关键字 → 字段名 is_async + alias="async"（populate_by_name）；
4. usage 结构与 SSE done 事件保持一致：prompt_tokens / completion_tokens / model。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from app.schemas.common import BaseSchema, ORMModel

__all__ = [
    "AgentChatRequest",
    "AgentChatResponse",
    "AgentConversationResponse",
    "AgentMessageResponse",
    "AgentTaskRequest",
    "AgentTaskResponse",
    "TokenUsage",
]


class TokenUsage(ORMModel):
    """token 用量（与 SSE done 事件 usage 结构一致）。"""

    prompt_tokens: int = Field(0, ge=0, description="输入 token 数")
    completion_tokens: int = Field(0, ge=0, description="输出 token 数")
    model: str = Field("", description="实际使用的模型")


class AgentChatRequest(BaseSchema):
    """对话请求（POST /agents/chat 与 /agents/chat/stream 共用）。"""

    agent_type: str = Field(
        "assistant",
        max_length=32,
        description="auto/assistant=总路由自动分派；指定具体类型则强制该 Agent",
    )
    conversation_id: str | int | None = Field(
        None, description="会话 ID（字符串数字）；首次对话传空由服务端创建"
    )
    message: str = Field(..., min_length=1, max_length=8000, description="用户消息")
    stream: bool = Field(False, description="是否流式（仅 /chat/stream 生效）")


class AgentChatResponse(ORMModel):
    """对话响应。"""

    conversation_id: str = Field(..., description="会话 ID")
    message_id: int | None = Field(None, description="助手回复消息 ID")
    answer: str = Field("", description="回答正文")
    agent: str = Field("", description="实际执行的 Agent")
    route_reason: str = Field("", description="路由依据：explicit/keyword:*/llm/default")
    citations: list[dict[str, Any]] = Field(default_factory=list, description="引用来源")
    usage: TokenUsage = Field(default_factory=TokenUsage, description="token 用量")
    degraded: bool = Field(False, description="是否降级通用应答")
    elapsed_ms: float = Field(0.0, ge=0, description="总耗时（毫秒）")
    report: dict[str, Any] | None = Field(None, description="结构化报告（analytics 等 Agent 返回）")


class AgentConversationResponse(ORMModel):
    """会话摘要。"""

    id: int
    title: str = ""
    agent_type: str = "assistant"
    status: str = "active"
    message_count: int = 0
    last_message_at: datetime | None = None
    created_at: datetime | None = None


class AgentMessageResponse(ORMModel):
    """会话消息。"""

    id: int
    role: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_name: str = ""
    elapsed_ms: float = 0.0
    created_at: datetime | None = None


class AgentTaskRequest(BaseSchema):
    """任务派发请求（POST /agents/tasks）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    agent_type: str = Field(..., max_length=32, description="任务型 Agent：resume/talent/matching/assessment/training")
    payload: dict[str, Any] = Field(default_factory=dict, description="任务负载（如 {file_id: 12}）")
    is_async: bool = Field(False, alias="async", description="true=立即返回 task_id，后台执行")


class AgentTaskResponse(ORMModel):
    """任务状态（提交与查询共用）。"""

    task_id: str
    agent_type: str
    status: str = Field("pending", description="pending/running/success/failed")
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_ms: float = 0.0
