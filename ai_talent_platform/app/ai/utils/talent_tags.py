"""
gxd-加载业务标签词表，统一模型标签名称，避免同义标签重复。

标签：[RAG组] [P1]（人才标签抽取与规范化）
"""
import json
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def tag_catalog():
    """gxd-从配置文件读取标签定义，重启服务后加载更新词表。"""
    path = Path(__file__).resolve().parents[2] / "config" / "talent_tags.json"
    return json.loads(path.read_text(encoding="utf-8"))["tags"]

def normalize_labels(labels, source):
    """gxd-标签名按词表归一化，并核对每条标签的原文证据。"""
    lookup = {}
    for tag in tag_catalog():
        for alias in [tag["name"], *tag.get("aliases", [])]:
            lookup[alias.casefold()] = tag
    result, seen = [], set()
    for label in labels:
        tag = lookup.get(label.name.strip().casefold())
        if not tag or label.evidence not in source or tag["name"] in seen:
            continue
        seen.add(tag["name"])
        label.name, label.category = tag["name"], tag["category"]
        result.append(label)
    return result