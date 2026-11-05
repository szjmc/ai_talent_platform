"""
模块：app/adapters/llm/ollama_adapter.py
职责：本地 Ollama 适配器（私有化 / 离线场景）。

标签：[架构组] [P2]（本地模型，默认不启用）

============================ 实现思路与代码要点 ============================
- 端点：POST {base_url}/api/chat、POST {base_url}/api/embeddings、GET {base_url}/api/tags；
- 与 OpenAI 协议不同，Ollama 用 prompt_eval_count / eval_count 统计 token；
- 不需要 API Key；未启动 Ollama 时调用会抛 LLMRetryableError（由上层降级或提示）。
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
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)


@llm_registry.register("ollama")
class OllamaAdapter(BaseLLM):
    """Ollama 本地模型适配器。"""

    provider = "ollama"
    default_model = "qwen2.5:7b"
    default_base_url = "http://127.0.0.1:11434"

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
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        options.update(self.merge_options(**kwargs))
        if options:
            payload["options"] = options

        started = time.perf_counter()
        data = await self._post("/api/chat", payload)
        message = data.get("message") or {}
        content = message.get("content") or ""
        return LLMResult(
            content=content,
            model=data.get("model") or payload["model"],
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            elapsed_ms=self.timed(started),
            finish_reason="stop" if data.get("done") else None,
            raw=data,
        )

    # ---------------------------------------------------------------- stream
    async def stream(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": self.normalize_messages(messages),
            "stream": True,
        }
        if self.options:
            payload["options"] = dict(self.options)
        url = f"{self.base_url}/api/chat"
        async with self.client() as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    raise LLMRetryableError(f"Ollama 流式调用失败（HTTP {response.status_code}）")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        import json

                        chunk = json.loads(line)
                    except ValueError:  # noqa: BLE001
                        continue
                    delta = (chunk.get("message") or {}).get("content")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        break

    # ---------------------------------------------------------------- embed
    @ai_retry()
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        async with self.client() as client:
            for text in texts:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.options.get("embed_model") or self.model, "prompt": text},
                )
                if response.status_code >= 400:
                    raise LLMRetryableError(f"Ollama 向量接口失败（HTTP {response.status_code}）")
                try:
                    vector = response.json().get("embedding") or []
                except ValueError as exc:
                    raise LLMResponseError("Ollama 向量返回不是 JSON") from exc
                if not vector:
                    raise LLMResponseError("Ollama 向量返回为空，请确认模型支持 embedding")
                vectors.append([float(x) for x in vector])
        return vectors

    # ---------------------------------------------------------------- 内部
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.client() as client:
            try:
                response = await client.post(f"{self.base_url}{path}", json=payload)
            except httpx.HTTPError as exc:
                raise LLMRetryableError(f"无法连接 Ollama（{self.base_url}）：{exc}") from exc
            if response.status_code >= 400:
                raise LLMRetryableError(
                    f"Ollama 调用失败（HTTP {response.status_code}）：{response.text[:300]}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise LLMResponseError("Ollama 返回不是 JSON") from exc

    async def health(self) -> dict[str, Any]:
        """本机 Ollama 是否可用（返回已安装模型列表）。"""
        async with self.client() as client:
            try:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code >= 400:
                    return {"ok": False, "reason": f"HTTP {response.status_code}"}
                models = [item.get("name") for item in response.json().get("models", [])]
            except httpx.HTTPError as exc:
                return {"ok": False, "reason": str(exc)}
        return {"ok": True, "models": models, "current_model": self.model}


__all__ = ["OllamaAdapter"]
