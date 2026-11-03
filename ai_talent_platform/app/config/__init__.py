"""
模块：app/config/__init__.py
职责：配置层统一出口，便于 `from app.config import settings`。
"""

from app.config.settings import Settings, get_settings, settings

__all__ = ["Settings", "settings", "get_settings"]
