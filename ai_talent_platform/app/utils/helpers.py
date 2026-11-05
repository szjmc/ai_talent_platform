"""
模块：app/utils/helpers.py
职责：零散通用小工具（避免落入各业务模块重复实现）。

标签：[架构组] [P2]（零散小工具，避免重复实现）

待补充清单：
    - [ ] camel_to_snake / snake_to_camel（前后端字段风格转换）
    - [ ] deep_merge(dict, dict)
    - [ ] chunked(iterable, size)（批量处理）
    - [ ] mask_mobile 已在 core/security.mask_sensitive，不重复实现
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, TypeVar

T = TypeVar("T")

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


def snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并字典（override 优先），返回新字典。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def chunked(iterable: Iterable[T], size: int) -> Iterator[list[T]]:
    """把可迭代对象按 size 分批（用于批量写库 / 批量向量化）。"""
    batch: list[T] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
