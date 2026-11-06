"""
模块：app/ai/agents/base_agent.py
职责：Agent 基类（统一「输入 -> 模型调用 -> 输出解析 -> 落库」骨架）。

标签：[架构组] [P0]（Agent 组 6 个 Agent 的父类）

============================ 实现思路与代码要点 ============================
基类骨架：
    class BaseAgent(ABC):
        name: str
        system_prompt: str

        def __init__(self, db: AsyncSession, user: User | None = None)
        async def build_prompt(self, payload: dict) -> list[ChatMessage]   # 取数据 + 组装提示
        async def parse_output(self, raw: str) -> dict                     # 结构化解析 + 校验
        async def persist(self, result: dict, payload: dict) -> Any        # 写回业务表

        async def run(self, payload: dict) -> dict:
            1) build_prompt -> 2) llm_manager.chat -> 3) parse_output
            4) 失败重试 / 兜底 -> 5) persist -> 6) 记录 token 与耗时 -> 返回

实现要点：
- Agent 通过 Services 取数据（不要直接写 SQL），保证权限一致；
- 输出解析用 ai/utils/output_parser（JSON 容错 + Pydantic 校验）；
- 失败重试最多 N 次（settings.LLM_MAX_RETRIES），仍失败则返回明确的错误结构；
- 每个 Agent 注册到 patterns.registry.agent_registry，便于调度与 Dify 可视化。
=========================================================================
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from app.adapters.llm.base_llm import ChatMessage
from app.ai.context import AIContext, get_context
from app.config.settings import settings
from app.core.exceptions import BusinessException
from app.patterns.registry import agent_registry

logger = logging.getLogger(__name__)


def register_agent(name: str):
    """Agent 注册装饰器：@register_agent("resume")。"""

    def decorator(cls: type[BaseAgent]) -> type[BaseAgent]:
        cls.name = name
        agent_registry.register(name)(cls)
        return cls

    return decorator


class BaseAgent(ABC):
    """Agent 基类。"""

    name: str = "base"
    system_prompt: str = ""
    prompt_name: str | None = None      # 对应 config/prompt_config.py 中的模板名
    purpose: str = "chat"               # 用于 model_selector 选模型
    temperature: float | None = None
    max_tokens: int | None = None
    persist_result: bool = True         # 是否调用 persist（纯问答类可关闭）

    def __init__(self, db: Any | None = None, user: Any | None = None, context: AIContext | None = None) -> None:
        self.db = db
        self.user = user
        self.context = context or get_context()

    # ---------------------------------------------------------------- 子类实现
    @abstractmethod
    async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]:
        """取数据 + 组装提示词（数据访问必须走 Service）。"""

    @abstractmethod
    async def parse_output(self, raw: str) -> dict[str, Any]:
        """解析模型输出为结构化 dict（失败抛 AIError，由 run 重试）。"""

    async def persist(self, result: dict[str, Any], payload: dict[str, Any]) -> Any:
        """写回业务表（默认不落库，子类按需覆盖）。"""
        return None

    # ---------------------------------------------------------------- 主流程
    async def _release_db_before_llm(self) -> None:
        """build_prompt 之后、LLM 调用之前归还数据库连接。

        请求级事务（Unit of Work）下，长耗时 LLM 调用若持有连接会占满连接池
        （并发稍高即耗尽）。release_connection 提交当前事务把连接还回池中；
        session 因 expire_on_commit=False 仍可继续用于 persist，
        后续写入随请求结束由 get_db 再次提交。
        """
        if self.db is None:
            return
        try:
            from app.infrastructure.database.session import release_connection

            await release_connection(self.db)
        except Exception:  # noqa: BLE001 - 归还失败不阻断 AI 调用
            logger.warning("LLM 调用前归还数据库连接失败（继续执行）", exc_info=True)

    def _publish_ai_call(
        self,
        *,
        status: str,
        elapsed_ms: float,
        model: str = "",
        tokens: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """AI 调用审计：发布 EVENT_AI_CALL_FINISHED（AuditService 订阅落库）。"""
        from app.infrastructure.messaging.event_bus import EVENT_AI_CALL_FINISHED, event_bus

        event_bus.publish(
            EVENT_AI_CALL_FINISHED,
            {
                "status": status,
                "agent": self.name,
                "model": model,
                "prompt_tokens": (tokens or {}).get("prompt", 0),
                "completion_tokens": (tokens or {}).get("completion", 0),
                "elapsed_ms": round(elapsed_ms, 2),
                "user_id": self.context.user_id,
                "username": self.context.username,
                "trace_id": self.context.trace_id,
                "error_message": error_message,
            },
        )

    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """标准执行流程：build -> 归还连接 -> chat -> parse -> persist -> 埋点。"""
        from app.ai.core.llm_manager import llm_manager
        from app.ai.utils.output_parser import parse_json

        payload = payload or {}
        started = time.perf_counter()
        attempts = max(1, settings.LLM_MAX_RETRIES + 1)
        last_error: str | None = None
        raw = ""

        # build_prompt 内部会取数据并做参数/权限校验（ParamError / PermissionDeniedError 等），
        # 这些业务异常必须在 run() 契约内返回结构化错误，而不是裸冒泡成 HTTP 500。
        # 注意：只捕获 BusinessException，编程类异常（AttributeError/TypeError 等）仍照常抛出，
        # 避免把代码缺陷伪装成业务错误。
        try:
            messages = await self.build_prompt(payload)
            messages = self._with_system_prompt(messages)
        except BusinessException as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._publish_ai_call(
                status="fail", elapsed_ms=elapsed_ms, error_message=exc.message
            )
            return self._error_result(exc.message, code=exc.code, elapsed_ms=elapsed_ms)
        # 长外部调用前先归还连接，避免一次 LLM 请求占住池连接几十秒（issue：连接池耗尽）
        await self._release_db_before_llm()

        for attempt in range(1, attempts + 1):
            try:
                result = await llm_manager.chat(
                    messages,
                    purpose=self.purpose,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    user_id=self.context.user_id,
                    conversation_id=self.context.conversation_id,
                    trace_id=self.context.trace_id,
                )
                raw = result.content
                parsed = await self.parse_output(raw)
                if not isinstance(parsed, dict):  # 容错：允许 parse_output 返回 JSON 对象
                    parsed = parse_json(raw)
                persisted = None
                if self.persist_result:
                    persisted = await self.persist(parsed, payload)
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._publish_ai_call(
                    status="success",
                    elapsed_ms=elapsed_ms,
                    model=getattr(result, "model", ""),
                    tokens={
                        "prompt": getattr(result, "prompt_tokens", 0),
                        "completion": getattr(result, "completion_tokens", 0),
                    },
                )
                return {
                    "ok": True,
                    "agent": self.name,
                    "result": parsed,
                    "persisted": persisted,
                    "model": getattr(result, "model", ""),
                    "prompt_version": self.prompt_version,
                    "tokens": {
                        "prompt": getattr(result, "prompt_tokens", 0),
                        "completion": getattr(result, "completion_tokens", 0),
                    },
                    "elapsed_ms": round(elapsed_ms, 2),
                    "attempts": attempt,
                }
            except BusinessException as exc:
                # 业务异常（权限 / 参数 / 规则）不重试，直接返回结构化错误
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._publish_ai_call(
                    status="fail", elapsed_ms=elapsed_ms, error_message=exc.message
                )
                return self._error_result(exc.message, code=exc.code, elapsed_ms=elapsed_ms)
            except Exception as exc:  # noqa: BLE001 - 模型/解析异常按次数重试
                # 带异常类型，避免底层异常 str 为空（如部分网络/TLS 故障）时错误信息只剩空串
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Agent %s 第 %s/%s 次执行失败：%s",
                    self.name,
                    attempt,
                    attempts,
                    last_error,
                )
                if attempt >= attempts:
                    break

        elapsed_ms = (time.perf_counter() - started) * 1000
        final_error = last_error or "Agent 执行失败"
        self._publish_ai_call(status="fail", elapsed_ms=elapsed_ms, error_message=final_error)
        return self._error_result(
            final_error,
            code=5002,
            elapsed_ms=elapsed_ms,
            raw=raw[:1000],
        )

    # ---------------------------------------------------------------- 工具
    def _with_system_prompt(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """前置 system 提示词（渲染 prompt_config 模板后注入）。"""
        system_prompt = self.get_system_prompt()
        if not system_prompt:
            return messages
        if messages and messages[0].role == "system":
            return messages
        return [ChatMessage.system(system_prompt), *messages]

    def get_system_prompt(self) -> str:
        """优先用 prompt_config 中的模板，其次用类属性。"""
        if self.prompt_name:
            try:
                from app.config.prompt_config import prompt_registry

                return prompt_registry.get(self.prompt_name)
            except Exception:  # noqa: BLE001 - 模板缺失回退到类属性
                logger.debug("提示词模板缺失：%s，使用类内 system_prompt", self.prompt_name)
        return self.system_prompt

    @property
    def prompt_version(self) -> str:
        from app.config.prompt_config import PROMPT_VERSION

        return PROMPT_VERSION

    @staticmethod
    def _error_result(
        message: str, *, code: int = 5002, elapsed_ms: float = 0.0, raw: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": message,
            "error_code": code,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        if raw:
            payload["raw"] = raw
        return payload

    async def stream(self, payload: dict[str, Any] | None = None):
        """流式执行（只做模型输出，不解析与落库，供 SSE 使用）。"""
        from app.ai.core.llm_manager import llm_manager

        payload = payload or {}
        messages = self._with_system_prompt(await self.build_prompt(payload))
        # 与 run() 相同：长外部调用前归还连接
        await self._release_db_before_llm()
        async for chunk in llm_manager.stream(
            messages,
            purpose=self.purpose,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            user_id=self.context.user_id,
            conversation_id=self.context.conversation_id,
        ):
            yield chunk

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "persist_result": self.persist_result,
        }


__all__ = ["BaseAgent", "register_agent"]
