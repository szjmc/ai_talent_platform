"""可重复的真实服务检索评测；仅清理本次UUID标记数据。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio,json,time,math
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import delete
from app.models.knowledge import KnowledgeDoc,KnowledgeChunk
from app.models.file import File
from app.infrastructure.database.session import get_session_factory,close_db
from app.infrastructure.vector.milvus import close_milvus
from app.services.knowledge_service import KnowledgeService
from app.schemas.knowledge import RetrieveRequest
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.adapters.storage.base_storage import get_storage
from app.repositories.rag_ingest_job import PREFIX

async def main():
 root=Path(__file__).resolve().parents[1]
 suite=json.loads((root/'tests/fixtures/rag_eval.json').read_text(encoding='utf-8'))
 run=uuid4().hex;category='eval_'+run
 user=SimpleNamespace(id=None,is_superuser=False,organization_id=None,role_codes=['employee'])
 assets=[];expected={};details=[];report={'run':run,'dataset':suite['description'],'queries':len(suite['queries']),'documents':len(suite['documents'])}
 storage=get_storage('minio')
 try:
  for sample in suite['documents']:
   async with get_session_factory()() as db:
    service=KnowledgeService(db)
    try:
     result=await service.upload(sample['text'].encode(),sample['key']+'.txt','text/plain',category,user)
     f=await db.get(File,result['file_id']);assets.append((result['id'],result['file_id'],f.file_path))
     assert result['status']=='indexed',result
     await db.commit();await service.finish_jobs(True)
     expected[sample['key']]=result['id']
    finally:
     await db.rollback();await service.finish_jobs(False)
  print('EVAL_FIXTURES_READY',flush=True)
  for mode,rerank in [('semantic',False),('keyword',False),('hybrid',False),('hybrid',True)]:
   for sample in suite['queries']:
    async with get_session_factory()() as db:
     service=KnowledgeService(db)
     started=time.perf_counter()
     hits=await service.search(RetrieveRequest(question=sample['question'],category=category,mode=mode,use_rerank=rerank,top_k=3),user)
     elapsed=(time.perf_counter()-started)*1000
     rank=next((i for i,h in enumerate(hits,1) if h['doc_id']==expected[sample['expected']]),None)
     details.append({'mode':mode,'rerank':rerank,'question':sample['question'],'rank':rank,'latency_ms':round(elapsed,2)})
   print('MODE_DONE',mode,rerank,flush=True)
  report['metrics']=[]
  for mode,rerank in [('semantic',False),('keyword',False),('hybrid',False),('hybrid',True)]:
   subset=[r for r in details if r['mode']==mode and r['rerank']==rerank];times=sorted(r['latency_ms'] for r in subset)
   report['metrics'].append({'mode':mode,'rerank':rerank,'hit_at_1':sum(r['rank']==1 for r in subset)/len(subset),'hit_at_3':sum(r['rank'] is not None for r in subset)/len(subset),'mrr_at_3':sum(1/r['rank'] if r['rank'] else 0 for r in subset)/len(subset),'mean_ms':round(sum(times)/len(times),2),'p95_ms':times[math.ceil(.95*len(times))-1]})
  report['details']=details
  report['limits']='8题合成开发集，每种策略各执行一遍，含首次缓存开销；非独立业务验收集，不代表95%准确率或并发SLA。'
 finally:
  ids=[a[0] for a in assets]
  for did,fid,key in assets:
   await MilvusAdapter().delete_document(did);await storage.delete(key)
   for snapshot in await storage.client.list_keys('_rag_bm25/v2/'+str(did)+'/'):await storage.delete(snapshot)
  async with get_session_factory()() as db:
   if ids:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(ids)))
    await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(ids)))
    await db.execute(delete(File).where(File.id.in_([a[1] for a in assets])))
    await db.commit()
  for path in await storage.client.list_keys(PREFIX):
   job=json.loads(await storage.read(path))
   if job.get('doc_id') in ids:await storage.delete(path)
  await close_db();await close_milvus()
  report['test_data_cleaned']=True
  (root/'docs/rag_eval_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(report.get('metrics',[])),flush=True)
if __name__=='__main__':asyncio.run(main())
