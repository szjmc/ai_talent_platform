"""合并入口真实HTTP与删除恢复验证。
标签：[RAG组] [P1]
"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio,json
from uuid import uuid4
from sqlalchemy import select,delete
from httpx import AsyncClient,ASGITransport
from app.models.user import User
from app.models.file import File
from app.models.knowledge import KnowledgeDoc,KnowledgeChunk
from app.core.security import hash_password
from app.core.application import create_application
from app.infrastructure.database.session import get_session_factory,close_db
from app.infrastructure.vector.milvus import close_milvus
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.services.rag_recovery import recover
from app.repositories.rag_ingest_job import PREFIX
async def main():
 run=uuid4().hex;uid=did=fid=None;key=None;storage=get_storage('minio')
 try:
  async with get_session_factory()() as db:
   user=User(username='bridge_'+run[:12],password_hash=hash_password(run),is_superuser=True,is_active=True,roles=[])
   db.add(user);await db.flush();uid=user.id;await db.commit()
  async with AsyncClient(transport=ASGITransport(app=create_application()),base_url='http://test') as c:
   r=await c.post('/api/v1/auth/login',json={'username':'bridge_'+run[:12],'password':run});assert r.status_code==200
   headers={'Authorization':'Bearer '+r.json()['data']['access_token']}
   assert (await c.post('/api/v1/files/upload',data={'category':'policy'},files={'file':('test.txt',b'test','text/plain')})).status_code==401
   r=await c.post('/api/v1/files/upload',headers=headers,data={'category':'policy'},files={'file':('bridge.txt',('制度联调'+run).encode(),'text/plain')})
   assert r.status_code==200,r.text
   did=r.json()['data']['id'];fid=r.json()['data']['file_id'];assert r.json()['data']['status']=='indexed',r.text
   async with get_session_factory()() as db:
    f=await db.get(File,fid);key=f.file_path
   r=await c.get('/api/v1/ingestion/chunks/fetch',headers=headers,params={'category':'policy','file_id':fid})
   assert r.status_code==200 and r.json()['data']['total']>=1,r.text
   r=await c.post('/api/v1/ingestion/trigger',headers=headers,json={'file_id':str(fid),'bucket_name':'ai-talent','object_name':key,'file_name':'bridge.txt','category':'policy'})
   assert r.status_code==200 and r.json()['data']['id']==did,r.text
   print('BRIDGE_UPLOAD_TRIGGER_FETCH_PASS',flush=True)
   r=await c.post('/api/v1/ingestion/delete',headers=headers,json={'file_id':str(fid),'category':'policy'})
   assert r.status_code==202,r.text
   await recover()
   r=await c.get('/api/v1/ingestion/chunks/fetch',headers=headers,params={'category':'policy','file_id':fid})
   assert r.status_code==200 and r.json()['data']['total']==0,r.text
   print('BRIDGE_DELETE_RECOVERY_PASS',flush=True)
 finally:
  if did:
   await MilvusAdapter().delete_document(did)
   async with get_session_factory()() as db:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id==did));await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id==did));await db.execute(delete(File).where(File.id==fid));await db.commit()
   if key:await storage.delete(key)
   for prefix in ['_rag_bm25/v2/'+str(did)+'/',PREFIX]:
    for path in await storage.client.list_keys(prefix):
     if prefix!=PREFIX or json.loads(await storage.read(path)).get('doc_id')==did:await storage.delete(path)
  if uid:
   async with get_session_factory()() as db:
    await db.execute(delete(User).where(User.id==uid));await db.commit()
  await close_db();await close_milvus()
  print('BRIDGE_TEST_CLEANED',flush=True)
if __name__=='__main__':asyncio.run(main())
