"""
模块：app/schemas/user.py
职责：用户管理相关请求 / 响应结构。

标签：[架构组] [P1]

实现要点：
- 响应模型不包含 password_hash；
- 管理员创建用户可直接指定角色；普通注册走 auth 接口（默认 employee）；
- mobile / email 在校验层保持原值，脱敏在「非管理员查看」时由 Service 处理。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema, ORMModel


class RoleBrief(ORMModel):
    """角色简要信息。"""

    id: int
    code: str
    name: str


class UserCreate(BaseSchema):
    """管理员创建用户。"""

    username: str = Field(..., min_length=4, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8, max_length=64, description="初始密码，需含字母和数字")
    real_name: str | None = Field(None, max_length=64)
    mobile: str | None = Field(None, pattern=r"^1[3-9]\d{9}$")
    email: str | None = Field(None, max_length=128)
    organization_id: int | None = Field(None, gt=0, description="所属部门 ID")
    position_title: str | None = Field(None, max_length=64, description="岗位/职务")
    is_active: bool = Field(True)
    role_codes: list[str] = Field(default_factory=lambda: ["employee"], description="角色编码列表")

    @field_validator("password")
    @classmethod
    def _check_strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("密码必须同时包含字母和数字")
        return v


class UserUpdate(BaseSchema):
    """更新用户（全部可选，不含密码）。"""

    real_name: str | None = Field(None, max_length=64)
    mobile: str | None = Field(None, pattern=r"^1[3-9]\d{9}$")
    email: str | None = Field(None, max_length=128)
    avatar: str | None = Field(None, max_length=255)
    organization_id: int | None = Field(None, gt=0)
    position_title: str | None = Field(None, max_length=64)


class UserStatusUpdate(BaseSchema):
    """启用 / 禁用用户。"""

    is_active: bool
    reason: str | None = Field(None, max_length=255, description="变更原因（写入审计）")


class AssignRolesRequest(BaseSchema):
    """分配角色。"""

    role_codes: list[str] = Field(..., min_length=1, description="角色编码列表，会覆盖原角色")


class ResetPasswordRequest(BaseSchema):
    """管理员重置密码。"""

    new_password: str = Field(..., min_length=8, max_length=64)

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("密码必须同时包含字母和数字")
        return v


class UserResponse(ORMModel):
    """用户响应（不含任何密码字段）。"""

    id: int
    username: str
    real_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    organization_id: int | None = None
    organization_name: str | None = Field(None, description="所属部门名称（Service 补充）")
    position_title: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    roles: list[RoleBrief] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime | None = None
