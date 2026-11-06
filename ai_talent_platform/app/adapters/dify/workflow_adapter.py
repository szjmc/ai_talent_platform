"""
模块：app/adapters/dify/workflow_adapter.py
职责：Dify 工作流适配器（lz_agent 分支的 workflow_key 设计，合并 origin/lz 后保留）。
标签：[Dify组] [P1]（依赖：[Agent组] services/jd_parser、explanation_service）

============================ 合并说明（必读） ============================
合并 origin/lz 后，app/adapters/dify/dify_adapter.py 采用 lz 线实现：
    DifyAdapter(api_key, app=...)  + run_workflow(inputs) / chat / chat_stream
密钥由上层 Service 从 workflow_info 表查出后注入，服务知识库 / RAG 链路。

本文件保留 lz_agent 线原有的「工作流注册表 + 异常分族 + 重试」实现，专项服务：
    app/services/jd_parser.py           JD 解析（workflow_key="job_profile_extraction"）
    app/services/explanation_service.py 匹配解释（workflow_key="match_explanation"）

两套适配器并存、职责分离：
    dify_adapter.DifyAdapter             -> 通用调用（密钥注入式）
    workflow_adapter.DifyWorkflowAdapter -> 按 workflow_key 取 Key 的封装（岗位匹配链路）
=========================================================================
"""


from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from app.ai.utils.output_parser import parse_json
from app.ai.utils.retry_handler import ai_retry
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 异常族


# *kwq* 新增：Dify 异常族（统一父类 + 按原因细分）。
# 原因：上层降级链需要区分「可重试」「需换 Key」「需改工作流」，不能一律 except Exception。
# 影响范围：纯新增，不改既有异常体系；父类沿用 ExternalServiceError（code 5001），
#          与「外部服务失败」的既有语义一致，全局异常处理器无需改动。
class DifyError(ExternalServiceError):
    """Dify 调用失败的统一父类。"""

    code = 5001
    status_code = 502
    message = "Dify 工作流调用失败"


class DifyNotConfiguredError(DifyError):
    """未配置 DIFY_API_KEY（本地开发常见）。

    上层应直接降级到 LLM / 规则化，不做重试 —— 重试也不会成功。
    """

    code = 5011
    status_code = 503
    message = "Dify 未配置（缺少 DIFY_API_KEY）"


class DifyTimeoutError(DifyError):
    """请求超时（可重试）。"""

    code = 5012
    status_code = 504
    message = "Dify 工作流调用超时"


class DifyUnavailableError(DifyError):
    """Dify 服务不可用（5xx / 429 / 连接失败，可重试）。"""

    code = 5013
    message = "Dify 服务暂不可用"


class DifyAuthError(DifyError):
    """鉴权失败（401/403）—— 需要换 Key，重试无意义。"""

    code = 5014
    status_code = 401
    message = "Dify 鉴权失败，请检查 API Key"


class DifyRequestError(DifyError):
    """请求参数错误（4xx）—— 需要改调用方或工作流配置，重试无意义。"""

    code = 5015
    status_code = 400
    message = "Dify 请求参数错误"


class DifyParseError(DifyError):
    """返回内容不是预期 JSON（响应体、或 outputs.result 解析失败）。"""

    code = 5002
    message = "Dify 返回内容无法解析为结构化数据"


class DifyWorkflowFailedError(DifyError):
    """工作流执行失败（HTTP 200 但 data.status=failed）。"""

    code = 5016
    message = "Dify 工作流内部执行失败"


# *kwq* 新增：可重试异常集合（只保留「等一会儿可能就好了」的两类）。
# 依据：V1.0 方案 MATCH-006 验收要求「仅超时 / 5xx 重试」；
#      鉴权、参数、工作流内部错误重试只会浪费时间并放大故障。
_RETRY_ON: tuple[type[BaseException], ...] = (DifyTimeoutError, DifyUnavailableError)


# ------------------------------------------------------------------ 工作流定义


# *kwq* 新增：工作流规格（把「调用哪个工作流、要哪些入参」从代码里挪到配置结构）。
# 原因：Dify 组尚未冻结契约，变量名随时可能调整；写成结构后调整只改常量，不改调用逻辑。
# 注意：required_inputs 为【暂定·待联调确认】（V1.0 方案 3.6 节）。
@dataclass(frozen=True)
class DifyWorkflowSpec:
    """单个 Dify 工作流的调用规格。"""

    key: str                                  # 逻辑名（代码内引用）
    path: str = "/workflows/run"              # Dify 工作流执行接口路径
    required_inputs: tuple[str, ...] = ()     # 必填输入变量名（暂定，待联调确认）
    description: str = ""


