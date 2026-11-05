"""
模块：app/adapters/llm/base_llm.py
职责：大模型统一接口（所有模型适配器的抽象基类）。

标签：[架构组] [P0]（其余 4 组的公共契约）

============================ 实现思路与代码要点 ============================
为什么需要抽象：
- 业务代码（Service / Agent）只依赖 BaseLLM，切换模型（通义 -> GPT -> 本地 Ollama）不改业务；
- 统一的入参/出参结构，便于统一埋点（token、耗时）与重试。

接口设计：
    class BaseLLM(ABC):
        async def chat(self, messages: list[ChatMessage], *, temperature, max_tokens) -> LLMResult
        async def generate(self, prompt: str, **kwargs) -> LLMResult
        async def stream(self, messages, ...) -> AsyncIterator[str]     # SSE 用
        async def embed(self, texts: list[str]) -> list[list[float]]     # 向量化

数据结构：
    ChatMessage   {role: system/user/assistant, content: str}
    LLMResult     {content, model, prompt_tokens, completion_tokens, elapsed_ms, raw}

子类：openai_adapter（兼容 DashScope compatible-mode）、qwen_adapter、
      deepseek_adapter（OpenAI 兼容协议）、ollama_adapter；模型由 provider_config / settings 决定
实现要点：
- 统一重试：ai/utils/retry_handler.py（指数退避 + 超时）；
- 统一异常：core.exceptions.AIError / ExternalServiceError；
- token 统计写 audit 或 conversation 表，用于成本分析（由 LLMManager 统一记录）。
=========================================================================
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Sequence

import httpx

from app.adapters.llm.provider_config import (
    get_provider_spec,
    resolve_api_key,
    resolve_base_url,
    resolve_model,
)
from app.config.settings import settings
from app.core.exceptions import AIError, ExternalServiceError

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 数据结构
@dataclass
class ChatMessage:
    """对话消息（与 OpenAI / 通义 / Ollama 的 role 语义一致）。"""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role="assistant", content=content)

    @classmethod
    def tool(cls, content: str) -> "ChatMessage":
        return cls(role="tool", content=content)

    @classmethod
    def from_any(cls, item: Any) -> "ChatMessage":
        """兼容 dict / 元组 / 对象三种写法，便于业务层随便传。"""
        if isinstance(item, ChatMessage):
            return item
        if isinstance(item, dict):
            return cls(role=str(item.get("role", "user")), content=str(item.get("content", "")))
        if isinstance(item, (tuple, list)) and len(item) == 2:
            return cls(role=str(item[0]), content=str(item[1]))
        role = getattr(item, "role", "user")
        content = getattr(item, "content", "")
        return cls(role=str(role), content=str(content))

    def __str__(self) -> str:  # pragma: no cover - 调试输出
        return f"[{self.role}] {self.content[:80]}"


@dataclass
class LLMResult:
    """统一的模型输出（含 token 与耗时，便于成本分析）。"""

    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: float = 0.0
    finish_reason: str | None = None
    raw: Any = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "finish_reason": self.finish_reason,
        }

    def __bool__(self) -> bool:
        return bool(self.content)


# ------------------------------------------------------------------ 异常
class LLMRetryableError(ExternalServiceError):
    """可重试的模型调用错误（超时、429、5xx、连接失败）。"""

    code = 5001
    message = "大模型调用失败，可重试"


class LLMResponseError(AIError):
    """模型返回了无法解析的内容。"""

    code = 5002
    message = "大模型返回内容无法解析"


# ------------------------------------------------------------------ 基类
class BaseLLM(ABC):
    """大模型适配器基类。"""

    provider: str = "base"
    default_model: str = ""
    default_base_url: str = ""
    supports_stream: bool = True
    supports_embedding: bool = True

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        **options: Any,
    ) -> None:
        # 模型 / 密钥 / 地址统一走 provider_config：
        # 显式参数 > 供应商专属配置 > 通用配置 > 目录默认值（不把任何厂商写死在这里）。
        spec = get_provider_spec(self.provider)
        if spec is not None:
            self.model = resolve_model(self.provider, model) or self.default_model
            self.base_url = (resolve_base_url(self.provider, base_url) or self.default_base_url).rstrip("/")
        else:
            # 自定义供应商（仅注册了适配器类）：以类属性默认值为准，LLM_* 作为兜底
            self.model = model or self.default_model or settings.LLM_MODEL
            self.base_url = (base_url or self.default_base_url or settings.LLM_BASE_URL).rstrip("/")
        self.api_key = resolve_api_key(self.provider, api_key)
        self.timeout = float(timeout or settings.LLM_TIMEOUT)
        self.max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self.options: dict[str, Any] = dict(options)

    # ---------------------------------------------------------------- 抽象方法
    @abstractmethod
    async def chat(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        """多轮对话补全。"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化（维度需与 Milvus 集合一致）。"""

    # ---------------------------------------------------------------- 默认实现
    async def generate(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> LLMResult:
        """单轮文本生成（内部转成 chat）。"""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage.system(system))
        messages.append(ChatMessage.user(prompt))
        return await self.chat(messages, **kwargs)

    async def stream(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式输出；子类可覆盖，默认退化为一次性返回。"""
        result = await self.chat(messages, **kwargs)
        yield result.content

    # ---------------------------------------------------------------- 工具方法
    @staticmethod
    def normalize_messages(
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        """统一消息结构，便于各适配器直接组请求体。"""
        return [ChatMessage.from_any(item).to_dict() for item in messages or []]

    def client(self) -> httpx.AsyncClient:
        """统一的 HTTP 客户端（超时与鉴权头由适配器自行补充）。"""
        return httpx.AsyncClient(timeout=self.timeout, headers=self.default_headers())

    def default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def merge_options(self, **kwargs: Any) -> dict[str, Any]:
        """合并实例默认参数与单次调用参数（单次优先）。"""
        merged = dict(self.options)
        merged.update({k: v for k, v in kwargs.items() if v is not None})
        return merged

    def timed(self, started: float) -> float:
        return (time.perf_counter() - started) * 1000

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "supports_stream": self.supports_stream,
            "supports_embedding": self.supports_embedding,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} model={self.model} base_url={self.base_url}>"


def messages_to_dict(messages: Iterable[Any]) -> list[dict[str, str]]:
    """模块级便捷函数。"""
    return [ChatMessage.from_any(item).to_dict() for item in messages or []]


__all__ = [
    "BaseLLM",
    "ChatMessage",
    "LLMResponseError",
    "LLMResult",
    "LLMRetryableError",
    "messages_to_dict",
]
