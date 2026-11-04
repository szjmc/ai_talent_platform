"""
模块：app/adapters/dify/dify_adapter.py
职责：Dify 中台适配（应用发布 / 工作流 / 知识库运维的统一封装）。

标签：[Dify组] [P0]  依赖：[架构组] BaseLLM 契约 / LLMManager（工作流触发 / 应用调用 / 回调解析）

============================ 实现思路与代码要点 ============================
Dify 在平台中的定位：
    统一模型接入、Prompt 管理、知识库运维、低代码应用发布、AI 调用日志。
本适配器只封装「调用」，不承载业务规则。

主要能力（按 Dify API）：
    run_workflow(workflow_id, inputs, user)       运行工作流（阻塞 / 流式）
    chat(app_id, query, conversation_id, user)    对话型应用
    upload_document(dataset_id, file)             上传知识库文档
    list_documents(dataset_id)                    文档列表
实现要点：
- 使用 httpx.AsyncClient，超时与重试统一在 ai/utils/retry_handler；
- 配置来自 settings.DIFY_BASE_URL / DIFY_API_KEY；
- 流式接口返回 SSE，上层 routers/agent_router 直接转发；
- 失败抛 ExternalServiceError，并在日志中记录 request_id 便于对齐 Dify 日志。
待实现清单：
    - [ ] __init__（客户端、鉴权头）
    - [ ] run_workflow / chat / upload_document / list_documents
    - [ ] 流式解析（逐行 SSE）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
