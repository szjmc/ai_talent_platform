"""
模块：app/adapters/digital_human/comfyui_adapter.py
职责：ComfyUI 本地数字人适配器（HTTP 调用 ComfyUI 工作流生成真人说话头视频）。

ComfyUI 交互流程：
1. 用户在 ComfyUI 里搭好数字人工作流（MuseTalk / SadTalker / AnimateDiff + 音频驱动），
   导出 API 格式工作流 JSON，配置到 settings.COMFYUI_WORKFLOW。
2. 后端把播报文本填入工作流指定节点，POST /prompt 触发生成，返回 prompt_id。
3. 轮询 GET /history/{prompt_id} 直到完成，从 outputs 中提取输出视频文件名。
4. GET /view?filename=xxx&type=output 下载视频二进制。
5. 上传到平台对象存储（MinIO/OSS/S3/local），返回平台内可播放 URL。

环境变量：
    COMFYUI_BASE_URL=http://127.0.0.1:8188
    COMFYUI_WORKFLOW=<工作流 JSON 字符串或文件路径>
    COMFYUI_TEXT_NODE_ID=<负责输入文本的节点 ID，如 "6">；留空则自动寻找含 "text" 的节点
    COMFYUI_TEXT_INPUT_KEY=<文本输入字段名，如 "text">；默认 "text"
    COMFYUI_OUTPUT_NODE_ID=<负责保存视频的节点 ID，如 "11">；留空则自动提取首个视频
    COMFYUI_TIMEOUT_SECONDS=300
    COMFYUI_POLL_INTERVAL_SECONDS=2
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.adapters.digital_human.base_digital_human import BaseDigitalHuman, DigitalHumanResult
from app.adapters.digital_human import digital_human_registry
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class ComfyUINotConfigured(ExternalServiceError):
    code = 5031
    message = "ComfyUI 数字人未配置"


class ComfyUIGenerationError(ExternalServiceError):
    code = 5032
    message = "ComfyUI 数字人生成失败"


@digital_human_registry.register("comfyui")
class ComfyUIAdapter(BaseDigitalHuman):
    """ComfyUI 本地数字人适配器。"""

    provider = "comfyui"

    def __init__(
        self,
        base_url: str | None = None,
        workflow: str | None = None,
        text_node_id: str | None = None,
        text_input_key: str | None = None,
        output_node_id: str | None = None,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "COMFYUI_BASE_URL", "") or "").rstrip("/")
        self.text_node_id = text_node_id or getattr(settings, "COMFYUI_TEXT_NODE_ID", "") or ""
        self.text_input_key = text_input_key or getattr(settings, "COMFYUI_TEXT_INPUT_KEY", "text") or "text"
        self.output_node_id = output_node_id or getattr(settings, "COMFYUI_OUTPUT_NODE_ID", "") or ""
        self.timeout = float(timeout or getattr(settings, "COMFYUI_TIMEOUT_SECONDS", 300))
        self.poll_interval = float(poll_interval or getattr(settings, "COMFYUI_POLL_INTERVAL_SECONDS", 2))
        self.workflow = self._load_workflow(workflow or getattr(settings, "COMFYUI_WORKFLOW", ""))

    def _load_workflow(self, workflow: str | None) -> dict[str, Any] | None:
        if not workflow:
            return None
        workflow = workflow.strip()
        try:
            p = Path(workflow)
            if p.exists() and p.is_file():
                workflow = p.read_text(encoding="utf-8")
        except OSError:
            pass
        try:
            return json.loads(workflow)
        except json.JSONDecodeError as exc:
            logger.warning("ComfyUI 工作流 JSON 解析失败：%s", exc)
            return None

    async def _prompt(self, client: httpx.AsyncClient, prompt: dict[str, Any]) -> str:
        payload = {"prompt": prompt, "client_id": "ai-talent-platform"}
        resp = await client.post(f"{self.base_url}/prompt", json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ComfyUIGenerationError(f"ComfyUI /prompt 返回 {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIGenerationError(f"ComfyUI /prompt 未返回 prompt_id: {data}")
        return str(prompt_id)

    async def _wait_for_completion(self, client: httpx.AsyncClient, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout
        last_error = ""
        while time.time() < deadline:
            try:
                resp = await client.get(f"{self.base_url}/history/{prompt_id}", timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if prompt_id in data and data[prompt_id].get("outputs"):
                        status = data[prompt_id].get("status", {})
                        if status.get("completed") or data[prompt_id].get("outputs"):
                            return data[prompt_id]
                    last_error = ""
                else:
                    last_error = f"history 返回 {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(self.poll_interval)
        raise ComfyUIGenerationError(f"ComfyUI 生成超时或未完成（{last_error}）")

    @staticmethod
    def _extract_video_filename(outputs: dict[str, Any], output_node_id: str | None) -> str | None:
        candidates: list[str] = []
        if output_node_id and output_node_id in outputs:
            node_out = outputs[output_node_id]
            for key in ("videos", "images", "gifs"):
                for item in node_out.get(key, []) or []:
                    if isinstance(item, dict):
                        candidates.append(item.get("filename", ""))
                    elif isinstance(item, str):
                        candidates.append(item)
        if not candidates:
            for _node_id, node_out in outputs.items():
                if not isinstance(node_out, dict):
                    continue
                for key in ("videos", "images", "gifs"):
                    for item in node_out.get(key, []) or []:
                        if isinstance(item, dict):
                            candidates.append(item.get("filename", ""))
                        elif isinstance(item, str):
                            candidates.append(item)
        for name in candidates:
            if name and str(name).lower().endswith((".mp4", ".webm", ".mov", ".avi", ".mkv")):
                return str(name)
        return None

    async def _download_file(self, client: httpx.AsyncClient, filename: str) -> bytes:
        params = {"filename": filename, "type": "output"}
        resp = await client.get(f"{self.base_url}/view", params=params, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ComfyUIGenerationError(f"ComfyUI /view 下载失败 {resp.status_code}: {resp.text[:200]}")
        return resp.content

    # ------------------------------------------------------------------ 主入口
    async def generate_video(
        self,
        text: str,
        *,
        persona_key: str = "default",
        voice: str | None = None,
        language: str = "zh-CN",
        **options: Any,
    ) -> DigitalHumanResult:
        if not self.base_url:
            raise ComfyUINotConfigured("未设置 COMFYUI_BASE_URL，请先在环境变量/.env 中配置")
        if not self.workflow:
            raise ComfyUINotConfigured(
                "未设置 COMFYUI_WORKFLOW，请在 ComfyUI 里搭好数字人工作流并导出 API JSON"
            )

        started = time.perf_counter()
        prompt = json.loads(json.dumps(self.workflow))
        self._inject_text(prompt, text)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            prompt_id = await self._prompt(client, prompt)
            history = await self._wait_for_completion(client, prompt_id)
            outputs = history.get("outputs", {}) or {}
            filename = self._extract_video_filename(outputs, self.output_node_id or None)
            if not filename:
                raise ComfyUIGenerationError("ComfyUI 输出中未发现视频文件")
            video_bytes = await self._download_file(client, filename)

        storage_url = await self._upload_to_storage(video_bytes, filename)

        return DigitalHumanResult(
            video_url=storage_url,
            video_bytes=video_bytes,
            provider=self.provider,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            raw={"filename": filename, "prompt_id": prompt_id},
        )

    # ------------------------------------------------------------------ 内部工具
    def _inject_text(self, prompt: dict[str, Any], text: str) -> None:
        nodes = prompt.get("nodes") if isinstance(prompt.get("nodes"), list) else None
        if nodes is not None:
            for node in nodes:
                node_id = str(node.get("id") or "")
                if self.text_node_id and node_id != self.text_node_id:
                    continue
                inputs = node.get("inputs") or {}
                if self.text_input_key in inputs:
                    inputs[self.text_input_key] = text
                    logger.debug("ComfyUI 已向节点 %s 填入文本", node_id)
                    return
            if not self.text_node_id:
                for node in nodes:
                    inputs = node.get("inputs") or {}
                    if self.text_input_key in inputs:
                        inputs[self.text_input_key] = text
                        logger.debug("ComfyUI 自动定位文本节点 %s", node.get("id"))
                        return
        else:
            target = self.text_node_id or None
            for node_id, node in prompt.items():
                if not isinstance(node, dict):
                    continue
                if target and node_id != target:
                    continue
                inputs = node.get("inputs") or {}
                if self.text_input_key in inputs:
                    inputs[self.text_input_key] = text
                    logger.debug("ComfyUI 已向节点 %s 填入文本", node_id)
                    return
        logger.warning(
            "ComfyUI 工作流中未找到可填入文本的节点（text_node_id=%s, text_input_key=%s），"
            "生成的内容可能不随文本变化。",
            self.text_node_id or "auto",
            self.text_input_key,
        )

    async def _upload_to_storage(self, video_bytes: bytes, filename: str) -> str:
        from app.adapters.storage import get_storage

        storage = get_storage()
        object_key = f"digital-human/{int(time.time())}/{filename}"
        await storage.save(video_bytes, object_key, subdir="")
        return storage.url(object_key)

    async def health(self) -> dict[str, Any]:
        if not self.base_url or not self.workflow:
            return {
                "ok": False,
                "provider": self.provider,
                "error": "ComfyUI 未配置（缺少 BASE_URL 或 WORKFLOW）",
            }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/system_stats", timeout=5.0)
            return {
                "ok": resp.status_code < 400,
                "provider": self.provider,
                "base_url": self.base_url,
                "workflow_loaded": bool(self.workflow),
            }
        except Exception as exc:
            return {"ok": False, "provider": self.provider, "error": str(exc)}


__all__ = ["ComfyUIAdapter", "ComfyUINotConfigured", "ComfyUIGenerationError"]
