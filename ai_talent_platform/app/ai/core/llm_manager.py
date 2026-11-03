"""
模块：app/ai/core/llm_manager.py
职责：大模型统一管理（单例、按用途取模型、统一埋点）。

============================ 实现思路与代码要点 ============================
1. 根据 settings.LLM_PROVIDER 通过 patterns.factory + llm_registry 创建适配器实例，进程内单例；
2. 对外 API：
     get_llm(purpose: str = "default") -> BaseLLM
     chat(messages, purpose="chat") -> LLMResult
     embed(texts) -> list[list[float]]
3. purpose 与模型映射来自 model_selector（例如 resume_parse -> 结构化输出强模型，
   assistant -> 成本较低模型）；
4. 每次调用统一记录：model、tokens、耗时 -> token_manager；
5. 异常统一转换 ExternalServiceError / AIError，并支持一次自动重试。
待实现清单：
    - [ ] LLMManager 单例与 get_llm
    - [ ] chat / embed 封装
    - [ ] 与 model_selector、token_manager 的联动
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
