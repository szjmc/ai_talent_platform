"""
模块：app/schemas/audit.py
职责：审计日志查询的请求 / 响应结构。

标签：[架构组] [P1]（审计查询契约）

契约要点（与 docs/api_document.md §十九 一致）：
- 审计日志**只增不改**，本模块只有读结构，没有写结构；
- 列表返回统一 PageResult，字段与 audit_logs 表一一对应；
- before_data / after_data 已在上游写入时脱敏，此处原样返回。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from app.schemas.common import BaseSchema, ORMModel


class AuditLogResponse(ORMModel):
    """审计日志对外视图。"""

    id: int
    user_id: int | None = Field(None, description="操作人 ID")
    username: str | None = Field(None, description="操作人用户名（冗余，防改名后无法追溯）")
    action: str = Field(..., description="动作：create/update/delete/login/export/ai_call")
    module: str | None = Field(None, description="业务模块")
    target_type: str | None = Field(None, description="操作对象类型")
    target_id: int | None = Field(None, description="操作对象 ID")
    request_method: str | None = Field(None, description="HTTP 方法")
    request_path: str | None = Field(None, description="请求路径")
    request_ip: str | None = Field(None, description="来源 IP")
    user_agent: str | None = Field(None, description="User-Agent")
    trace_id: str | None = Field(None, description="链路追踪 ID")
    before_data: dict[str, Any] | None = Field(None, description="变更前数据（已脱敏）")
    after_data: dict[str, Any] | None = Field(None, description="变更后数据（已脱敏）")
    status: str = Field("success", description="success / fail")
    error_message: str | None = Field(None, description="失败原因")
    created_at: datetime | None = Field(None, description="发生时间")


class AuditStatsResponse(BaseSchema):
    """审计统计（动作分布 + 成功 / 失败比）。"""

    model_config = ConfigDict(extra="ignore")

    total: int = Field(0, description="统计区间内日志总数")
    by_action: dict[str, int] = Field(default_factory=dict, description="按动作分布")
    by_module: dict[str, int] = Field(default_factory=dict, description="按模块分布")
    success: int = Field(0, description="成功条数")
    failed: int = Field(0, description="失败条数")
    fail_rate: float = Field(0.0, description="失败率（0~1）")
    start_at: datetime | None = Field(None, description="统计起点")
    end_at: datetime | None = Field(None, description="统计终点")
