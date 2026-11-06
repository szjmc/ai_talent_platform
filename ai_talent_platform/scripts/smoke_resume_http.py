"""真实HTTP及数据库链路测试；结构化模型使用明确标注的测试替身。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio,secrets
from uuid import uuid4
from unittest.mock import patch,AsyncMock
from sqlalchemy import delete,select
from httpx import AsyncClient,ASGITransport
from app.core.application import create_application
from app.core.security import hash_password
from app.models.user import User
from app.models.role import Role,UserRole
from app.models.resume import Resume,ResumeExperience
from app.models.file import File
from app.schemas.resume import ParsedResume
from app.infrastructure.database.session import get_session_factory,close_db
from app.adapters.storage.base_storage import get_storage

async def main():
 real_model = '--real-model' in __import__('sys').argv
 run=uuid4().hex[:12];password=secrets.token_hex(12)+'A1';ids=[];rid=fid=key=None;created_role=None
 try:
  async with get_session_factory()() as db:
   role=await db.scalar(select(Role).where(Role.code=='hr'))
   if role is None:
    role=Role(code='hr',name='test '+run);db.add(role);await db.flush();created_role=role.id
   for suffix in ['a','b']:
    u=User(username='resume_'+run+suffix,password_hash=hash_password(password),is_active=True,roles=[role]);db.add(u);await db.flush();ids.append(u.id)
   await db.commit()
  app=create_application()
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
   headers=[]
   for suffix in ['a','b']:
    response=await c.post('/api/v1/auth/login',json={'username':'resume_'+run+suffix,'password':password});assert response.status_code==200,response.text
    headers.append({'Authorization':'Bearer '+response.json()['data']['access_token']})
   assert (await c.get('/api/v1/resumes')).status_code==401
   payload=('张三 '+run+' Python FastAPI 三年工作经验').encode()
   response=await c.post('/api/v1/resumes/upload',headers=headers[0],files={'file':('resume.txt',payload,'text/plain')})
   assert response.status_code==200,response.text
   row=response.json()['data'];rid=row['id'];fid=row['file_id']
   async with get_session_factory()() as db:
    f=await db.get(File,fid);key=f.file_path;assert f.biz_type=='resume'
   assert (await c.get('/api/v1/resumes/'+str(rid),headers=headers[1])).status_code==403
   assert (await c.post('/api/v1/resumes/upload',headers=headers[1],files={'file':('resume.txt',payload,'text/plain')})).status_code==403
   response=await c.post('/api/v1/resumes/upload',headers=headers[0],files={'file':('resume.txt',payload,'text/plain')})
   assert response.json()['data']['duplicate']
   print('UPLOAD_DEDUP_OWNER_ISOLATION_PASS',flush=True)
   parsed=ParsedResume(basic_info={'name':'张三'},skills=['Python','FastAPI'],work_experience=[{'company':'测试公司','title':'开发工程师'}])
   if real_model:
    response=await c.post('/api/v1/resumes/'+str(rid)+'/parse',headers=headers[0],json={})
    assert response.status_code==202,response.text
   else:
    with patch('app.ai.workflows.resume_workflow.run_resume_workflow',new=AsyncMock(return_value=parsed)):
     response=await c.post('/api/v1/resumes/'+str(rid)+'/parse',headers=headers[0],json={})
     assert response.status_code==202,response.text
   response=await c.get('/api/v1/resumes/'+str(rid)+'/result',headers=headers[0])
   assert response.json()['data']['status']=='success',response.text
   assert {'Python','FastAPI'}.issubset(set(response.json()['data']['parsed']['skills']))
   print('REAL_DEEPSEEK_PARSE_PERSISTENCE_PASS' if real_model else 'BACKGROUND_PARSE_PERSISTENCE_PASS_WITH_MODEL_STUB',flush=True)
   response=await c.get('/api/v1/resumes',headers=headers[0]);assert response.status_code==200 and any(r['id']==rid for r in response.json()['data']['items'])
   assert (await c.post('/api/v1/resumes/'+str(rid)+'/bind',headers=headers[0],json={'talent_id':9223372036854775807})).status_code==404
   print('LIST_RESULT_BIND_VALIDATION_PASS',flush=True)
   with patch('app.ai.workflows.resume_workflow.run_resume_workflow',new=AsyncMock(side_effect=RuntimeError('injected model outage'))):
    response=await c.post('/api/v1/resumes/'+str(rid)+'/parse',headers=headers[0],json={'force':True})
    assert response.status_code==202,response.text
   response=await c.get('/api/v1/resumes/'+str(rid)+'/result',headers=headers[0])
   assert response.json()['data']['status']=='failed' and response.json()['data']['parsed'] is None,response.text
   print('FORCE_REPARSE_FAILURE_STATUS_PASS',flush=True)
 finally:
  async with get_session_factory()() as db:
   if rid:
    await db.execute(delete(ResumeExperience).where(ResumeExperience.resume_id==rid));await db.execute(delete(Resume).where(Resume.id==rid))
   if fid:await db.execute(delete(File).where(File.id==fid))
   if ids:
    await db.execute(delete(UserRole).where(UserRole.user_id.in_(ids)));await db.execute(delete(User).where(User.id.in_(ids)))
   if created_role:await db.execute(delete(Role).where(Role.id==created_role))
   await db.commit()
  if key:await get_storage().delete(key)
  await close_db();print('RESUME_TEST_DATA_CLEANED',flush=True)
if __name__=='__main__':asyncio.run(main())
