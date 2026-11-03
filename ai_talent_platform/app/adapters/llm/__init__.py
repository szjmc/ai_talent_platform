"""
模块：app/adapters/llm/__init__.py
职责：大模型适配器出口。

待实现：
    base_llm.py        统一接口 BaseLLM / ChatMessage / LLMResult
    openai_adapter.py  OpenAI 兼容协议（DashScope compatible-mode 也可复用）
    qwen_adapter.py    通义千问（DashScope）
    ollama_adapter.py  本地 Ollama 私有化模型

工厂：adapters/llm/factory.py 或 app/patterns/factory.AdapterFactory，
      按 settings.LLM_PROVIDER 创建对应适配器并注册到 patterns.registry.llm_registry。
"""
