"""
模块：app/schemas/auth.py
职责：认证相关请求 / 响应结构（注册、登录、刷新令牌、修改密码、当前用户）。

实现要点：
- 密码强度在 Schema 层校验（字母 + 数字，8-64 位），提示语为中文；
- 注册请求不允许出现 roles / is_superuser 字段（extra="forbid" 会自动拒绝），防止越权；
- 响应模型绝不包含 password_hash。
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema, ORMModel


class RegisterRequest(BaseSchema):
    """注册请求。"""

    username: str = Field(
        ...,
        min_length=4,
        max_length=32,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="登录名，4-32 位字母、数字或下划线",
        examples=["zhangsan"],
    )
    password: str = Field(..., min_length=8, max_length=64, description="密码，需同时包含字母和数字")
    real_name: str | None = Field(None, max_length=64, description="真实姓名")
    mobile: str | None = Field(None, pattern=r"^1[3-9]\d{9}$", description="手机号")
    email: str | None = Field(None, max_length=128, description="邮箱")

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("密码必须同时包含字母和数字")
        return v


class LoginRequest(BaseSchema):
    """登录请求。"""

    username: str = Field(..., min_length=1, max_length=64, description="登录名")
    password: str = Field(..., min_length=1, max_length=64, description="密码")
    remember_me: bool = Field(False, description="记住我（延长 access_token 有效期 7 天）")


class TokenResponse(ORMModel):
    """令牌响应。"""

    access_token: str = Field(..., description="访问令牌，放入 Authorization: Bearer <token>")
    refresh_token: str = Field(..., description="刷新令牌，用于换取新的 access_token")
    token_type: str = Field("bearer", description="令牌类型")
    expires_in: int = Field(..., description="access_token 有效期（秒）")


class RefreshTokenRequest(BaseSchema):
    """刷新令牌请求。"""

    refresh_token: str = Field(..., description="登录时返回的 refresh_token")


class ChangePasswordRequest(BaseSchema):
    """修改密码请求。"""

    old_password: str = Field(..., description="原密码")
    new_password: str = Field(..., min_length=8, max_length=64, description="新密码")
    confirm_password: str = Field(..., description="确认新密码")

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("新密码必须同时包含字母和数字")
        return v


class LoginUserInfo(ORMModel):
    """当前登录用户信息（前端据此渲染菜单）。"""

    id: int
    username: str
    real_name: str | None = None
    avatar: str | None = None
    organization_id: int | None = None
    organization_name: str | None = None
    position_title: str | None = None
    is_superuser: bool = False
    roles: list[str] = Field(default_factory=list, description="角色编码列表")
    permissions: list[str] = Field(default_factory=list, description="权限码列表（超管为 ['*']）")
