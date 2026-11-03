"""
模块：app/ai/skills/base.py
职责：Skills 可编排技能组件库体系 —— 技能基类、参数校验、结果封装、权限与上下文适配。

标签：[架构组] [P0]（其他 4 组的公共地基，D1–D2 必须先落地）

============================ 实现思路与代码要点 ============================
设计目标：
1. 一个 Skill = Agent / LLM 可调用的「一个工具」，对外只有三样东西：
     name / description / parameters(JSON Schema) + execute()
2. 编排友好：to_openai_tool() 直接喂给 function-calling；describe() 供
   `GET /system/skills` 之类接口导出技能清单；run() 统一计时、鉴权、兜错，
   返回 SkillResult，Agent 侧不需要 try/except 每一处。
3. 安全第一（架构组 review 硬规则）：
     - 身份与权限只从 AI 上下文取，LLM 传入的 user_id / roles 一律忽略；
     - 参数按 JSON Schema 校验（必填 / 类型 / 枚举 / 默认值 / 上下界），
       未知参数丢弃而不是透传，避免模型幻觉参数污染业务层；
     - execute 内部只允许走 Service / Repository / 适配器，禁止裸 SQL；
     - 失败抛业务异常（ParamError / PermissionDeniedError / AIError），
       由 run() 转成结构化失败结果，绝不返回 None 让上层猜。
4. 依赖未实现 / 未启用（Milvus、Vanna、LLM 等）时不静默降级为「空答案」，
   而是抛 SkillUnavailableError，让 Agent 明确告诉用户「该能力暂不可用」。
=========================================================================
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import (
    AIError,
    BusinessException,
    ParamError,
    PermissionDeniedError,
)
from app.core.permissions import has_permission
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

# JSON Schema type -> Python 类型（bool 单独判定：bool 是 int 的子类）
_SCHEMA_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


# ------------------------------------------------------------------ 异常体系
class SkillError(AIError):
    """技能执行失败（依赖异常、输出不合法等）。"""

    code = 5002
    message = "技能执行失败"


class SkillUnavailableError(SkillError):
    """技能依赖的能力未实现 / 未启用（Milvus、Vanna、LLM、业务 Service 等）。"""

    code = 5002
    message = "该技能依赖的能力暂未实现或未启用"


class SkillParamError(ParamError):
    """技能入参不合法（LLM function-calling 传错参数时的统一出口）。"""

    code = 1001
    message = "技能入参不合法"


class SkillTimeoutError(SkillError):
    """技能执行超时。"""

    code = 5002
    message = "技能执行超时"


def import_dependency(module_path: str, attr: str, hint: str = "") -> Any:
    """延迟导入依赖能力（模块或属性未实现时抛 SkillUnavailableError）。"""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - 模块不存在
        require_dependency(False, f"{module_path}.{attr}", hint=hint)
        raise SkillUnavailableError(f"{module_path} 导入失败：{exc}")
    target = getattr(module, attr, None)
    require_dependency(target is not None, f"{module_path}.{attr}", hint=hint)
    return target


def require_dependency(available: bool, dependency: str, hint: str = "") -> None:
    """依赖不可用时的统一抛错入口（让 Agent 能给出明确原因）。"""
    if available:
        return
    message = f"依赖未就绪：{dependency}"
    if hint:
        message = f"{message}（{hint}）"
    raise SkillUnavailableError(message, data={"dependency": dependency})


# ------------------------------------------------------------------ 上下文适配
@dataclass
class SkillContext:
    """技能执行上下文（身份与权限的唯一可信来源）。

    `app/ai/context.py` 实现后，这里会直接映射它的 AIContext；
    在实现前（或其他进程/后台任务里没有设置上下文时）退化为匿名上下文，
    只读技能按「最小可见范围」处理，不会抛异常中断编排。
    """

    user_id: int | None = None
    roles: list[str] = field(default_factory=list)
    organization_id: int | None = None
    data_scope: str = "self"
    trace_id: str | None = None
    conversation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    anonymous: bool = True

    @property
    def is_super_admin(self) -> bool:
        from app.core.permissions import is_super_admin

        return is_super_admin(self.roles)


def load_skill_context() -> SkillContext:
    """读取当前 AI 上下文；`app/ai/context.py` 未实现时返回匿名上下文。"""
    try:
        from app.ai.context import get_context  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return SkillContext()

    raw = get_context()
    if raw is None:
        return SkillContext()
    return SkillContext(
        user_id=getattr(raw, "user_id", None),
        roles=list(getattr(raw, "roles", None) or []),
        organization_id=getattr(raw, "organization_id", None),
        data_scope=str(getattr(raw, "data_scope", "self") or "self"),
        trace_id=getattr(raw, "trace_id", None),
        conversation_id=getattr(raw, "conversation_id", None),
        extra=dict(getattr(raw, "extra", None) or {}),
        anonymous=getattr(raw, "user_id", None) is None,
    )


# ------------------------------------------------------------------ 执行结果
@dataclass
class SkillResult:
    """技能执行结果（可 JSON 序列化，供 Agent / 审计使用）。"""

    skill: str
    ok: bool
    data: Any = None
    error: str | None = None
    error_code: int | None = None
    elapsed_ms: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "params": self.params,
        }

    def raise_for_error(self) -> SkillResult:
        """失败时抛出对应业务异常（需要「快速失败」的编排链路使用）。"""
        if not self.ok:
            raise SkillError(self.error or "技能执行失败", code=self.error_code or 5002)
        return self

    def __bool__(self) -> bool:
        return self.ok


# ------------------------------------------------------------------ 技能基类
class BaseSkill(ABC):
    """技能组件基类。

    子类必须声明 name / description / parameters，并实现 execute()。
    """

    name: str = "base"
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    # 编排元数据
    category: str = "business"          # retrieval / business / data
    version: str = "1.0.0"
    enabled: bool = True
    requires: tuple[str, ...] = ()      # 依赖说明，仅供清单展示与排障
    required_permission: str | None = None   # 需要的权限码（来自当前上下文角色）
    timeout_seconds: float = 30.0

    # ---------------------------------------------------------------- 参数校验
    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """按 JSON Schema 校验入参：补默认值、校验必填/类型/枚举/上下界、丢弃未知项。"""
        if not isinstance(params, dict):
            raise SkillParamError("技能入参必须是 dict")

        schema = self.parameters or {}
        properties: dict[str, Any] = schema.get("properties", {}) or {}
        required: list[str] = list(schema.get("required", []) or [])
        cleaned: dict[str, Any] = {}

        for key, spec in properties.items():
            if key in params and params[key] is not None:
                cleaned[key] = params[key]
            elif "default" in spec:
                cleaned[key] = spec["default"]

        unknown = [key for key in params if key not in properties]
        if unknown:
            # 模型幻觉参数不报错，只丢弃，保证编排链路不被无关参数打断
            logger.debug("技能 %s 忽略未知参数：%s", self.name, unknown)

        for key in required:
            if key not in cleaned or cleaned[key] is None:
                raise SkillParamError(f"缺少必填参数：{key}", data={"skill": self.name, "param": key})

        for key, value in cleaned.items():
            spec = properties.get(key, {})
            expected = spec.get("type")
            if expected and expected in _SCHEMA_TYPES:
                types = _SCHEMA_TYPES[expected]
                if expected == "integer" and isinstance(value, bool):
                    raise SkillParamError(f"参数 {key} 应为整数", data={"skill": self.name})
                if not isinstance(value, types):
                    raise SkillParamError(
                        f"参数 {key} 类型错误：期望 {expected}，实际 {type(value).__name__}",
                        data={"skill": self.name, "param": key},
                    )
            if "enum" in spec and value not in spec["enum"]:
                raise SkillParamError(
                    f"参数 {key} 取值不合法：{value}，可选 {spec['enum']}",
                    data={"skill": self.name, "param": key},
                )
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = spec.get("minimum")
                maximum = spec.get("maximum")
                if minimum is not None and value < minimum:
                    raise SkillParamError(f"参数 {key} 不能小于 {minimum}", data={"skill": self.name})
                if maximum is not None and value > maximum:
                    raise SkillParamError(f"参数 {key} 不能大于 {maximum}", data={"skill": self.name})
            if isinstance(value, str):
                max_length = spec.get("maxLength")
                if max_length and len(value) > max_length:
                    raise SkillParamError(
                        f"参数 {key} 长度超过 {max_length}", data={"skill": self.name, "param": key}
                    )
        return cleaned

    # ---------------------------------------------------------------- 执行入口
    async def run(self, **params: Any) -> SkillResult:
        """统一执行入口：校验 -> 鉴权 -> 计时 -> 兜错，不抛异常（返回结构化结果）。"""
        started = time.perf_counter()
        try:
            safe_params = self.validate_params(params)
            context = load_skill_context()
            self._check_permission(context)
            data = await asyncio.wait_for(
                self.execute(**safe_params), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            result = SkillResult(
                skill=self.name,
                ok=False,
                error=f"技能执行超时（{self.timeout_seconds}s）",
                error_code=SkillTimeoutError.code,
                params=params,
            )
            logger.warning("技能 %s 执行超时：%s", self.name, exc)
        except BusinessException as exc:
            result = SkillResult(
                skill=self.name,
                ok=False,
                error=exc.message,
                error_code=exc.code,
                params=params,
            )
            logger.warning("技能 %s 执行失败：%s（code=%s）", self.name, exc.message, exc.code)
        except Exception as exc:  # noqa: BLE001 - 兜底：不让单个技能打断 Agent
            result = SkillResult(
                skill=self.name,
                ok=False,
                error=str(exc) or "技能执行异常",
                error_code=SkillError.code,
                params=params,
            )
            logger.exception("技能 %s 执行异常", self.name)
        else:
            result = SkillResult(skill=self.name, ok=True, data=data, params=safe_params)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

    def _check_permission(self, context: SkillContext) -> None:
        """权限只认上下文角色，不信任 LLM 传入的任何身份参数。"""
        if not self.required_permission:
            return
        if has_permission(context.roles, self.required_permission):
            return
        raise PermissionDeniedError(
            f"当前角色无权使用该技能（需要 {self.required_permission}）",
            data={"skill": self.name, "required": self.required_permission},
        )

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """技能本体：返回可 JSON 序列化的 dict / list。"""

    # ---------------------------------------------------------------- 对外描述
    def to_openai_tool(self) -> dict[str, Any]:
        """OpenAI / DashScope function-calling 工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_tool_spec(self) -> dict[str, Any]:
        """通用工具描述（LangChain / LangGraph 侧按此结构包装）。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
            "version": self.version,
        }

    def describe(self) -> dict[str, Any]:
        """技能清单项（供 /system/skills 与管理台展示）。"""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "enabled": self.enabled,
            "parameters": self.parameters,
            "required_permission": self.required_permission,
            "requires": list(self.requires),
            "timeout_seconds": self.timeout_seconds,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name}>"


def get_skill(name: str) -> BaseSkill:
    """按名称取技能实例；未注册抛 NotFoundError。"""
    from app.core.exceptions import NotFoundError

    skill_cls = skill_registry.try_get(name)
    if skill_cls is None:
        raise NotFoundError(f"技能未注册：{name}，可用：{skill_registry.keys()}")
    skill = skill_cls() if isinstance(skill_cls, type) else skill_cls
    return skill


def list_skills(*, enabled_only: bool = False) -> list[BaseSkill]:
    """已注册技能实例列表（按名称排序）。"""
    skills: list[BaseSkill] = []
    for _, skill_cls in skill_registry:
        skill = skill_cls() if isinstance(skill_cls, type) else skill_cls
        if enabled_only and not skill.enabled:
            continue
        skills.append(skill)
    return sorted(skills, key=lambda item: item.name)


def list_tools(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    """供 Agent function-calling 使用的工具描述列表。"""
    return [skill.to_openai_tool() for skill in list_skills(enabled_only=enabled_only)]
