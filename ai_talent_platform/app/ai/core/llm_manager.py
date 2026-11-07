"""
模块：app/ai/core/llm_manager.py
职责：大模型统一管理（单例、按用途取模型、统一埋点）。

标签：[架构组] [P0]（其余 4 组的公共门面）

============================ 实现思路与代码要点 ============================
1. 根据 settings.LLM_PROVIDER 通过 llm_registry 创建适配器实例，进程内单例；
   模型由 model_selector + provider_config 决定，不写死任何厂商 / 模型；
2. 对外 API：
     get_llm(purpose: str = "default") -> BaseLLM
     chat(messages, purpose="chat") -> LLMResult
     embed(texts) -> list[list[float]]
3. purpose 与模型映射来自 model_selector；
4. 每次调用统一记录：model、tokens、耗时 -> token_manager；
5. 异常统一转换 ExternalServiceError / AIError，并按降级链重试一次备用模型；
6. 向量供应商可独立配置（LLM_EMBED_PROVIDER），DeepSeek 等无 embedding 的厂商可接入 qwen / openai。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Iterable, Sequence

from app.adapters.llm.base_llm import BaseLLM, ChatMessage, LLMResult
from app.adapters.llm.provider_config import (
    describe_providers,
    provider_configured,
    resolve_base_url,
)
from app.ai.core.model_selector import (
    fallback_models,
    select_embedding_model,
    select_model,
    select_provider,
)
from app.ai.core.token_manager import token_manager
from app.ai.utils.retry_handler import is_permanent_http_error
from app.config.settings import settings
from app.core.exceptions import AIError
from app.patterns.registry import llm_registry

logger = logging.getLogger(__name__)

# 模块导入即触发适配器注册（各适配器文件底部 @llm_registry.register）
_ADAPTER_MODULES = (
    "app.adapters.llm.openai_adapter",
    "app.adapters.llm.qwen_adapter",
    "app.adapters.llm.deepseek_adapter",
    "app.adapters.llm.ollama_adapter",
    "app.adapters.llm.siliconflow_adapter",
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
    def get_llm(
        self,
        purpose: str = "default",
        provider: str | None = None,
        model: str | None = None,
    ) -> BaseLLM:
        """按用途取适配器实例（同 provider + model 复用同一个实例）。

        :param purpose: 用途（chat / resume / matching / embed ...），决定默认模型
        :param provider: 供应商（qwen / deepseek / openai / ollama / 自定义注册名），
                         默认取 settings.LLM_PROVIDER
        :param model: 显式指定模型（最高优先级；不传则由 model_selector 按用途选择）
        """
        _ensure_adapters_registered()
        provider = str(provider or settings.LLM_PROVIDER or "").strip().lower()
        target_model = model or self._model_for(provider, purpose)
        cache_key = f"{provider}:{target_model}"
        cached = self._providers.get(cache_key)
        if cached is not None:
            return cached

        adapter_cls = llm_registry.try_get(provider)
        if adapter_cls is None:
            raise AIError(
                f"未注册的 LLM provider：{provider}，可用：{llm_registry.keys()}",
                data={"available": llm_registry.keys(), "provider": provider},
            )
        # 密钥/地址交由BaseLLM按专属配置、当前供应商通用配置和目录默认值解析。
        instance = adapter_cls(model=target_model)
        self._providers[cache_key] = instance
        logger.info("创建 LLM 适配器：provider=%s model=%s purpose=%s", provider, target_model, purpose)
        return instance

    def get_embedding_llm(self, model: str | None = None) -> BaseLLM:
        """取向量模型适配器。

        向量供应商可与对话供应商不同（LLM_EMBED_PROVIDER）：
        例如对话用 deepseek（无 embedding），向量用 qwen / openai。
        """
        _ensure_adapters_registered()
        provider = select_provider("embed")
        embed_model = model or self._embedding_model_for(provider)
        adapter_cls = llm_registry.try_get(provider)
        if adapter_cls is not None and getattr(adapter_cls, "supports_embedding", True) is False:
            raise AIError(
                f"LLM 供应商 {provider} 不支持 embedding，请设置 LLM_EMBED_PROVIDER"
                "（如 qwen / openai）与 LLM_EMBED_MODEL",
                data={"provider": provider},
            )
        if not embed_model:
            raise AIError(
                f"当前向量供应商 {provider} 未提供 embedding 模型，"
                "请设置 LLM_EMBED_PROVIDER（如 qwen / openai）与 LLM_EMBED_MODEL",
                data={"provider": provider, "embedding_model": embed_model},
            )
        return self.get_llm(purpose="embed", provider=provider, model=embed_model)

    # ---------------------------------------------------------------- 对话
    @staticmethod
    def _llm_not_configured_error(provider: str) -> AIError:
        """LLM 密钥未配置时的统一、可操作错误（不做任何网络请求）。"""
        return AIError(
            "大模型未配置：请在 .env 中设置 LLM_API_KEY"
            f"（当前 LLM_PROVIDER={provider or '未设置'}），"
            "或改用本地 Ollama（LLM_PROVIDER=ollama + OLLAMA_BASE_URL）后重启服务",
            data={"provider": provider, "reason": "api_key_missing"},
        )

    def _ensure_configured(self, provider: str) -> None:
        """密钥缺失时快速失败，避免每次请求都等满超时/重试。"""
        from app.adapters.llm.provider_config import provider_configured

        if not provider_configured(provider):
            raise self._llm_not_configured_error(provider)

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
        self._ensure_configured(llm.provider)
        target_model = model or self._model_for(llm.provider, purpose)
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
            if is_permanent_http_error(exc):
                raise self._wrap_error(exc)
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
        self._ensure_configured(llm.provider)
        normalized = [ChatMessage.from_any(item) for item in messages or []]
        async for chunk in llm.stream(normalized, **kwargs):
            yield chunk

    # ---------------------------------------------------------------- 向量
    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """文本向量化（维度需与 Milvus 集合一致）。"""
        if not texts:
            return []
        llm = self.get_embedding_llm(model=model)
        try:
            return await llm.embed(list(texts))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_error(exc) from exc

    # ---------------------------------------------------------------- 运维
    def status(self) -> dict[str, Any]:
        """当前 LLM 运行状态（供 /system/info 的 ai_ready 使用）。"""
        _ensure_adapters_registered()
        provider = str(settings.LLM_PROVIDER or "").strip().lower()
        return {
            "provider": provider,
            "default_model": select_model("default"),
            "api_key_configured": provider_configured(provider),
            "base_url": resolve_base_url(provider),
            "registered_providers": llm_registry.keys(),
            "loaded_instances": sorted(self._providers.keys()),
            "model_by_purpose": select_model("chat"),
            "embedding_provider": select_provider("embed"),
            "embedding_model": select_embedding_model(),
            "providers": describe_providers(),
        }

    def clear_instances(self) -> None:
        """清空实例缓存（配置变更 / 测试用）。"""
        self._providers.clear()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _is_current_provider(provider: str) -> bool:
        return provider == str(settings.LLM_PROVIDER or "").strip().lower()

    def _model_for(self, provider: str, purpose: str) -> str:
        """按用途选模型；默认供应商走原入口，保证可被测试 / 配置替换。"""
        if self._is_current_provider(provider):
            return select_model(purpose)
        return select_model(purpose, provider=provider)

    def _embedding_model_for(self, provider: str) -> str:
        if self._is_current_provider(provider):
            return select_embedding_model()
        return select_embedding_model(provider)

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
        # 带异常类型，避免底层异常 str 为空（如部分网络/TLS 故障）时错误信息只剩空串
        if isinstance(exc, TimeoutError):
            return ExternalServiceError(f"大模型调用超时：{type(exc).__name__}: {exc}")
        return AIError(f"大模型调用失败：{type(exc).__name__}: {exc}")

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
