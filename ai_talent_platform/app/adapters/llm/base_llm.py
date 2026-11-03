"""
模块：app/adapters/llm/base_llm.py
职责：大模型统一接口（所有模型适配器的抽象基类）。

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

子类：openai_adapter（兼容 DashScope compatible-mode）、qwen_adapter、ollama_adapter
实现要点：
- 统一重试：ai/utils/retry_handler.py（指数退避 + 超时）；
- 统一异常：core/exceptions.AIError / ExternalServiceError；
- token 统计写 audit 或 conversation 表，用于成本分析。
=========================================================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

# TODO(负责人)：按上述设计实现 BaseLLM、ChatMessage、LLMResult。
