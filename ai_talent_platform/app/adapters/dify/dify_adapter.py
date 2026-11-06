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
- 服务器地址来自 settings.DIFY_BASE_URL；密钥由上层 Service 从 workflow_info 表查出后注入；
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
import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.ai.utils.retry_handler import ai_retry
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

# 可重试的 HTTP 状态码（限流 / 超时 / 网关错误）
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

class DifyRetryableError(ExternalServiceError):
    """可恢复的 Dify 调用错误（触发重试）。"""

# 只重试这些：网络/超时/可恢复错误；400/401/403 等业务错误不重试
_RETRY_ON = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.TransportError,
    DifyRetryableError,
)

class DifyAdapter:
    """Dify 调用封装：工作流 + 聊天应用（阻塞 / 流式 SSE）。"""

    def __init__(
        self,
        api_key: str,
        *,
        app: str = "prompt",
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # 密钥由上层 Service 从 workflow_info 表查出后注入，适配器不再读 settings 的密钥字典
        if not api_key:
            raise ExternalServiceError(f"未获取到 Dify 工作流密钥: {app}")
        self.app = app
        self.base_url = (base_url or settings.DIFY_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # ============================ Workflow 工作流 ============================
    @ai_retry(retry_on=_RETRY_ON)
    async def run_workflow(
        self,
        inputs: dict[str, object],
        user: str = "backend",
        *,
        workflow_id: str = "",
    ) -> dict[str, object]:
        """运行工作流（阻塞），返回 Dify 的 data 段，业务结果在 ['outputs']。"""
        payload: dict[str, object] = {
            "inputs": self._normalize_inputs(inputs),
            "response_mode": "blocking",
            "user": user,
        }
        if workflow_id:
            payload["workflow_id"] = workflow_id

        async with self._client() as client:
            response = await client.post("/workflows/run", json=payload)
            await self._raise_for_status(response)
            body = await self._safe_json(response)

        data = body.get("data") or {}
        status = data.get("status")
        if status and status != "succeeded":
            raise ExternalServiceError(
                f"Dify 工作流执行失败：{data.get('error')}",
                data=data,
            )
        logger.info("Dify 工作流阻塞调用成功 app=%s status=%s", self.app, status)
        return data

    async def run_workflow_stream(
        self,
        inputs: dict[str, object],
        user: str = "backend",
        *,
        workflow_id: str = "",
    ) -> AsyncIterator[dict[str, object]]:
        """运行工作流（流式 SSE），逐事件 yield；最终事件 event=workflow_finished。"""
        payload: dict[str, object] = {
            "inputs": self._normalize_inputs(inputs),
            "response_mode": "streaming",
            "user": user,
        }
        if workflow_id:
            payload["workflow_id"] = workflow_id

        async with self._client() as client:
            async with client.stream("POST", "/workflows/run", json=payload) as response:
                await self._raise_for_status(response)
                async for event in self._iter_sse(response):
                    yield event

    # ============================ Chat 聊天应用 ============================
    @ai_retry(retry_on=_RETRY_ON)
    async def chat(
        self,
        query: str,
        user: str = "backend",
        *,
        inputs: dict[str, object] | None = None,
        conversation_id: str = "",
    ) -> dict[str, object]:
        """调用聊天应用（阻塞），返回体含 answer / conversation_id。"""
        payload: dict[str, object] = {
            "query": query,
            "inputs": self._normalize_inputs(inputs) if inputs else {},
            "response_mode": "blocking",
            "conversation_id": conversation_id,
            "user": user,
        }
        async with self._client() as client:
            response = await client.post("/chat-messages", json=payload)
            await self._raise_for_status(response)
            body = await self._safe_json(response)

        logger.info("Dify 聊天阻塞调用成功 app=%s", self.app)
        return body

    async def chat_stream(
        self,
        query: str,
        user: str = "backend",
        *,
        inputs: dict[str, object] | None = None,
        conversation_id: str = "",
    ) -> AsyncIterator[dict[str, object]]:
        """调用聊天应用（流式 SSE），逐事件 yield；event=message_end 表示结束。"""
        payload: dict[str, object] = {
            "query": query,
            "inputs": self._normalize_inputs(inputs) if inputs else {},
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": user,
        }
        async with self._client() as client:
            async with client.stream("POST", "/chat-messages", json=payload) as response:
                await self._raise_for_status(response)
                async for event in self._iter_sse(response):
                    yield event

    # ============================ 内部工具 ============================
    @staticmethod
    def _normalize_inputs(inputs: dict[str, object]) -> dict[str, object]:
        """归一化工作流/聊天入参，适配 Dify 开始节点变量类型约束：

        Dify 的 text/paragraph 变量只接受字符串，number 接受数值；
        客户端常直接传 dict/list（如 evidence_pack 结构体），这里统一
        序列化为 JSON 字符串，标量（str/int/float/bool/None）原样透传。
        """
        normalized: dict[str, object] = {}
        for key, value in inputs.items():
            if isinstance(value, (dict, list, tuple)):
                normalized[key] = json.dumps(value, ensure_ascii=False)
            else:
                normalized[key] = value
        return normalized

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
        )

    async def _raise_for_status(self, response: httpx.Response) -> None:
        """状态码 → 平台异常：业务错误不重试，429/5xx/超时可重试。"""
        if response.status_code < 400:
            return
        await response.aread()
        body = response.text[:500]
        message = f"Dify 调用失败（HTTP {response.status_code}）：{body}"
        if response.status_code in _RETRYABLE_STATUS:
            raise DifyRetryableError(message, data={"status": response.status_code})
        raise ExternalServiceError(message, data={"status": response.status_code})

    @staticmethod
    async def _safe_json(response: httpx.Response) -> dict[str, object]:
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                f"Dify 返回不是 JSON：{response.text[:300]}"
            ) from exc

    @staticmethod
    async def _iter_sse(
        response: httpx.Response,
    ) -> AsyncIterator[dict[str, object]]:
        """解析 SSE 数据流，逐行提取 data: 里的 JSON（工作流/聊天共用）。"""
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                yield json.loads(chunk)
            except (ValueError, TypeError):
                continue

__all__ = ["DifyAdapter", "DifyRetryableError"]