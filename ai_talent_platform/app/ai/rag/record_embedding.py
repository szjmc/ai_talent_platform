"""
模块：app/ai/rag/record_embedding.py
职责：面试记录表向量化（测评组自有，独立于 RAG 组的 embedding_service）。

设计说明：
- 面试记录表是「短中文文本 + 少量结构化字段」，本模块用「确定性本地哈希嵌入」
  保证维度稳定（EMBED_DIM=128）、离线可复现，不依赖远程向量模型；
- 与 RAG 组的远程向量空间（EMBEDDING_DIM=1024）物理隔离：面试记录集合
  interview_record_vectors 是独立集合，写入与召回都走本模块，闭环一致；
- 组卷（题库检索）请走 RAG 组 question_bank_service / question_embeddings，
  不要复用本模块，避免向量空间混用。

对外 API：
    embed_query(text)  -> list[float]
    embed_texts(texts) -> list[list[float]]
    embed_dim()        -> int
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from app.config.logging_config import get_logger
from app.config.settings import settings

logger = get_logger(__name__)


def embed_dim() -> int:
    """面试记录集合的向量维度（本地哈希实现，固定 EMBED_DIM）。"""
    return int(getattr(settings, "EMBED_DIM", 128) or 128)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化（确定性本地哈希，同一文本永远得到同一向量）。"""
    return [_local_embed(t, embed_dim()) for t in texts]


async def embed_query(text: str) -> list[float]:
    """单条文本向量化。"""
    return (await embed_texts([text]))[0]


# ================================================================ 本地哈希嵌入
_TOKEN_RE = re.compile(r"[A-Za-z]+|[一-龥]|\d+")


def _tokens(text: str) -> list[str]:
    """英文按词、中文按单字（含二元组）、数字按串切分。"""
    raw = _TOKEN_RE.findall((text or "").lower())
    bigrams = [raw[i] + raw[i + 1] for i in range(len(raw) - 1)]
    return raw + bigrams


def _local_embed(text: str, dim: int) -> list[float]:
    """字符 n-gram 哈希 + TF 加权 + L2 归一化（确定性，可复现）。"""
    vec = [0.0] * dim
    toks = _tokens(text)
    if not toks:
        return vec
    for tok, tf in Counter(toks).items():
        weight = 1.0 + math.log(1.0 + tf)
        for salt in ("a", "b", "c"):
            h = hashlib.md5((salt + tok).encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


__all__ = ["embed_texts", "embed_query", "embed_dim"]
