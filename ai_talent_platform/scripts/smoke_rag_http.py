"""真实 ASGI 路由认证与权限验收；不覆盖认证依赖，不启动自动建表。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio,json,secrets
from uuid import uuid4
from sqlalchemy import select,delete
from httpx import AsyncClient,ASGITransport
from app.core.application import create_application
from app.core.security import hash_password,create_access_token
from app.models.user import User
from app.models.role import Role,UserRole
from app.models.organization import Organization
from app.models.knowledge import KnowledgeDoc,KnowledgeChunk
from app.models.file import File
from app.infrastructure.database.session import get_session_factory,close_db
from app.infrastructure.vector.milvus import close_milvus
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.repositories.rag_ingest_job import PREFIX

async def main():
 run=uuid4().hex[:12]; password=secrets.token_hex(12)+'A1'
 users=[];orgs=[];created_roles=[];doc_ids=[];file_id=None;key=None
 category='http_'+run
 try:
  async with get_session_factory()() as db:
   roles={}
   for code in ['admin','employee','hr']:
    role=await db.scalar(select(Role).where(Role.code==code))
    if role is None:
     role=Role(code=code,name='HTTP test '+run);db.add(role);await db.flush();created_roles.append(role.id)
    roles[code]=role
   for suffix in ['a','b']:
    org=Organization(code='http_'+run+suffix,name='HTTP test '+run+suffix);db.add(org);await db.flush();orgs.append(org.id)
   for name,role,org in [('admin','admin',orgs[0]),('employee','employee',orgs[0]),('other','employee',orgs[1]),('hr','hr',orgs[0]),('none',None,orgs[0])]:
    u=User(username='http_'+run+'_'+name,password_hash=hash_password(password),organization_id=org,is_active=True,is_superuser=False)
    u.roles=[roles[role]] if role else [];db.add(u);await db.flush();users.append((name,u.id,u.username))
   for scope,extra in [('public',{}),('dept',{'organization_id':orgs[0]}),('role',{'allowed_roles':['manager','hr']})]:
    d=KnowledgeDoc(title=scope,category=category,status='indexed',source_type='text',permission_scope=scope,**extra);db.add(d);await db.flush();doc_ids.append(d.id)
    db.add(KnowledgeChunk(doc_id=d.id,chunk_index=0,content='scope-test-'+run))
   await db.commit()
  # ASGITransport 执行真实路由/中间件/数据库依赖，避免 lifespan 的现有自动建表逻辑。
  app=create_application()
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
   query={'question':'scope-test-'+run,'category':category,'mode':'keyword'}
   assert (await c.post('/api/v1/knowledge/retrieve',json=query)).status_code==401
   assert (await c.post('/api/v1/knowledge/retrieve',json=query,headers={'Authorization':'Bearer invalid'})).status_code==401
   print('ANONYMOUS_INVALID_TOKEN_PASS',flush=True)
   headers={}
   for name,uid,username in users:
    response=await c.post('/api/v1/auth/login',json={'username':username,'password':password})
    assert response.status_code==200,(name,response.status_code,response.text[:300])
    payload=response.json()['data'];headers[name]={'Authorization':'Bearer '+payload['access_token']}
    if name=='admin':
     assert (await c.post('/api/v1/knowledge/retrieve',json=query,headers={'Authorization':'Bearer '+payload['refresh_token']})).status_code==401
   print('REAL_LOGIN_REFRESH_TYPE_PASS',flush=True)
   expected={'employee':{doc_ids[0],doc_ids[1]},'other':{doc_ids[0]},'hr':set(doc_ids)}
   for name,visible in expected.items():
    response=await c.post('/api/v1/knowledge/retrieve',json=query,headers=headers[name])
    assert response.status_code==200,response.text[:300]
    assert {h['doc_id'] for h in response.json()['data']}==visible,(name,response.text)
   print('DEPARTMENT_ROLE_SCOPE_PASS',flush=True)
   assert (await c.post('/api/v1/knowledge/retrieve',json=query,headers=headers['none'])).status_code==403
   assert (await c.post('/api/v1/knowledge/docs/upload',data={'category':category},files={'file':('test.txt',b'test','text/plain')},headers=headers['employee'])).status_code==403
   print('NO_PERMISSION_EMPLOYEE_UPLOAD_DENIED_PASS',flush=True)
   response=await c.post('/api/v1/knowledge/docs/upload',data={'category':category},files={'file':('http-test.txt',('HTTP upload '+run+' Python FastAPI').encode(),'text/plain')},headers=headers['admin'])
   assert response.status_code==200,response.text[:400]
   result=response.json()['data'];doc_ids.append(result['id']);file_id=result['file_id']
   assert result['status']=='indexed',result
   async with get_session_factory()() as db:
    f=await db.get(File,file_id);key=f.file_path
   response=await c.post('/api/v1/knowledge/retrieve',json={'question':'Python FastAPI','category':category,'mode':'hybrid','use_rerank':True},headers=headers['employee'])
   assert response.status_code==200 and any(h['doc_id']==result['id'] for h in response.json()['data']),response.text[:400]
   assert (await c.post('/api/v1/knowledge/docs/'+str(result['id'])+'/retry',headers=headers['employee'])).status_code==403
   assert (await c.post('/api/v1/knowledge/docs/'+str(result['id'])+'/retry',headers=headers['admin'])).status_code==200
   print('HTTP_UPLOAD_SEMANTIC_RETRY_PASS',flush=True)
 finally:
  if doc_ids:
   for did in doc_ids: await MilvusAdapter().delete_document(did)
  async with get_session_factory()() as db:
   if doc_ids:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(doc_ids)))
    await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(doc_ids)))
   if file_id: await db.execute(delete(File).where(File.id==file_id))
   ids=[u[1] for u in users]
   if ids:
    await db.execute(delete(UserRole).where(UserRole.user_id.in_(ids)))
    await db.execute(delete(User).where(User.id.in_(ids)))
   if orgs: await db.execute(delete(Organization).where(Organization.id.in_(orgs)))
   if created_roles: await db.execute(delete(Role).where(Role.id.in_(created_roles)))
   await db.commit()
  storage=get_storage('minio')
  if key: await storage.delete(key)
  for did in doc_ids:
   for snapshot in await storage.client.list_keys('_rag_bm25/v2/'+str(did)+'/'): await storage.delete(snapshot)
  for path in await storage.client.list_keys(PREFIX):
   job=json.loads(await storage.read(path))
   if job.get('doc_id') in doc_ids: await storage.delete(path)
  await close_db();await close_milvus()
  print('HTTP_TEST_DATA_CLEANED',flush=True)
if __name__=='__main__':asyncio.run(main())
