"""
模块：app/models/dify.py
职责：Dify 工作流配置表 workflow_info（ORM 表映射类）。

标签：[Dify组] [P1]
说明：id / created_at / updated_at / is_deleted / deleted_at /
      created_by / updated_by / version 由 BaseModel 提供，与需求图一致。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class WorkflowInfo(BaseModel):
    """Dify 工作流配置。"""

    __tablename__ = "workflow_info"
    __table_args__ = {"comment": "Dify 工作流配置表"}

    workflow_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="工作流名称（业务标识）"
    )
    wf_apikey: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="工作流 API 密钥"
    )
    wf_explanation: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="工作流说明"
    )
    wf_var: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSON, nullable=True, comment="工作流所需变量（JSON 对象或数组）"
    )
