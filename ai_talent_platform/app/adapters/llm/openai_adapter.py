"""
模块：app/adapters/llm/openai_adapter.py
职责：OpenAI 协议适配器（同时兼容 DashScope compatible-mode、DeepSeek、Moonshot 等）。

标签：[架构组] [P0]

============================ 实现思路与代码要点 ============================
- 只用 httpx 直接打 HTTP，不引入 SDK（依赖更少、便于私有化部署）；
- 端点：POST {base_url}/chat/completions、POST {base_url}/embeddings；
- 可重试错误（超时 / 429 / 5xx / 连接失败）经 @ai_retry 指数退避重试；
- 业务错误（401/403/400）直接抛 AIError，不重试；
- token 从 usage 字段读取，缺失时按 0 处理（不伪造数据）。
=========================================================================
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Sequence

import httpx

from app.adapters.llm.base_llm import (
    BaseLLM,
    ChatMessage,
    LLMResponseError,
    LLMResult,
    LLMRetryableError,
)
from app.ai.utils.retry_handler import ai_retry
from app.core.exceptions import AIError
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

# 4xx 中可重试的只有 429（限流），其余视为请求错误，不重试
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@llm_registry.register("openai")
class OpenAIAdapter(BaseLLM):
    """OpenAI Chat Completions 协议适配器。"""

    provider = "openai"
    default_model = "gpt-4o-mini"
    default_base_url = "https://api.openai.com/v1"

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ---------------------------------------------------------------- chat
    @ai_retry()
    async def chat(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": self.normalize_messages(messages),
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(self.merge_options(**kwargs))

        data = await self._post("/chat/completions", payload)
        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseError(f"模型未返回内容：{str(data)[:300]}")
        try:
            choice = choices[0]
            content = choice.get("message", {}).get("content") or ""
        except (IndexError, AttributeError, TypeError) as exc:
            raise LLMResponseError(f"模型返回结构异常：{str(data)[:300]}") from exc
        usage = data.get("usage") or {}
        return LLMResult(
            content=content,
            model=data.get("model") or payload["model"],
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )

    # ---------------------------------------------------------------- stream
    async def stream(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """SSE 流式输出（逐块产出增量文本）。"""
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": self.normalize_messages(messages),
            "stream": True,
        }
        payload.update(self.merge_options(**kwargs))
        url = f"{self.base_url}/chat/completions"
        started = time.perf_counter()
        async with self.client() as client:
            async with client.stream("POST", url, json=payload) as response:
                await self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        import json

                        delta = json.loads(chunk)["choices"][0].get("delta", {})
                    except Exception:  # noqa: BLE001 - 单点脏数据不影响整体流
                        continue
                    if delta.get("content"):
                        yield delta["content"]
        logger.debug("stream 完成，耗时 %.0fms", (time.perf_counter() - started) * 1000)

    # ---------------------------------------------------------------- embed
    @ai_retry()
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {
            "model": self.options.get("embed_model") or self.model,
            "input": list(texts),
        }
        data = await self._post("/embeddings", payload)
        items = data.get("data") or []
        vectors: list[list[float]] = []
        for item in items:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not vector:
                raise LLMResponseError("向量接口返回为空，请确认模型是否为 embedding 模型")
            vectors.append([float(x) for x in vector])
        if len(vectors) != len(texts):
            raise LLMResponseError(f"向量数量不匹配：期望 {len(texts)}，实际 {len(vectors)}")
        return vectors

    # ---------------------------------------------------------------- 内部
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        started = time.perf_counter()
        async with self.client() as client:
            response = await client.post(url, json=payload)
            await self._raise_for_status(response)
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMResponseError(f"模型返回不是 JSON：{response.text[:300]}") from exc
        logger.debug(
            "LLM %s %s 耗时 %.0fms",
            self.provider,
            path,
            (time.perf_counter() - started) * 1000,
        )
        return data

    async def _raise_for_status(self, response: httpx.Response) -> None:
        """状态码 -> 平台异常：4xx 业务错误不重试，429/5xx 可重试。"""
        if response.status_code < 400:
            return
        body = response.text[:500]
        message = f"大模型调用失败（HTTP {response.status_code}）：{body}"
        if response.status_code in (401, 403):
            raise AIError(f"{message}（请检查 LLM_API_KEY 与模型权限）", data={"status": response.status_code})
        if response.status_code in _RETRYABLE_STATUS:
            raise LLMRetryableError(message, data={"status": response.status_code})
        raise AIError(message, data={"status": response.status_code})


__all__ = ["OpenAIAdapter"]
