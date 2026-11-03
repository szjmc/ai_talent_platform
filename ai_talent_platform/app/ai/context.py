"""
模块：app/ai/context.py
职责：AI 调用上下文（把「当前用户 / 权限 / trace_id」透传给 Agent 与 Skill）。

标签：[架构组] [P0]（其余 4 组的公共契约，最先落地）

============================ 实现思路与代码要点 ============================
为什么需要：
- Agent / Skill 在执行过程中需要知道「是谁在问、能看哪些数据」，否则会出现越权；
- 但把 Request / User 对象一路塞进每个函数参数很脏，因此用 contextvars 做请求级上下文。

设计：
    @dataclass AIContext(user_id, username, roles, organization_id, trace_id, conversation_id, extra)
    current_ai_context: ContextVar[AIContext]
    set_context(user=None, **extra) -> AIContext     # 在 Router / Service 入口调用
    get_context() -> AIContext                        # Skill / Agent 内部读取（权限过滤用）
    clear_context()

要点：
- Skill 内部做数据过滤时，必须使用 get_context() 中的角色，禁止信任 LLM 传入的参数；
- 上下文不跨进程，后台任务需要显式重建（从 user_id 查库）；
- 未设置上下文时返回「匿名上下文」而不是抛异常，保证脚本 / 单测可跑。
=========================================================================
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass
class AIContext:
    """AI 调用上下文（身份与权限的唯一可信来源）。"""

    user_id: int | None = None
    username: str | None = None
    roles: list[str] = field(default_factory=list)
    organization_id: int | None = None
    trace_id: str = "-"
    conversation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- 派生属性
    @property
    def is_anonymous(self) -> bool:
        return self.user_id is None

    @property
    def data_scope(self) -> str:
        """数据范围：all / dept_sub / dept / self（多角色取最宽）。"""
        from app.core.permissions import get_data_scope

        return get_data_scope(self.roles)

    @property
    def is_super_admin(self) -> bool:
        from app.core.permissions import is_super_admin

        return is_super_admin(self.roles)

    def has_permission(self, required: str) -> bool:
        """权限判断（统一走 permissions 模块，避免各处自己拼逻辑）。"""
        from app.core.permissions import has_permission

        return has_permission(self.roles, required)

    def require_permission(self, required: str) -> None:
        from app.core.permissions import check_permission

        check_permission(self.roles, required)

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "roles": list(self.roles),
            "organization_id": self.organization_id,
            "data_scope": self.data_scope,
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "extra": dict(self.extra),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AIContext user_id={self.user_id} roles={self.roles} scope={self.data_scope}>"


# 请求级上下文（未设置时为 None，由 get_context() 兜底为匿名上下文）
current_ai_context: ContextVar[AIContext | None] = ContextVar("current_ai_context", default=None)

ANONYMOUS_CONTEXT = AIContext(user_id=None, roles=[], trace_id="-")


def _roles_of(user: Any) -> list[str]:
    """从 User 对象取角色编码（兼容 role_codes 属性 / roles 关系）。"""
    role_codes = getattr(user, "role_codes", None)
    if role_codes:
        return [str(code) for code in role_codes]
    roles = getattr(user, "roles", None) or []
    codes: list[str] = []
    for role in roles:
        code = getattr(role, "code", None)
        if code:
            codes.append(str(code))
    return codes


def set_context(
    user: Any = None,
    *,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    roles: list[str] | None = None,
    organization_id: int | None = None,
    **extra: Any,
) -> AIContext:
    """设置当前上下文（Router / Service / 后台任务入口调用）。

    :param user: User ORM 对象 / dict / None（匿名）
    """
    if isinstance(user, dict):
        user_id = user.get("user_id") or user.get("id")
        username = user.get("username")
        roles = roles or list(user.get("roles") or [])
        organization_id = organization_id if organization_id is not None else user.get("organization_id")
    elif user is not None:
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)
        roles = roles or _roles_of(user)
        organization_id = (
            organization_id if organization_id is not None else getattr(user, "organization_id", None)
        )
    else:
        user_id = None
        username = None

    context = AIContext(
        user_id=user_id,
        username=username,
        roles=list(roles or []),
        organization_id=organization_id,
        trace_id=trace_id or _current_trace_id(),
        conversation_id=conversation_id,
        extra=dict(extra),
    )
    current_ai_context.set(context)
    return context


def get_context() -> AIContext:
    """读取上下文；未设置时返回匿名上下文（不抛异常）。"""
    return current_ai_context.get() or ANONYMOUS_CONTEXT


def update_context(**changes: Any) -> AIContext:
    """在现有上下文上增量更新（如补充 conversation_id）。"""
    current = get_context()
    data = current.to_dict()
    data.pop("data_scope", None)
    data.update({k: v for k, v in changes.items() if v is not None})
    context = AIContext(
        user_id=data.get("user_id"),
        username=data.get("username"),
        roles=list(data.get("roles") or []),
        organization_id=data.get("organization_id"),
        trace_id=data.get("trace_id") or "-",
        conversation_id=data.get("conversation_id"),
        extra=dict(data.get("extra") or {}),
    )
    current_ai_context.set(context)
    return context


def clear_context() -> None:
    """清除上下文（请求结束时调用，避免上下文串到下一个请求）。"""
    current_ai_context.set(None)


def _current_trace_id() -> str:
    try:
        from app.config.logging_config import trace_id_ctx

        return trace_id_ctx.get() or "-"
    except Exception:  # noqa: BLE001 - 日志模块未就绪时不影响主流程
        return "-"


@contextmanager
def context_scope(user: Any = None, **extra: Any) -> Iterator[AIContext]:
    """同步上下文作用域（脚本 / 单测用）。"""
    context = set_context(user, **extra)
    try:
        yield context
    finally:
        clear_context()


@asynccontextmanager
async def async_context_scope(user: Any = None, **extra: Any):
    """异步上下文作用域（与 context_scope 等价，便于 async with 使用）。"""
    context = set_context(user, **extra)
    try:
        yield context
    finally:
        clear_context()


__all__ = [
    "AIContext",
    "ANONYMOUS_CONTEXT",
    "async_context_scope",
    "clear_context",
    "context_scope",
    "current_ai_context",
    "get_context",
    "set_context",
    "update_context",
]
