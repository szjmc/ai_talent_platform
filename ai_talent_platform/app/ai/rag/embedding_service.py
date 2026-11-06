# 文档和查询共用同一向量模型。批次返回数量和维度必须符合契约，避免错配切片主键。
"""通过 LangChain 批量向量化并校验结果。
标签：[RAG组] [P0]。"""
import math
from app.config.settings import settings
from app.ai.rag.question_embeddings import get_embeddings
from app.core.exceptions import AIError

class EmbeddingService:
    def __init__(self, embeddings=None, batch_size=None):
        batch_size = settings.EMBEDDING_BATCH_SIZE if batch_size is None else batch_size
        if batch_size < 1:
            raise ValueError("batch_size 必须为正数")
        self.embeddings = embeddings
        self.batch_size = batch_size

    async def embed_texts(self, texts):
        if not texts:
            return []
        if self.embeddings is None:
            self.embeddings = get_embeddings()
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                result = await self.embeddings.aembed_documents(list(batch))
            except AIError:
                raise
            except Exception as exc:
                raise AIError("Embedding 请求失败") from exc
            if len(result) != len(batch):
                raise AIError("Embedding 返回条数与输入不一致")
            vectors.extend(result)
        if vectors:
            dim = len(vectors[0])
            if dim != settings.EMBEDDING_DIM:
                raise AIError("Embedding 实际维度与 EMBEDDING_DIM 配置不一致")
            if not dim or any(len(v) != dim or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors):
                raise AIError("Embedding 维度不一致或包含无效向量")
        return vectors

    async def embed_query(self, text):
        return (await self.embed_texts([text]))[0]


# ====================================================================== 合并兼容层
# 说明：合并 origin/develop 时追加。develop 侧 talent_router 引入 talent_retrieval_service，
#      其模块级调用 embed_texts / embed_query 在 lz/develop 两线上均无定义（gxd 模块既有问题），
#      因阻塞应用启动，此处以纯追加方式补两个模块级便捷函数，委托给 EmbeddingService；
#      本文件既有代码一行未改。
from functools import lru_cache


@lru_cache(maxsize=1)
def _default_embedding_service() -> EmbeddingService:
    return EmbeddingService()


async def embed_texts(texts):
    """模块级便捷封装：批量向量化（委托 EmbeddingService）。"""
    return await _default_embedding_service().embed_texts(texts)


async def embed_query(text):
    """模块级便捷封装：单条查询向量化（委托 EmbeddingService）。"""
    return await _default_embedding_service().embed_query(text)
