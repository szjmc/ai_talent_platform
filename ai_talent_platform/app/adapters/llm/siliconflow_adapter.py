"""
标签：[架构组] [P1]
模块：app/adapters/llm/siliconflow_adapter.py
职责：硅基流动（SiliconFlow）线上大模型适配器（OpenAI 兼容协议）。

============================ 实现思路与代码要点 ============================
硅基流动提供 OpenAI 兼容端点（POST {base_url}/chat/completions、/embeddings），
因此直接复用 OpenAIAdapter 的协议实现，只覆盖默认地址与默认模型，避免两套 HTTP 代码漂移。

- 默认 base_url：https://api.siliconflow.cn/v1
- 默认对话模型：Qwen/Qwen2.5-72B-Instruct（中文结构化输出稳定、成本低）
- 备用对话模型：Qwen/Qwen2.5-7B-Instruct（更快更省，可在降级链中使用）
- 默认向量模型：BAAI/bge-large-zh-v1.5（维度 1024，需与 Milvus 集合一致）

接入方式（.env）：
    LLM_PROVIDER=siliconflow
    LLM_API_KEY=sk-xxxxxxxxxxxx          # 硅基流动控制台 -> API 密钥
    LLM_MODEL=Qwen/Qwen2.5-72B-Instruct
    LLM_BASE_URL=https://api.siliconflow.cn/v1
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.llm.openai_adapter import OpenAIAdapter
from app.config.settings import settings
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

# 硅基流动向量模型默认维度（bge-large-zh-v1.5 = 1024）
DEFAULT_EMBED_MODEL = "BAAI/bge-large-zh-v1.5"
DEFAULT_EMBED_DIM = 1024


@llm_registry.register("siliconflow")
class SiliconFlowAdapter(OpenAIAdapter):
    """硅基流动（SiliconFlow）OpenAI 兼容适配器。"""

    provider = "siliconflow"
    default_model = "Qwen/Qwen2.5-72B-Instruct"
    default_base_url = "https://api.siliconflow.cn/v1"

    def __init__(self, model: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", self.default_base_url)
        # embed_model 默认用硅基流动中文向量模型，chat 仍用对话模型
        kwargs.setdefault("embed_model", DEFAULT_EMBED_MODEL)
        super().__init__(model or self.default_model, **kwargs)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """向量化：强制走 embedding 模型（避免误用对话模型名导致 400）。"""
        self.options["embed_model"] = self.options.get("embed_model") or DEFAULT_EMBED_MODEL
        return await super().embed(texts)

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(settings.LLM_API_KEY or self.api_key)


__all__ = ["DEFAULT_EMBED_DIM", "DEFAULT_EMBED_MODEL", "SiliconFlowAdapter"]
