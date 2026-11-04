"""
模块：app/adapters/llm/deepseek_adapter.py
职责：DeepSeek 适配器（OpenAI 兼容协议，支持 deepseek-chat / deepseek-reasoner 等任意模型）。

标签：[架构组] [P0]（内置 provider，模型由配置驱动，不写死单一模型）

============================ 实现思路与代码要点 ============================
- DeepSeek 提供 OpenAI 兼容接口（/chat/completions、/models），因此复用 OpenAIAdapter，
  只补充三件事：默认地址与默认模型、推理模型参数差异、向量能力缺失的显式提示；
- 默认地址 / 模型来自 provider_config.PROVIDER_SPECS["deepseek"]：
      DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
      通用兜底 LLM_BASE_URL / LLM_MODEL（仅当模型名匹配 deepseek-* 时生效）
  DeepSeek 后续新增模型（如 deepseek-v3.2）无需改代码，改 .env 即可：
      LLM_PROVIDER=deepseek
      LLM_MODEL=deepseek-chat
      LLM_MODEL_MAP=chat=deepseek-chat,talent=deepseek-reasoner
- deepseek-reasoner 会额外返回 reasoning_content，且不支持 temperature / top_p 等采样参数，
  这里自动过滤，避免请求 400；返回内容仍以 content 为准，content 为空时回退思维链；
- DeepSeek 不提供 embedding 接口：调用 embed() 会抛 AIError 并提示配置
  LLM_EMBED_PROVIDER / LLM_EMBED_MODEL（例如指向 qwen / openai），不做静默伪造。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Sequence

from app.adapters.llm.base_llm import ChatMessage, LLMResult
from app.adapters.llm.openai_adapter import OpenAIAdapter
from app.adapters.llm.provider_config import PROVIDER_SPECS
from app.core.exceptions import AIError
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

_SPEC = PROVIDER_SPECS["deepseek"]

# deepseek-reasoner 不支持的采样参数（发送会被服务端拒绝）
_REASONER_UNSUPPORTED_OPTIONS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs"}
)


@llm_registry.register("deepseek")
class DeepSeekAdapter(OpenAIAdapter):
    """DeepSeek（OpenAI 兼容协议）适配器。"""

    provider = "deepseek"
    default_model = _SPEC.default_model
    default_base_url = _SPEC.default_base_url
    supports_embedding = False

    # ---------------------------------------------------------------- 对话
    async def chat(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        target_model = str(kwargs.get("model") or self.model)
        if self.is_reasoning_model(target_model):
            if temperature is not None:
                logger.debug("deepseek-reasoner 不支持 temperature，已忽略该参数")
            temperature = None
            dropped = _REASONER_UNSUPPORTED_OPTIONS & set(kwargs)
            if dropped:
                logger.debug("deepseek-reasoner 忽略不支持的参数：%s", sorted(dropped))
            kwargs = {k: v for k, v in kwargs.items() if k not in _REASONER_UNSUPPORTED_OPTIONS}

        result = await super().chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        # 推理模型：content 正常时直接用；极端情况下（只返回思维链）回退到 reasoning_content
        if not result.content:
            reasoning = self.extract_reasoning_content(result)
            if reasoning:
                result.content = reasoning
        return result

    # ---------------------------------------------------------------- 流式
    async def stream(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式输出：推理模型自动过滤不支持的采样参数。"""
        target_model = str(kwargs.get("model") or self.model)
        if self.is_reasoning_model(target_model):
            for key in _REASONER_UNSUPPORTED_OPTIONS:
                kwargs.pop(key, None)
        async for chunk in super().stream(messages, **kwargs):
            yield chunk

    # ---------------------------------------------------------------- 向量
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AIError(
            "DeepSeek 未提供 embedding 接口，请将向量模型指向其他供应商："
            "LLM_EMBED_PROVIDER=qwen（或 openai）并设置 LLM_EMBED_MODEL=text-embedding-v3",
            data={"provider": self.provider, "llm_embed_provider": "qwen/openai"},
        )

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def is_reasoning_model(model: str | None) -> bool:
        """是否为推理模型（deepseek-reasoner 及后续同类命名）。"""
        return "reasoner" in str(model or "").lower()

    @staticmethod
    def extract_reasoning_content(result: LLMResult) -> str:
        """从原始响应中读取 reasoning_content（推理过程）。"""
        raw = result.raw if isinstance(result.raw, dict) else {}
        choices = raw.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("reasoning_content") or "")

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["reasoning_model"] = self.is_reasoning_model(self.model)
        info["embedding_hint"] = "DeepSeek 不提供向量接口，请配置 LLM_EMBED_PROVIDER"
        return info


__all__ = ["DeepSeekAdapter"]
