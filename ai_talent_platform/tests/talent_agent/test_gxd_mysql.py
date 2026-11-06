"""
gxd-真实MySQL的业务集成测试；仅连接pytest专用库，模型调用使用替身。

标签：[RAG组] [P2]（MySQL 联调用例，需外部服务）
"""
import json
from uuid import uuid4
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from app.config.settings import settings
from app.core.dependencies import get_db, get_current_user
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import DuplicateError, NotFoundError
from app.models.user import User
from app.models.talent import Talent
from app.models.resume import Resume
from app.services.talent_profile_service import TalentService
from app.schemas.talent import TalentUpdate, TalentMergeRequest

@pytest.fixture
async def mysql_factory(mysql_ready):
    """gxd-每用例独立无池引擎，避免跨事件循环复用连接。"""
    engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()

@pytest.fixture
async def actor(mysql_factory):
    """gxd-在独立测试库创建能执行完整业务流程的测试账号。"""
    async with mysql_factory() as db:
        user=User(username="gxd_"+uuid4().hex[:16],password_hash="not-a-login-password",is_superuser=True,is_active=True)
        db.add(user);await db.commit()
        await db.refresh(user,attribute_names=["roles"])
        return user

async def test_real_mysql_partial_update_merge_and_rollback(mysql_factory,actor,monkeypatch):
    """gxd-真实SQL验证部分修改、唯一冲突回滚、合并迁移和逻辑删除。"""
    monkeypatch.setattr(TalentService,"refresh_derived",AsyncMock(return_value={"status":"test-only"}))
    async with mysql_factory() as db:
        a=Talent(name="甲",mobile="gxd"+uuid4().hex[:12],school="原学校",created_by=actor.id)
        b=Talent(name="乙",mobile="gxd"+uuid4().hex[:12],school="另一学校",created_by=actor.id)
        db.add_all([a,b]);await db.commit();aid,bid=a.id,b.id;mobile=b.mobile
    async with mysql_factory() as db:
        service=TalentService(db,user=actor)
        obj=await service.update_talent(aid,TalentUpdate(work_years=3))
        assert obj.school=="原学校" and obj.work_years==3
        await db.commit()
    async with mysql_factory() as db:
        with pytest.raises(DuplicateError):
            await TalentService(db,user=actor).update_talent(aid,TalentUpdate(name="不能保存",mobile=mobile))
        await db.rollback()
    async with mysql_factory() as db:
        service=TalentService(db,user=actor)
        assert (await service.get_talent(aid)).name=="甲"
        result=await service.merge(TalentMergeRequest(target_id=aid,source_ids=[bid],confirmed=True))
        assert result["conflicts"]
        await db.commit()
    async with mysql_factory() as db:
        service=TalentService(db,user=actor)
        with pytest.raises(NotFoundError):await service.get_talent(bid)
        assert (await service.get_talent(aid)).school=="原学校"

async def test_http_batch_direct_pipeline(mysql_factory,actor,monkeypatch,tmp_path):
    """gxd-HTTP批量上传→请求内LangGraph→原有简历和画像表；替身仅模型与向量调用。"""
    from app.routers.resume_router import router as resumes
    from app.routers.talent_router import router as talents
    from app.ai.core.llm_manager import llm_manager
    from app.services.talent_retrieval_service import TalentRetrievalService
    monkeypatch.setattr(settings,"UPLOAD_DIR",str(tmp_path))
    app=FastAPI();app.include_router(resumes,prefix="/api/v1");app.include_router(talents,prefix="/api/v1")
    register_exception_handlers(app)
    async def db_dependency():
        """gxd-请求成功提交，失败回滚测试数据库事务。"""
        async with mysql_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback();raise
    async def user_dependency():
        """gxd-仅测试环境替换身份依赖，生产代码仍校验JWT。"""
        return actor
    async def fake_chat(messages,*args,**kwargs):
        """gxd-模型固定输出，用于验证工程流程而非评估模型准确率。"""
        if kwargs.get("purpose")=="resume":
            return Obj(content=json.dumps({"basic_info":{"name":"集成样例"},"skills":["Python"]}),model="test-model")
        return Obj(content=json.dumps({"summary":"有Python技能记录", "labels":[]}),model="test-model")
    app.dependency_overrides[get_db]=db_dependency;app.dependency_overrides[get_current_user]=user_dependency
    monkeypatch.setattr(llm_manager,"chat",fake_chat)
    monkeypatch.setattr(TalentRetrievalService,"sync_index",AsyncMock(return_value={"status":"ready"}))
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        files=[("files",("one.txt",f"集成样例 熟悉Python {uuid4().hex}".encode(),"text/plain")),("files",("bad.exe",b"bad","application/octet-stream"))]
        response=await client.post("/api/v1/resumes/upload/batch",files=files)
        assert response.status_code==200,response.text
        result=response.json()["data"]
        assert len(result["accepted"])==1 and len(result["rejected"])==1
        parsed=result["accepted"][0]["result"]
        assert parsed["status"]=="success",parsed
        tid=parsed["talent_id"]
        response=await client.get(f"/api/v1/talents/{tid}")
        assert response.status_code==200 and response.json()["data"]["name"]=="集成样例"
        rid=result["accepted"][0]["resume_id"]
        response=await client.get(f"/api/v1/resumes/{rid}/result")
        assert response.json()["data"]["parsed"]["skills"]==["Python"]
        response=await client.patch(f"/api/v1/talents/{tid}",json={"school":None})
        assert response.status_code==200,response.text
        response=await client.post(f"/api/v1/resumes/{rid}/parse",json={})
        assert response.status_code==200,response.text

