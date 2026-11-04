"""
模块：app/config/__init__.py
职责：配置层统一出口，便于 `from app.config import settings`。

标签：[架构组] [P0]（settings 统一出口）
"""

from app.config.settings import Settings, get_settings, settings

__all__ = ["Settings", "settings", "get_settings"]
