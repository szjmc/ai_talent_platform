"""
模块：app/adapters/llm/__init__.py
职责：大模型适配器出口。

标签：[架构组] [P1]（LLM 适配出口）

已实现：
    base_llm.py          统一接口 BaseLLM / ChatMessage / LLMResult
    provider_config.py   供应商目录（默认地址 / 默认模型 / 专属配置项，模型不写死）
    openai_adapter.py    OpenAI 兼容协议（DashScope compatible-mode / DeepSeek 均可复用）
    qwen_adapter.py      通义千问（DashScope）
    deepseek_adapter.py  DeepSeek（deepseek-chat / deepseek-reasoner，任意模型名可配）
    ollama_adapter.py    本地 Ollama 私有化模型

工厂：LLMManager 按 settings.LLM_PROVIDER 从 patterns.registry.llm_registry 创建适配器；
新增供应商 = 新增一个 @llm_registry.register("xxx") 的适配器类（可继承 OpenAIAdapter）。
"""