async def test_database_constraint_rolls_back_all_patch_fields(mysql_factory,actor):
    """gxd-真实外键失败时姓名与部门修改一起回滚，原记录保持不变。"""
    async with mysql_factory() as db:
        obj=Talent(name="原姓名",created_by=actor.id)
        db.add(obj);await db.commit();tid=obj.id
    async with mysql_factory() as db:
        with pytest.raises(DuplicateError):
            await TalentService(db,user=actor).update_talent(tid,TalentUpdate(name="不能保存",organization_id=999999999))
        await db.rollback()
    async with mysql_factory() as db:
        obj=await TalentService(db,user=actor).get_talent(tid)
        assert obj.name=="原姓名" and obj.organization_id is None

@pytest.mark.skipif(__import__('os').getenv("GXD_MILVUS_TEST") != "1", reason="设置GXD_MILVUS_TEST=1验证MySQL与Milvus联动")
async def test_mysql_vector_index_version_and_cited_answer(mysql_factory,actor,monkeypatch):
    """gxd-真实MySQL/Milvus验证索引、引用和版本失效；只替换Embedding/聊天外部模型。"""
    import app.services.talent_retrieval_service as mod
    from app.config.talent_config import talent_settings as cfg
    from app.schemas.talent import TalentAskRequest, TalentSemanticSearchRequest
    monkeypatch.setattr(settings,"MILVUS_ENABLED",True)
    monkeypatch.setattr(cfg,"collection","gxd_verify_"+uuid4().hex)
    monkeypatch.setattr(cfg,"embedding_dim",2)
    async def vectors(texts):
        """gxd-提供二维确定性向量，只验证存储检索流程而非语义质量。"""
        return [[1.,0.] for _ in texts]
    monkeypatch.setattr(mod,"embed_texts",vectors)
    monkeypatch.setattr(mod,"embed_query",AsyncMock(return_value=[1.,0.]))
    async def answer(messages,*a,**kw):
        """gxd-从实际召回证据构建可核对的测试引用。"""
        evidence=json.loads(messages[1]["content"])["evidence"][0]
        return Obj(content=json.dumps({"answer":"档案记录了Python技能", "citations":[{"chunk_id":evidence["chunk_id"],"quote":evidence["text"][:30]}]}),model="test-model")
    monkeypatch.setattr(mod.llm_manager,"chat",answer)
    adapter=mod.MilvusAdapter()
    try:
        async with mysql_factory() as db:
            obj=Talent(name="向量用例",skill_tags=["Python"],created_by=actor.id)
            db.add(obj);await db.commit();tid=obj.id
        async with mysql_factory() as db:
            service=mod.TalentRetrievalService(db,user=actor)
            result=await service.sync_index(tid)
            assert result["chunks"]>=2
            await db.commit()
        async with mysql_factory() as db:
            service=mod.TalentRetrievalService(db,user=actor)
            result=await service.search(TalentSemanticSearchRequest(query="Python"))
            assert result["items"][0]["talent_id"]==tid
            result=await service.ask(TalentAskRequest(question="技能是什么",talent_ids=[tid]))
            assert result["citations"][0]["talent_id"]==tid
            await service.update_talent(tid,TalentUpdate(skill_tags=["Java"]))
            await db.commit()
        async with mysql_factory() as db:
            service=mod.TalentRetrievalService(db,user=actor)
            hits,_,pending=await service.retrieve("Python",3,ids=[tid])
            assert hits==[] and pending is None
            await service.sync_index(tid);await db.commit()
        async with mysql_factory() as db:
            service=mod.TalentRetrievalService(db,user=actor)
            hits,_,pending=await service.retrieve("Java",3,ids=[tid])
            assert hits and all(x["version"]==2 for x in hits)
    finally:
        await adapter.drop(cfg.collection)