# *kwq* 新增：工作流注册表。
# 说明：两个工作流共用一个 Dify 应用 Key 时走默认鉴权；若 Dify 组按应用分 Key，
#      可通过 DifyWorkflowAdapter(workflow_keys={...}) 或 api_key 参数覆盖，不需改本文件。
DEFAULT_WORKFLOWS: dict[str, DifyWorkflowSpec] = {
    "job_profile_extraction": DifyWorkflowSpec(
        key="job_profile_extraction",
        required_inputs=("jd_text",),
        description="JD 自然语言 -> 结构化岗位信息（Dify 只做语义提取，不做判定）",
    ),
    "match_explanation": DifyWorkflowSpec(
        key="match_explanation",
        required_inputs=("evidence_pack",),
        description="结构化证据包 -> 自然语言匹配解释（Dify 只做表达，不算分）",
    ),
}


# ------------------------------------------------------------------ 适配器


class DifyWorkflowAdapter:
    """Dify 工作流调用适配器（异步、可注入 transport 便于单测）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        workflow_key: str | None = None,
        timeout: float | None = None,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        user: str = "ai-talent-platform",
        workflows: Mapping[str, DifyWorkflowSpec] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        参数：
            base_url:   默认取 settings.DIFY_BASE_URL（如 http://127.0.0.1/v1）
            api_key:    默认取 settings.DIFY_API_KEY；为空时调用会抛 DifyNotConfiguredError
            timeout:    单次请求超时（秒），默认 60
            max_attempts: 最大尝试次数（含首次），仅对超时 / 5xx 生效
            base_delay: 重试基准延迟（秒），测试传 0 可免等待
            user:       传给 Dify 的调用方标识（其日志按此归集）
            workflows:  工作流规格覆盖（默认 DEFAULT_WORKFLOWS）
            transport / client: 测试注入点（httpx.MockTransport）
        """
        self._base_url = (base_url if base_url is not None else settings.DIFY_BASE_URL or "").rstrip("/")
        # 步骤1：按业务选择应用密钥；显式参数优先，兼容共用配置。
        # [岗位匹配接入] 按工作流选择Key；这是公共适配器，保留其他调用方兼容。
        key_fields = {"job_profile_extraction": "DIFY_JD_API_KEY", "match_explanation": "DIFY_EXPLANATION_API_KEY"}
        configured_key = getattr(settings, key_fields.get(workflow_key or "", "DIFY_API_KEY"), "") or settings.DIFY_API_KEY
        self._api_key = (api_key if api_key is not None else configured_key) or ""
        self._timeout = float(timeout if timeout is not None else 60.0)
        self._user = user
        self._workflows = dict(workflows or DEFAULT_WORKFLOWS)
        self._transport = transport
        self._client = client
        self._owns_client = client is None
        self._max_attempts = max(1, int(max_attempts))

        # *kwq* 新增：按实例配置生成重试包装（ai_retry 的参数是装饰器级的，
        # 直接在类上装饰会把重试次数写死，无法按实例调整）。复用既有重试器，不自写退避逻辑。
        self._post = ai_retry(
            max_attempts=self._max_attempts,
            base_delay=base_delay,
            retry_on=_RETRY_ON,
        )(self._post_once)

    # ------------------------------------------------------------------ 生命周期
    def _ensure_client(self) -> httpx.AsyncClient:
        """懒创建 httpx.AsyncClient（复用连接池；测试时用注入的 transport）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        """关闭自建客户端（注入的客户端由调用方负责关闭）。"""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "DifyWorkflowAdapter":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ 状态
    @property
    def configured(self) -> bool:
        """是否已配置可用（缺 Key 时上层应直接走降级）。"""
        return bool(self._api_key) and bool(self._base_url)

    @property
    def workflows(self) -> list[str]:
        """已注册的工作流逻辑名（供运维排查「调的是哪个」）。"""
        return sorted(self._workflows)

    # ------------------------------------------------------------------ 内部：请求
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _spec(self, key: str) -> DifyWorkflowSpec:
        spec = self._workflows.get(key)
        if spec is None:
            raise DifyRequestError(
                f"未注册的 Dify 工作流：{key}",
                data={"available": sorted(self._workflows)},
            )
        return spec

    async def _post_once(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """单次请求（不含重试）：负责发请求 + 分类 HTTP 错误 + 解析响应体。"""
        client = self._ensure_client()
        try:
            response = await client.post(path, json=payload, headers=self._headers(), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise DifyTimeoutError(f"Dify 调用超时（{timeout}s）：{path}") from exc
        except httpx.HTTPError as exc:  # 连接失败 / DNS / 协议错误
            raise DifyUnavailableError(f"Dify 连接失败：{exc}") from exc

        self._raise_for_status(response, path)

        try:
            body = response.json()
        except ValueError as exc:
            raise DifyParseError(
                "Dify 响应不是合法 JSON",
                data={"snippet": response.text[:300]},
            ) from exc
        if not isinstance(body, dict):
            raise DifyParseError("Dify 响应不是 JSON 对象", data={"snippet": str(body)[:300]})

        return self._normalize(body, path)

    @staticmethod
    def _raise_for_status(response: httpx.Response, path: str) -> None:
        """把 HTTP 状态码翻译成上表可识别的异常（这是降级决策的依据）。"""
        status = response.status_code
        if status < 400:
            return
        snippet = response.text[:300]
        data = {"status_code": status, "path": path, "snippet": snippet}

        if status in (401, 403):
            raise DifyAuthError("Dify 鉴权失败（401/403），请检查 DIFY_API_KEY 是否有效", data=data)
        if status == 404:
            raise DifyRequestError("Dify 接口不存在（404），请检查 DIFY_BASE_URL 与工作流路径", data=data)
        if status in (408, 504):
            raise DifyTimeoutError("Dify 网关超时", data=data)
        if status == 429 or status >= 500:
            raise DifyUnavailableError(f"Dify 服务不可用（HTTP {status}）", data=data)
        if status >= 400:
            raise DifyRequestError(f"Dify 请求被拒绝（HTTP {status}）", data=data)

    def _normalize(self, body: dict[str, Any], path: str) -> dict[str, Any]:
        """把 Dify 响应体规范成统一结构。

        Dify 工作流（blocking 模式）返回形如：
            {"data": {"id": "...", "status": "succeeded",
                      "outputs": {"result": "{...json...}"}, "error": null,
                      "elapsed_time": 2.1, "total_tokens": 123}}
        但 outputs 的形态（单字段 result 还是多字段）需 Dify 组确认（待联调）。
        """
        data = body.get("data")
        if not isinstance(data, dict):
            # 兼容「Dify 直接返回 outputs」的部署形态（自建版可能不同），仍按待联调处理
            raise DifyParseError("Dify 响应缺少 data 字段", data={"snippet": str(body)[:300]})

        status = str(data.get("status") or "succeeded")
        if status != "succeeded":
            error = data.get("error") or data.get("error_message") or "未知错误"
            raise DifyWorkflowFailedError(
                f"Dify 工作流执行失败（status={status}）：{error}",
                data={"status": status, "error": str(error)[:300], "path": path},
            )

        outputs = data.get("outputs")
        if not isinstance(outputs, dict):
            raise DifyParseError("Dify 响应缺少 outputs 字段", data={"snippet": str(outputs)[:300]})

        return {
            "run_id": str(data.get("id") or ""),
            "status": status,
            "outputs": outputs,
            "parsed": self._parse_outputs(outputs),
            "elapsed_time": data.get("elapsed_time"),
            "total_tokens": data.get("total_tokens"),
        }

    @staticmethod
    def _parse_outputs(outputs: dict[str, Any]) -> Any:
        """解析 outputs 的业务内容。

        口径（V1.0 方案 3.6 节暂定）：
            - 有 "result" 且是字符串 -> 按 JSON 解析（Dify 的工作流输出常被塞成一个字符串）；
            - 有 "result" 且是对象 -> 直接用；
            - 没有 "result" -> 整个 outputs 当结果（多字段输出形态）。
        解析失败抛 DifyParseError，上层据此降级（不要拿半截字符串去猜字段）。
        """
        if "result" in outputs:
            raw = outputs["result"]
            if isinstance(raw, str):
                try:
                    return parse_json(raw)
                except Exception as exc:  # noqa: BLE001 - parse_json 抛的是 AIError，统一转 DifyParseError
                    raise DifyParseError(
                        "Dify outputs.result 无法解析为 JSON",
                        data={"snippet": raw[:300]},
                    ) from exc
            return raw
        return outputs

    # ------------------------------------------------------------------ 对外：工作流调用

    # *kwq* 新增（MATCH-006 主入口）：统一工作流调用。
    # 调用方：MATCH-007 解释编排、MATCH-011 JD 解析。
    # 返回：{"run_id","status","outputs","parsed","elapsed_time","total_tokens","workflow_key"}
    #      —— parsed 是业务可直接使用的结构化结果，outputs 保留原始形态供审计。
    async def run_workflow(
        self,
        key: str,
        inputs: Mapping[str, Any],
        *,
        user: str | None = None,
        timeout: float | None = None,
        response_mode: str = "blocking",
    ) -> dict[str, Any]:
        """运行一个 Dify 工作流（失败抛 DifyError 子类，由上层决定是否降级）。"""
        spec = self._spec(key)

        if not self._api_key:
            # 本地开发常态：直接给出可识别异常，上层立即降级，不要发无意义的请求
            raise DifyNotConfiguredError("未配置 DIFY_API_KEY，Dify 通道不可用")

        if not isinstance(inputs, Mapping):
            raise DifyRequestError("inputs 必须是键值映射（dict）")

        missing = [name for name in spec.required_inputs if name not in inputs]
        if missing:
            raise DifyRequestError(
                f"工作流 {key} 缺少必填入参：{missing}",
                data={"required": list(spec.required_inputs)},
            )

        payload = {
            "inputs": dict(inputs),
            "response_mode": response_mode,
            "user": user or self._user,
        }
        logger.info(
            "dify.run_workflow key=%s user=%s inputs=%s",
            key,
            payload["user"],
            sorted(payload["inputs"]),
        )

        result = await self._post(spec.path, payload, float(timeout or self._timeout))
        result["workflow_key"] = key
        return result

    # *kwq* 新增：两个业务工作流的便捷入口（薄封装，只负责拼 inputs）。
    # 说明：变量名按 V1.0 方案 3.6 节暂定，【待联调确认】；契约变化时只改这里。
    async def run_job_profile_extraction(self, jd_text: str, **kwargs: Any) -> dict[str, Any]:
        """把 JD 原文交给 Dify 做结构化提取（不做任何业务判定）。"""
        if not str(jd_text or "").strip():
            raise DifyRequestError("JD 文本为空，不能提交提取")
        return await self.run_workflow("job_profile_extraction", {"jd_text": jd_text}, **kwargs)

    async def run_match_explanation(self, evidence_pack: Any, **kwargs: Any) -> dict[str, Any]:
        """把证据包交给 Dify 生成解释。

        入参统一序列化为 JSON 字符串（Dify 的 inputs 值以字符串为主，复杂结构需显式序列化）；
        只送证据包，不送简历全文 —— 这是防编造的前置条件。
        """
        payload = evidence_pack
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return await self.run_workflow("match_explanation", {"evidence_pack": text}, **kwargs)


# *kwq* 新增：进程内默认适配器（懒创建）。
# 原因：适配器持有连接池，每个请求新建一次会浪费连接；
#      但也不能在 import 时读配置（settings 可能尚未加载），所以懒创建。
_default_adapter: DifyWorkflowAdapter | None = None


def get_workflow_adapter(**kwargs: Any) -> DifyWorkflowAdapter:
    """获取默认 DifyWorkflowAdapter（传参时新建并替换默认实例，便于测试与切换配置）。"""
    global _default_adapter
    if kwargs or _default_adapter is None:
        _default_adapter = DifyWorkflowAdapter(**kwargs)
    return _default_adapter


def reset_workflow_adapter() -> None:
    """清空默认适配器（测试用，避免用例间状态串扰）。"""
    global _default_adapter
    _default_adapter = None


__all__ = [
    "DEFAULT_WORKFLOWS",
    "DifyWorkflowAdapter",
    "DifyAuthError",
    "DifyError",
    "DifyNotConfiguredError",
    "DifyParseError",
    "DifyRequestError",
    "DifyTimeoutError",
    "DifyUnavailableError",
    "DifyWorkflowFailedError",
    "DifyWorkflowSpec",
    "get_workflow_adapter",
    "reset_workflow_adapter",
]
