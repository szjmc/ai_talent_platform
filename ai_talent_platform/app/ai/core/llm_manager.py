"""
模块：app/ai/core/llm_manager.py
职责：大模型统一管理（单例、按用途取模型、统一埋点）。

标签：[架构组] [P0]（其余 4 组的公共门面）

============================ 实现思路与代码要点 ============================
1. 根据 settings.LLM_PROVIDER 通过 llm_registry 创建适配器实例，进程内单例；
2. 对外 API：
     get_llm(purpose: str = "default") -> BaseLLM
     chat(messages, purpose="chat") -> LLMResult
     embed(texts) -> list[list[float]]
3. purpose 与模型映射来自 model_selector；
4. 每次调用统一记录：model、tokens、耗时 -> token_manager；
5. 异常统一转换 ExternalServiceError / AIError，并按降级链重试一次备用模型。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Iterable, Sequence

from app.adapters.llm.base_llm import BaseLLM, ChatMessage, LLMResult
from app.ai.core.model_selector import fallback_models, select_embedding_model, select_model
from app.ai.core.token_manager import token_manager
from app.config.settings import settings
from app.core.exceptions import AIError
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

# 模块导入即触发适配器注册（各适配器文件底部 @llm_registry.register）
_ADAPTER_MODULES = (
    "app.adapters.llm.openai_adapter",
    "app.adapters.llm.qwen_adapter",
    "app.adapters.llm.ollama_adapter",
)


def _ensure_adapters_registered() -> None:
    import importlib

    for module in _ADAPTER_MODULES:
        if module.split(".")[-1].replace("_adapter", "") in llm_registry:
            continue
        try:
            importlib.import_module(module)
        except ImportError as exc:  # pragma: no cover - 依赖缺失时才发生
            logger.warning("LLM 适配器导入失败：%s（%s）", module, exc)


class LLMManager:
    """大模型统一管理（进程内单例）。"""

    _instance: "LLMManager | None" = None

    def __new__(cls) -> "LLMManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._providers = {}
            cls._instance._breakers = {}
        return cls._instance

    # ---------------------------------------------------------------- 适配器
    def get_llm(self, purpose: str = "default", provider: str | None = None) -> BaseLLM:
        """按用途取适配器实例（同 provider + model 复用同一个实例）。"""
        _ensure_adapters_registered()
        provider = provider or settings.LLM_PROVIDER
        model = select_model(purpose)
        cache_key = f"{provider}:{model}"
        cached = self._providers.get(cache_key)
        if cached is not None:
            return cached

        adapter_cls = llm_registry.try_get(provider)
        if adapter_cls is None:
            raise AIError(
                f"未注册的 LLM provider：{provider}，可用：{llm_registry.keys()}",
                data={"available": llm_registry.keys()},
            )
        instance = adapter_cls(model=model)
        self._providers[cache_key] = instance
        logger.info("创建 LLM 适配器：provider=%s model=%s purpose=%s", provider, model, purpose)
        return instance

    def get_embedding_llm(self) -> BaseLLM:
        """取向量模型适配器（模型名来自 model_selector）。"""
        _ensure_adapters_registered()
        return self.get_llm(purpose="embed")

    # ---------------------------------------------------------------- 对话
    async def chat(
        self,
        messages: Sequence[Any],
        purpose: str = "chat",
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        user_id: int | None = None,
        conversation_id: str | None = None,
        trace_id: str | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        """统一对话入口：选模型 -> 调用 -> 埋点 -> 失败按降级链重试一次。"""
        from app.ai.core.model_selector import select_temperature

        llm = self.get_llm(purpose)
        target_model = model or select_model(purpose)
        temp = temperature if temperature is not None else select_temperature(purpose)
        normalized = [ChatMessage.from_any(item) for item in messages or []]

        try:
            result = await llm.chat(
                normalized,
                model=target_model,
                temperature=temp,
                max_tokens=max_tokens,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - 需要按降级链兜底
            fallback = self._next_fallback(target_model)
            if fallback is None:
                raise self._wrap_error(exc)
            logger.warning("模型 %s 调用失败，降级到 %s：%s", target_model, fallback, exc)
            try:
                result = await llm.chat(
                    normalized,
                    model=fallback,
                    temperature=temp,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                result.model = fallback
            except Exception as second_exc:  # noqa: BLE001
                raise self._wrap_error(second_exc) from exc

        await self._record(result, purpose, user_id, conversation_id, trace_id)
        return result

    async def generate(self, prompt: str, *, system: str | None = None, purpose: str = "chat", **kwargs: Any) -> LLMResult:
        """单轮生成（内部转成 chat）。"""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage.system(system))
        messages.append(ChatMessage.user(prompt))
        return await self.chat(messages, purpose=purpose, **kwargs)

    async def stream(
        self,
        messages: Sequence[Any],
        purpose: str = "chat",
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式输出（SSE）；底层适配器不支持时自动退化为一次性输出。"""
        llm = self.get_llm(purpose)
        normalized = [ChatMessage.from_any(item) for item in messages or []]
        async for chunk in llm.stream(normalized, **kwargs):
            yield chunk

    # ---------------------------------------------------------------- 向量
    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """文本向量化（维度需与 Milvus 集合一致）。"""
        if not texts:
            return []
        llm = self.get_embedding_llm()
        target_model = model or select_embedding_model()
        try:
            return await llm.embed(list(texts))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc

    # ---------------------------------------------------------------- 运维
    def status(self) -> dict[str, Any]:
        """当前 LLM 运行状态（供 /system/info 的 ai_ready 使用）。"""
        _ensure_adapters_registered()
        return {
            "provider": settings.LLM_PROVIDER,
            "default_model": settings.LLM_MODEL,
            "api_key_configured": bool(settings.LLM_API_KEY),
            "base_url": settings.LLM_BASE_URL,
            "registered_providers": llm_registry.keys(),
            "loaded_instances": sorted(self._providers.keys()),
            "model_by_purpose": select_model("chat"),
            "embedding_model": select_embedding_model(),
        }

    def clear_instances(self) -> None:
        """清空实例缓存（配置变更 / 测试用）。"""
        self._providers.clear()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _next_fallback(model: str | None) -> str | None:
        chain = fallback_models(model or "")
        return chain[0] if chain else None

    @staticmethod
    def _wrap_error(exc: BaseException) -> BaseException:
        """把任意异常收敛成平台异常。"""
        from app.core.exceptions import BusinessException, ExternalServiceError

        if isinstance(exc, BusinessException):
            return exc
        if isinstance(exc, TimeoutError):
            return ExternalServiceError(f"大模型调用超时：{exc}")
        return AIError(f"大模型调用失败：{exc}")

    async def _record(
        self,
        result: LLMResult,
        purpose: str,
        user_id: int | None,
        conversation_id: str | None,
        trace_id: str | None,
    ) -> None:
        """统一埋点：token 与耗时写入 token_manager。"""
        try:
            await token_manager.record(
                user_id=user_id,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                purpose=purpose,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001 - 埋点失败不影响业务返回
            logger.warning("token 埋点失败：%s", exc)

    @staticmethod
    def to_messages(items: Iterable[Any]) -> list[ChatMessage]:
        return [ChatMessage.from_any(item) for item in items or []]


llm_manager = LLMManager()


__all__ = ["LLMManager", "llm_manager"]
