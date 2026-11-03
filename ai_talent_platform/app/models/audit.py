"""
模块：app/models/audit.py
职责：操作审计日志表（安全合规必需）。

============================ 实现思路与代码要点 ============================
表设计：audit_logs
- user_id、username、action(create/update/delete/login/export/ai_call)、
  module(talent/position/...)、target_type、target_id、
  request_method、request_path、request_ip、user_agent、trace_id、
  before_data(JSON)、after_data(JSON)、status(success/fail)、error_message、created_at
关键思路：
- 只增不改：不提供 update/delete 接口，按月归档；
- 敏感字段（密码、身份证）入库前必须脱敏；
- AI 调用也记审计（action=ai_call），记录 model、token 消耗、耗时；
- 写入审计不要阻塞主流程：可在 Service 中异步写入（事件总线/后台任务）。
已完成：
- [x] 模型字段与索引（user_id + created_at、module + action、trace_id、created_at）
待实现清单：
- [ ] AuditRepository：list_with_filters、count_by_action
- [ ] AuditService：log(...) 统一入口 + 中间件自动记录关键接口
=========================================================================
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AuditLog(BaseModel):
    """操作审计日志（只增不改，按月归档）。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_id_created_at", "user_id", "created_at"),
        Index("ix_audit_logs_module_action", "module", "action"),
        {"comment": "操作审计日志表"},
    )

    # 逻辑外键：审计日志需长期保留（按月归档），不能因用户删除而丢失，故不建物理外键。
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="操作人 ID（逻辑外键 -> users.id，审计保留需要）",
    )
    username: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="冗余用户名（防用户改名后无法追溯）"
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="动作：create/update/delete/login/export/ai_call"
    )
    module: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="模块：talent/position/..."
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="操作对象类型")
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作对象 ID")
    request_method: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="请求方法")
    request_path: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="请求路径")
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="客户端 IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="User-Agent")
    trace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="链路追踪 ID"
    )
    before_data: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="变更前数据（敏感字段脱敏）"
    )
    after_data: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="变更后数据（敏感字段脱敏）"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="success",
        server_default=text("'success'"),
        comment="状态：success/fail",
    )
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="失败原因")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog id={self.id} action={self.action} module={self.module}>"
