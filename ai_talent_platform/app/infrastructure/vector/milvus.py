# 连接生命周期由基础设施层管理，业务适配器复用客户端。
"""唯一 Milvus 连接生命周期；不包含检索语义。
标签：[RAG组] [P0]。"""
import asyncio
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError
_client = None
_lock = asyncio.Lock()

async def init_milvus():
    global _client
    async with _lock:
        if _client is None and settings.MILVUS_ENABLED:
            # SDK 在适配层延迟导入，未开启 Milvus 时不要求安装。
            from app.adapters.vector_store.milvus_adapter import create_client
            _client = await asyncio.to_thread(create_client, settings.MILVUS_URI, settings.MILVUS_TOKEN)
    return _client

async def get_milvus():
    client = await init_milvus()
    if client is None:
        raise ExternalServiceError("请先配置并启用 MILVUS_ENABLED")
    return client

async def close_milvus():
    global _client
    async with _lock:
        if _client is not None:
            await asyncio.to_thread(_client.close)
            _client = None

async def health_check():
    client = await get_milvus()
    await asyncio.to_thread(client.list_collections)
    return {"ok": True}
