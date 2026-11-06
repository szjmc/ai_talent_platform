"""
模块：app/adapters/dify/__init__.py
职责：Dify 适配包入口。

两套适配器并存（合并 origin/lz 后）：
    DifyAdapter(lz 线)            通用调用：密钥注入 + run_workflow/chat/chat_stream
    DifyWorkflowAdapter(lz_agent)  工作流注册表：按 workflow_key 取 Key（岗位匹配链路）

标签：[Dify组] [P0]
"""
from .dify_adapter import DifyAdapter, DifyRetryableError
from .workflow_adapter import DifyWorkflowAdapter

__all__ = ["DifyAdapter", "DifyRetryableError", "DifyWorkflowAdapter"]
