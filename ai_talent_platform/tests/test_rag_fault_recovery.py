"""暂态故障和模型结构修复验证。
标签：[RAG组] [P1]
"""
from types import SimpleNamespace
import pytest
import httpx
@pytest.mark.asyncio
async def test_s3_retries_502():
    from app.infrastructure.storage.s3_client import S3Client
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(502 if len(calls)<3 else 200,content=b'valid')
    client=S3Client(endpoint='localhost:9000',access_key='test',secret_key='test',bucket='test',secure=False,transport=httpx.MockTransport(handler))
    assert await client.get_object('test.txt')==b'valid'
    assert len(calls)==3
@pytest.mark.asyncio
async def test_bm25_falls_back_to_authorized_mysql(monkeypatch):
    from app.ai.rag import bm25_index as index
    index._cache.clear()
    class Storage:
        client=None
        async def exists(self,key):raise OSError('offline')
        async def put_object(self,*args):raise OSError('offline')
    storage=Storage();storage.client=storage
    monkeypatch.setattr(index,'get_storage',lambda *args:storage)
    class Repo:
        async def lexical_corpus(self,db,ids):
            assert ids==[42]
            return [SimpleNamespace(id=7,content='Python FastAPI')]
    result=await index.search_index(None,Repo(),[SimpleNamespace(id=42,version=1,indexed_at=None,chunk_count=1)],'FastAPI',5)
    assert result[0][0].id==7
    index._cache.clear()
@pytest.mark.asyncio
async def test_resume_repairs_invalid_fields(monkeypatch):
    from app.ai.workflows import resume_workflow as flow
    calls=[]
    async def chat(*args,**kwargs):
        calls.append(args)
        return SimpleNamespace(content='{"skills": {"bad":1}}' if len(calls)==1 else '{"skills":["Python"],"education":null,"basic_info":null}')
    monkeypatch.setattr(flow.llm_manager,'chat',chat)
    result=await flow.run_resume_workflow('合成简历 Python')
    assert result.skills==['Python'] and result.education==[] and len(calls)==2
@pytest.mark.asyncio
async def test_recovery_skips_unreadable_log(monkeypatch):
    from app.services import rag_recovery as recovery
    calls=[]
    class Storage:
        client=None
        async def list_keys(self,prefix):return ['bad.json','good.json']
        async def read(self,path):
            calls.append(path)
            if path=='bad.json':raise OSError('offline')
            return b'{"state":"done"}'
    storage=Storage();storage.client=storage
    monkeypatch.setattr(recovery,'get_storage',lambda *args:storage)
    await recovery.recover()
    assert calls==['bad.json','good.json']
