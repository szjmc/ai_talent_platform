"""
模块：app/adapters/digital_human/__init__.py
职责：数字人适配器注册表与获取入口（效仿 storage 设计）。

已内置适配器（注册名）：
    comfyui  本地 ComfyUI 工作流驱动的真人数字人

扩展方式：实现 BaseDigitalHuman 并 @digital_human_registry.register("xxx"),
业务侧通过 get_digital_human("xxx") 切换，无需改动调用方。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from app.config.settings import settings
from app.patterns.registry import Registry

logger = logging.getLogger(__name__)

digital_human_registry: Registry = Registry("digital_human")

_ADAPTER_MODULES: dict[str, str] = {
    "comfyui": "app.adapters.digital_human.comfyui_adapter",
}


def _ensure_registered(name: str) -> bool:
    if name in digital_human_registry:
        return True
    module = _ADAPTER_MODULES.get(name)
    if not module:
        return False
    try:
        importlib.import_module(module)
    except ImportError as exc:
        logger.warning("数字人适配器导入失败：%s（%s）", module, exc)
        return False
    return name in digital_human_registry


def get_digital_human(name: str | None = None) -> Any:
    wanted = str(name or getattr(settings, "DIGITAL_HUMAN_PROVIDER", "") or "comfyui").strip().lower()
    registered = _ensure_registered(wanted)
    if not registered:
        if wanted in _ADAPTER_MODULES:
            from app.core.exceptions import ExternalServiceError

            raise ExternalServiceError(
                f"数字人适配器 {wanted} 初始化失败，可用：{digital_human_registry.keys()}"
            )
        if wanted != "comfyui":
            logger.warning(
                "数字人适配器 %s 未注册，可用：%s，已回退 comfyui",
                wanted,
                digital_human_registry.keys(),
            )
        _ensure_registered("comfyui")
        wanted = "comfyui"
    cls = digital_human_registry.try_get(wanted)
    if cls is None:
        from app.core.exceptions import ExternalServiceError

        raise ExternalServiceError(f"未注册的数字人适配器：{wanted}，可用：{digital_human_registry.keys()}")
    return cls()


def available_providers() -> list[str]:
    for name in _ADAPTER_MODULES:
        _ensure_registered(name)
    return digital_human_registry.keys()


__all__ = ["get_digital_human", "available_providers", "digital_human_registry"]
