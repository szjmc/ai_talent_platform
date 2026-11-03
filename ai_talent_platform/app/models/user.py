"""
模块：app/models/user.py
职责：用户表 + 角色表 + 用户角色关联表（RBAC 基础，公共层必需）。

标签：[业务组] [P0]（用户表，认证与 AI 上下文身份来源）

============================ 实现思路与代码要点 ============================
表设计：
1. users：账号主体
   - username       登录名（唯一，索引）
   - password_hash  bcrypt 哈希，绝不存明文
   - real_name/mobile/email/avatar  基础资料
   - organization_id  所属部门（外键 -> organizations.id，可空）
   - is_active / is_superuser  状态
   - last_login_at / last_login_ip  登录审计
   - fail_count / locked_until  登录失败锁定
   - 继承 BaseModel：id、created_at、updated_at、is_deleted、version、created_by
2. roles：角色（code 唯一，如 hr/manager/employee）
3. user_roles：用户-角色多对多（联合主键）
实现要点：
- 密码哈希由 core/security.hash_password 生成，Service 层调用；
- 角色通过 relationship 预加载，Token 中冗余 username/roles，避免每次请求查库；
- 逻辑删除用户时把 username 置为 `username#deleted#<id>`，释放唯一键占用。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class User(BaseModel):
    """用户账号。"""

    __tablename__ = "users"
    __table_args__ = {"comment": "用户账号表"}

    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="登录名"
    )
    password_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="密码哈希（bcrypt）"
    )
    real_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="真实姓名")
    mobile: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True, index=True, comment="手机号"
    )
    email: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True, comment="邮箱"
    )
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="头像地址")
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属部门 ID",
    )
    position_title: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="岗位名称/职务"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1"), comment="是否启用"
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"), comment="是否超管"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最后登录时间"
    )
    last_login_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="最后登录 IP"
    )
    fail_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="连续登录失败次数"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="锁定截止时间"
    )

    # 关系：角色（多对多，延迟加载用 selectin 批量加载）
    roles: Mapped[list["Role"]] = relationship(
        "Role",
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
    )

    # ------------------------------------------------------------ 便捷属性
    @property
    def role_codes(self) -> list[str]:
        """角色编码列表（JWT 与权限判断使用）。"""
        return [r.code for r in self.roles]

    @property
    def is_locked(self) -> bool:
        """当前是否处于锁定状态。"""
        return self.locked_until is not None and self.locked_until > datetime.now()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} username={self.username}>"
