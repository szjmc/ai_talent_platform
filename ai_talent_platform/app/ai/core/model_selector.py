"""
模块：app/ai/core/model_selector.py
职责：按任务类型选择合适的大模型。

============================ 实现思路与代码要点 ============================
用途 -> 模型策略（示例，按 settings 可覆盖）：
    resume_parse   结构化输出稳定（qwen-plus / gpt-4o-mini）
    talent_portrait 推理能力较强（qwen-max / gpt-4o）
    matching       长上下文 + 推理（qwen-plus）
    assistant      成本优先 + 流式（qwen-turbo）
    embed          向量模型（text-embedding-v3 等，维度需与 Milvus 集合一致）
实现要点：
- 策略表集中定义，不要在业务代码里写模型名；
- 支持按环境变量覆盖（LLM_MODEL 为默认，特定用途可用 LLM_MODEL_RESUME 等）；
- 记录「实际使用模型」到结果表，便于效果对比与降级回溯。
待实现清单：
    - [ ] MODEL_BY_PURPOSE 映射 + select_model(purpose) -> str
    - [ ] 降级链：主模型失败 -> 备用模型重试一次
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
