# 缓存键绑定文档版本，缓存内容是分词后的语料。每个问题仍重新计算排名，发布时从当前事务重建快照。
"""按文档版本增量构建 BM25 语料快照，MinIO 持久化。
标签：[RAG组] [P0]。"""
import asyncio
import hashlib
import json
from collections import OrderedDict
from types import SimpleNamespace
from app.adapters.storage.base_storage import get_storage
from app.ai.rag.lexical_search import bm25_rank, tokenize

_cache=OrderedDict()

async def search_index(db, repo, docs, query, limit, strict=False):
    rows=[]
    storage=get_storage('minio')
    # 调用方传入的 docs 必须已经完成权限和分类过滤；从不加载不可见文档快照。
    for doc in docs:
        version=hashlib.sha256(str((doc.id,doc.version,doc.indexed_at,doc.chunk_count)).encode()).hexdigest()[:24]
        key='_rag_bm25/v2/'+str(doc.id)+'/'+version+'.json'
        # 发布事务必须验证持久化结果，不能仅凭进程内缓存确认发布成功。
        payload=None if strict else _cache.get(key)
        if payload is None:
            if not strict:
                try:
                    payload=json.loads(await storage.read(key)) if await storage.exists(key) else None
                except Exception:
                    # 快照只是缓存；对象存储暂不可用时从已授权MySQL切片重建，不扩大权限。
                    payload=None
            # 严格发布始终以本事务SQL切片重建，避免前次回滚留下相同秒级指纹的快照。
            if payload is None:
                chunks=await repo.lexical_corpus(db,[doc.id])
                payload=await asyncio.to_thread(lambda:[{'id':c.id,'content':c.content,'tokens':tokenize(c.content)} for c in chunks])
                try:
                    await storage.client.put_object(key,json.dumps(payload,ensure_ascii=False).encode(),'application/json')
                except Exception:
                    if strict:
                        raise
                    import logging
                    logging.getLogger(__name__).warning('BM25快照写入失败，当前请求使用MySQL重建结果 doc_id=%s',doc.id)
            _cache[key]=payload
            _cache.move_to_end(key)
            while len(_cache)>128: _cache.popitem(last=False)
        rows.extend(SimpleNamespace(**item) for item in payload)
    return await asyncio.to_thread(bm25_rank,query,rows,limit)
