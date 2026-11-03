"""
模块：app/adapters/llm/ollama_adapter.py
职责：Ollama 本地模型适配器。

============================ 实现思路与代码要点 ============================
1. 继承 app/adapters/llm/base_llm.BaseLLM，实现 chat / generate / stream / embed；
2. 配置全部来自 settings（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT）；
3. 使用 httpx.AsyncClient（连接复用）或官方 SDK；
4. 所有外部异常转换为 core.exceptions.ExternalServiceError / AIError；
5. 返回值统一为 LLMResult，并记录 prompt/completion tokens 与耗时；
6. stream() 需逐块 yield 文本，供 routers/agent_router 的 SSE 使用。

待实现清单：
    - [ ] __init__（读取配置、创建客户端）
    - [ ] chat() / generate()
    - [ ] stream()
    - [ ] embed()
    - [ ] 在 patterns.registry.llm_registry 中注册（@llm_registry.register("ollama_adapter")）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
