"""
模块：app/adapters/llm/qwen_adapter.py
职责：通义千问适配器（DashScope OpenAI 兼容模式）。

标签：[架构组] [P0]（默认 provider）

============================ 实现思路与代码要点 ============================
- DashScope 提供 OpenAI 兼容端点，因此直接复用 OpenAIAdapter 的协议实现，
  只覆盖默认地址与默认模型，避免两套 HTTP 代码漂移；
- 默认 base_url：https://dashscope.aliyuncs.com/compatible-mode/v1
- 默认模型：qwen-plus；向量模型：text-embedding-v3（维度 1024，需与 Milvus 集合一致）。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.llm.openai_adapter import OpenAIAdapter
from app.config.settings import settings
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

# 通义向量模型默认维度（text-embedding-v3 可指定 1024/768/512）
DEFAULT_EMBED_MODEL = "text-embedding-v3"
DEFAULT_EMBED_DIM = 1024


@llm_registry.register("qwen")
class QwenAdapter(OpenAIAdapter):
    """通义千问（DashScope compatible-mode）适配器。"""

    provider = "qwen"
    default_model = "qwen-plus"
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, model: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", self.default_base_url)
        # embed_model 默认用向量模型，chat 仍用对话模型
        kwargs.setdefault("embed_model", DEFAULT_EMBED_MODEL)
        super().__init__(model or self.default_model, **kwargs)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """向量化：强制走 embedding 模型（避免误用对话模型名导致 400）。"""
        self.options["embed_model"] = self.options.get("embed_model") or DEFAULT_EMBED_MODEL
        return await super().embed(texts)

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(settings.LLM_API_KEY or self.api_key)


__all__ = ["DEFAULT_EMBED_DIM", "DEFAULT_EMBED_MODEL", "QwenAdapter"]
