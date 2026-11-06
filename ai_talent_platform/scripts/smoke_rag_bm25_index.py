"""真实MinIO快照复用、增量版本与模型重排验证。
标签：[RAG组] [P1]。"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from app.ai.rag.bm25_index import search_index,_cache
from app.adapters.storage.base_storage import get_storage
from app.adapters.llm.rerank_adapter import rerank
class Repo:
 def __init__(self): self.calls=0
 async def lexical_corpus(self,db,ids):
  self.calls+=1
  return [SimpleNamespace(id=101,content='Python后端岗位要求FastAPI和MySQL'),SimpleNamespace(id=102,content='员工培训费用报销需要发票')]
async def main():
 doc=SimpleNamespace(id=int(uuid4().hex[:12],16),version=1,indexed_at='test-v1',chunk_count=2)
 repo=Repo();storage=get_storage('minio');prefix='_rag_bm25/v2/'+str(doc.id)+'/'
 try:
  first=await search_index(None,repo,[doc],'Python FastAPI',5)
  assert first[0][0].id==101 and repo.calls==1
  _cache.clear()
  await search_index(None,repo,[doc],'培训 发票',5)
  assert repo.calls==1
  print('PERSISTED_SNAPSHOT_REUSED_PASS')
  doc.version=2
  await search_index(None,repo,[doc],'Python',5)
  assert repo.calls==2
  print('DOCUMENT_VERSION_INCREMENTAL_PASS')
  assert not await search_index(None,repo,[],'Python',5)
  print('EMPTY_PERMISSION_SCOPE_PASS')
  results=await rerank('Python后端技能',[{'chunk_id':102,'content':'报销需要发票','score':.1},{'chunk_id':101,'content':'Python后端开发需要FastAPI和MySQL','score':.05}])
  assert results[0]['chunk_id']==101
  print('REAL_MODEL_RERANK_PASS')
 finally:
  for key in await storage.client.list_keys(prefix): await storage.delete(key)
  _cache.clear()
if __name__=='__main__':asyncio.run(main())
