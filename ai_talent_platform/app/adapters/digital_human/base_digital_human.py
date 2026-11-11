"""
模块：app/adapters/digital_human/base_digital_human.py
职责：数字人生成统一接口（所有数字人供应商适配器的抽象基类）。

接口：
    generate_video(text, *, persona_key, voice, language) -> DigitalHumanResult
    传入播报文本，返回数字人说话头视频（二进制）或可直接播放的 URL。

适配器示例：comfyui_adapter（本地 ComfyUI 工作流驱动的真人数字人）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DigitalHumanResult:
    """数字人生成结果。

    - video_url: 平台内可直接播放的视频 URL（优先返回）
    - video_bytes: 视频二进制（供调用方自行存储时使用）
    - provider: 供应商标识（comfyui / heygen / did 等）
    - elapsed_ms: 生成耗时
    """

    video_url: str = ""
    video_bytes: bytes | None = None
    provider: str = "base"
    elapsed_ms: float = 0.0
    raw: Any = None


class BaseDigitalHuman(ABC):
    """数字人适配器基类。"""

    provider: str = "base"

    @abstractmethod
    async def generate_video(
        self,
        text: str,
        *,
        persona_key: str = "default",
        voice: str | None = None,
        language: str = "zh-CN",
        **options: Any,
    ) -> DigitalHumanResult:
        """根据文本生成数字人说话头视频。"""

    async def health(self) -> dict[str, Any]:
        """供应商连通性自检（子类可覆盖为真实探测）。"""
        return {"ok": True, "provider": self.provider, "type": self.__class__.__name__}

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "type": self.__class__.__name__}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} provider={self.provider}>"
