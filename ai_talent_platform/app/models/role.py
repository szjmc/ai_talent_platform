"""
模块：app/models/role.py
职责：角色表 + 用户角色关联表（RBAC）。

============================ 实现思路与代码要点 ============================
1. roles：角色定义
   - code 唯一（super_admin / admin / hr / manager / employee）
   - 与 app/core/permissions.py 中的 RoleEnum 保持一致
2. user_roles：用户-角色多对多关联表（联合主键，级联删除）
3. 权限码当前在代码中集中配置（core/permissions.py）；
   若后续需要「后台可视化配置角色权限」，再增加 role_permissions 表：
     role_id + permission_code，PermissionService 读取时优先读库、回落代码常量。
4. 角色删除前必须校验：是否仍有用户绑定（Service 规则）。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Role(TimestampMixin, Base):
    """角色表。"""

    __tablename__ = "roles"
    __table_args__ = {"comment": "角色表"}

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="角色 ID",
    )
    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="角色编码：super_admin/admin/hr/manager/employee",
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名称")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="角色描述")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1"), comment="是否启用"
    )

    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User", secondary="user_roles", back_populates="roles"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role id={self.id} code={self.code}>"


class UserRole(Base):
    """用户角色关联表。"""

    __tablename__ = "user_roles"
    __table_args__ = {"comment": "用户角色关联表"}

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="用户 ID",
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        comment="角色 ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="绑定时间",
    )
