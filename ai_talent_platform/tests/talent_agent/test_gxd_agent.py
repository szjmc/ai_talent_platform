"""
gxd-人才模块边界与链路测试；模型和向量外部调用使用明确的测试替身。

标签：[RAG组] [P2]（人才 Agent 联调用例，需外部服务）
"""
import json
from types import SimpleNamespace, SimpleNamespace as Obj
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.ai.context import AIContext
from app.schemas.talent_resume_schema import ResumeParsedData, ResumeBindRequest
from app.schemas.talent import TalentUpdate, TalentCreate, TalentMergeRequest, TalentSemanticSearchRequest, TalentAskRequest
from app.ai.utils.resume_normalizer import normalize, clean_text
from app.config.talent_config import talent_settings as cfg
from app.config.settings import settings
from app.core.exceptions import AIError, FileError, NotFoundError, PermissionDeniedError, BusinessRuleError

CTX = AIContext(user_id=1, roles=["super_admin"])

@pytest.mark.parametrize("payload", [{"name":""}, {"name":"  "}, {"name":None}, {"work_years":None}, {"work_years":-1}, {"id":3}])
def test_patch_rejects_invalid_values(payload):
    """gxd-拒绝空姓名、非法年限和禁止修改的字段。"""
    with pytest.raises(ValidationError):
        TalentUpdate(**payload)

def test_patch_preserves_missing_and_explicit_null():
    """gxd-未提交字段不进入更新字典，明确null则保留。"""
    assert TalentUpdate(work_years=3).model_dump(exclude_unset=True) == {"work_years":3}
    assert TalentUpdate(school=None).model_dump(exclude_unset=True) == {"school":None}

@pytest.mark.parametrize("payload", [{}, {"talent_id":1,"create_talent":True}, {"talent_id":-1}])
def test_bind_requires_one_target(payload):
    """gxd-拒绝无目标或多个目标的档案绑定。"""
    with pytest.raises(ValidationError):
        ResumeBindRequest(**payload)

@pytest.mark.parametrize("payload", [{"target_id":1,"source_ids":[2]}, {"target_id":1,"source_ids":[1],"confirmed":True}])
def test_merge_requires_confirmation(payload):
    """gxd-重复合并必须确认且源主档不能相同。"""
    with pytest.raises(ValidationError):
        TalentMergeRequest(**payload)

def test_normalization_checks_evidence_and_dates():
    """gxd-虚构学校、技能、无证据经历被移除，日期倒置保留问题提示。"""
    source = "张三 Python 本科 2024-01至2023-01在甲公司工作，工作经验3年"
    parsed = ResumeParsedData(basic_info={"name":"张三", "school":"不存在大学", "education":"本科", "work_years":3}, skills=["python", "Python", "Go"], work_experience=[{"organization":"甲公司", "start_date":"2024.01", "end_date":"2023/01", "evidence":"2024-01至2023-01在甲公司工作"}], projects=[{"title":"假的项目", "evidence":"源文没有"}])
    result = normalize(parsed, source)
    assert result.basic_info.school is None
    assert result.basic_info.work_years == 3
    assert result.skills == ["Python"]
    assert not result.projects
    assert any("早于" in w for w in result.warnings)
    assert normalize(ResumeParsedData(basic_info={"work_years":30}), source).basic_info.work_years is None
    assert clean_text(" a\r\n\r\nb\x00 ") == "a\nb"

@pytest.mark.parametrize("encoding", ["utf8", "gb18030"])
def test_txt_loader(tmp_path, encoding):
    """gxd-真实读取两类编码TXT并保留中文正文。"""
    from app.ai.rag.document_loader import load
    path = tmp_path / "resume.txt"
    path.write_bytes("张三 Python开发工程师".encode(encoding))
    assert "张三" in load(str(path))[0].page_content

def test_docx_loader_reads_table(tmp_path):
    """gxd-真实DOCX段落和表格均被抽取。"""
    from docx import Document
    from app.ai.rag.document_loader import load
    doc = Document(); doc.add_paragraph("张三")
    doc.add_table(rows=1, cols=1).cell(0,0).text = "Python开发"
    path = tmp_path / "resume.docx"; doc.save(path)
    assert "Python开发" in load(str(path))[0].page_content

def test_pdf_loader_preserves_page(tmp_path):
    """gxd-真实读取有文本层PDF，保留页码。"""
    import fitz
    from app.ai.rag.document_loader import load
    path = tmp_path / "resume.pdf"
    doc=fitz.open(); page=doc.new_page(); page.insert_text((50,50),"Python developer")
    doc.save(path); doc.close()
    result = load(str(path))
    assert "Python" in result[0].page_content and result[0].metadata["page"] == 1

def test_scanned_pdf_requires_ocr(tmp_path, monkeypatch):
    """gxd-扫描页OCR未开启时明确失败，不能返回空的成功结果。"""
    import fitz
    from app.ai.rag.document_loader import load
    from app.config.settings import settings
    monkeypatch.setattr(settings, "OCR_ENABLED", False)
    path = tmp_path / "scan.pdf"; doc=fitz.open();doc.new_page();doc.save(path);doc.close()
    with pytest.raises(FileError, match="OCR"):
        load(str(path))

@pytest.mark.parametrize("name,content", [("x.exe",b"hi"),("x.pdf",b"not pdf"),("x.txt",b"")])
async def test_file_validation(name, content):
    """gxd-文件格式、签名及空内容在保存前拦截。"""
    from app.services.file_service import FileService
    with pytest.raises(FileError):
        await FileService(Obj()).save_resume(name,content,None,1)

async def test_resume_agent_rejects_hallucinated_fields(monkeypatch):
    """gxd-完整Agent链路校验JSON并清理无来源字段。"""
    from app.ai.agents.resume_agent import ResumeAgent
    from app.ai.core.llm_manager import llm_manager
    mock=AsyncMock(return_value=Obj(content=json.dumps({"basic_info":{"name":"张三","school":"虚构大学"},"skills":["Python"]}), model="test-model"))
    monkeypatch.setattr(llm_manager,"chat",mock)
    result=await ResumeAgent(context=CTX).run({"text":"张三 熟悉Python"})
    assert result["ok"] and result["result"]["basic_info"]["school"] is None
    assert result["result"]["skills"] == ["Python"]
    assert mock.call_args.args[0][0].role == "system"

async def test_resume_agent_enforces_permission():
    """gxd-Agent被直接调用时也必须具有解析权限。"""
    from app.ai.agents.resume_agent import ResumeAgent
    result = await ResumeAgent(context=AIContext(user_id=2,roles=["employee"])).run({"text":"简历"})
    assert result["ok"] is False
    assert result["error_code"] == PermissionDeniedError.code

async def test_portrait_drops_fabricated_scores():
    """gxd-删除无证据标签和未经校准的潜力、素质打分。"""
    from app.ai.agents.talent_agent import TalentAgent
    agent=TalentAgent(context=CTX)
    await agent.build_prompt({"source":"熟悉Python"})
    result=await agent.parse_output(json.dumps({"summary":"熟悉Python", "dimensions":{"skill":99}, "potential_level":"A", "labels":[{"name":"Python","category":"skill","evidence":"熟悉Python"},{"name":"领导力","category":"quality","evidence":"带队100人"}]}))
    assert result["dimensions"] == {} and result["potential_level"] is None
    assert len(result["labels"]) == 1

@pytest.mark.parametrize("vectors", [[[1]], [[float("nan"),1]], [[0,0]], []])
async def test_embedding_checks_output_shape(monkeypatch, vectors):
    """gxd-拒绝维度、数量、有限值或零向量错误。"""
    from app.ai.rag.embedding_service import EmbeddingService
    from app.config.settings import settings
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 2)
    embeddings = SimpleNamespace(aembed_documents=AsyncMock(return_value=vectors))
    with pytest.raises(AIError):
        await EmbeddingService(embeddings=embeddings).embed_texts(["abc"])

async def test_vector_adapter_validates_existing_collection():
    """gxd-集合结构不兼容时失败，不自动删除或重建用户集合。

    适配合并后的 MilvusAdapter：方法已由 create_collection 改为
    ensure_collection（共享集合由部署初始化，业务侧只校验），因此用注入
    client 的方式验证「不兼容 -> 报错，且绝不调用 drop_collection」。
    """
    from unittest.mock import Mock

    from app.adapters.vector_store.milvus_adapter import MilvusAdapter
    from app.core.exceptions import AIError

    client = Mock()
    client.has_collection.return_value = True
    client.describe_collection.return_value = {
        "fields": [{"name": "id", "is_primary": True, "type": 21},
                   {"name": "vector", "params": {"dim": 3}}]
    }
    adapter = MilvusAdapter(client=client, collection="talent_vectors")
    with pytest.raises(AIError):
        await adapter.ensure_collection(2)
    client.drop_collection.assert_not_called()

async def test_retrieval_filters_stale_and_foreign_vectors(monkeypatch):
    """gxd-回表重建证据，拒绝越权、旧版本与旧内容摘要的向量。"""
    import app.services.talent_retrieval_service as mod
    monkeypatch.setattr(settings,"MILVUS_ENABLED",True)
    monkeypatch.setattr(mod,"embed_query",AsyncMock(return_value=[1,0]))
    service=mod.TalentRetrievalService(Obj(),context=CTX)
    service.repo.filtered=AsyncMock(return_value=[Obj(id=1,version=3,name="甲")])
    chunk={"chunk_id":"t1:v3:0:current","talent_id":1,"version":3,"text":"可信文本","source":"a.txt","page":1,"kind":"resume"}
    service.build_chunks=AsyncMock(return_value=[chunk])
    service.vector.search=AsyncMock(return_value=[
        {"id":"foreign","score":1,"metadata":{"talent_id":999,"version":3}},
        {"id":"old","score":.99,"metadata":{"talent_id":1,"version":2}},
        {"id":"t1:v3:0:old-content","score":.95,"metadata":{"talent_id":1,"version":3}},
        {"id":chunk["chunk_id"],"score":.9,"metadata":{"talent_id":1,"version":3,"text":"篡改文本"}},
    ])
    hits, rows, pending=await service.retrieve("Python",5)
    assert len(hits)==1 and hits[0]["text"]=="可信文本" and pending is None
    assert service.build_chunks.await_count == 1
    expr=service.vector.search.call_args.kwargs["expr"]
    assert '== 1' in expr and '== 3' in expr

@pytest.mark.parametrize("citations", [[{"chunk_id":"foreign","quote":"Python"}], [{"chunk_id":"c1","quote":"不存在的原文"}]])
async def test_rag_rejects_bad_citations(monkeypatch,citations):
    """gxd-拒绝跨资料引用或模型编造引用文本。"""
    import app.services.talent_retrieval_service as mod
    service=mod.TalentRetrievalService(Obj(),context=CTX)
    service.get_talent=AsyncMock()
    service.retrieve=AsyncMock(return_value=([{"chunk_id":"c1","text":"熟悉Python","talent_id":1,"source":"r.txt","page":1}],{},0))
    monkeypatch.setattr(mod.llm_manager,"chat",AsyncMock(return_value=Obj(content=json.dumps({"answer":"会Python","citations":citations}),model="fake")))
    with pytest.raises(AIError):
        await service.ask(TalentAskRequest(question="技能?",talent_ids=[1]))

async def test_rag_no_evidence_does_not_call_llm(monkeypatch):
    """gxd-无可用证据时直接说明不足，不调用模型编造答案。"""
    import app.services.talent_retrieval_service as mod
    service=mod.TalentRetrievalService(Obj(),context=CTX)
    service.get_talent=AsyncMock();service.retrieve=AsyncMock(return_value=([],{},1))
    mock=AsyncMock();monkeypatch.setattr(mod.llm_manager,"chat",mock)
    result=await service.ask(TalentAskRequest(question="能力?",talent_ids=[1]))
    assert result["citations"]==[] and not mock.called

def test_department_scope_uses_own_id_not_parent_prefix():
    """gxd-下级部门过滤需带当前部门编号，防止同级部门资料泄露。"""
    from app.repositories.talent_profile_repository import TalentRepository
    from sqlalchemy.dialects.mysql import dialect
    expression=TalentRepository().scope(AIContext(user_id=2,roles=["manager"],organization_id=13))
    sql=str(expression.compile(dialect=dialect(),compile_kwargs={"literal_binds":True}))
    assert "'13'" in sql and "'/%%'" in sql

async def test_workflow_runs_all_nodes_without_external_services(monkeypatch):
    """gxd-用明确替身验证真实LangGraph节点顺序、保存和新建分支。"""
    import app.ai.workflows.resume_talent_workflow as mod
    events=[]
    row=Obj(id=1,talent_id=None,status="uploaded",parsed_data={})
    class Resumes:
        def __init__(self,*a,**kw):
            """gxd-构造测试简历服务。"""
        async def prepare_parse(self,*a,**kw):
            """gxd-返回测试简历。"""
            return None
        async def source_text(self,*a):
            """gxd-模拟成功提取正文。"""
            events.append("extract");return "张三 Python"
        async def finalize_parse(self, rid, data, meta, talent_id=None):
            """gxd-记录节点保存顺序。"""
            events.append("save");return {"talent_id":8,"status":"succeeded"}
        async def bind(self,*a,**kw):
            """gxd-模拟新建绑定。"""
            events.append("bind");return Obj(id=8)
    class Talents:
        def __init__(self,*a,**kw):
            """gxd-构造测试人才服务。"""
        async def duplicate_candidates(self,*a):
            """gxd-模拟无重复候选。"""
            events.append("dedupe");return []
    class Agent:
        def __init__(self,*a,**kw):
            """gxd-构造测试模型。"""
        async def run(self,*a):
            """gxd-提供固定结构化结果测试编排。"""
            events.append("parse");return {"ok":True,"result":{"basic_info":{"name":"张三"}}}
    monkeypatch.setattr(mod,"ResumeService",Resumes);monkeypatch.setattr(mod,"ResumeAgent",Agent)
    result=await mod.run_resume_workflow(Obj(flush=AsyncMock()),1,CTX)
    assert events==["extract","parse","save"]
    assert result["talent_id"]==8 and result["status"]=="succeeded"

async def test_direct_parse_failure_records_existing_resume_status(monkeypatch):
    """gxd-解析故障回滚解析保存点后，仍用原有简历状态记录失败。"""
    from contextlib import nullcontext
    from app.services.talent_resume_service import ResumeService
    import app.ai.workflows.resume_talent_workflow as workflow
    db=Obj(begin_nested=lambda: nullcontext())
    service=ResumeService(db,context=CTX)
    service.get_resume=AsyncMock(return_value=Obj(id=1,talent_id=None))
    service.repo.update_status=AsyncMock()
    service.result=AsyncMock(return_value={"status":"failed","parsed":None})
    monkeypatch.setattr(workflow,"run_resume_workflow",AsyncMock(side_effect=AIError("模型不可用")))
    result=await service.parse_resume(resume_id=1)
    assert result["status"]=="failed" and result["processing_status"]=="failed"
    service.repo.update_status.assert_awaited_once_with(db,1,"failed","模型不可用")

async def test_direct_batch_keeps_uploaded_parse_failure(monkeypatch):
    """gxd-上传成功但解析失败仍返回简历编号，不阻断后一份文件。"""
    from contextlib import nullcontext
    from app.services.talent_resume_service import ResumeService
    service=ResumeService(Obj(begin_nested=lambda: nullcontext()),context=CTX)
    service.upload=AsyncMock(side_effect=[{"resume_id":1},{"resume_id":2}])
    service.parse_resume=AsyncMock(side_effect=[{"status":"failed"},{"status":"success"}])
    files=[Obj(filename="a.txt",content_type="text/plain",read=AsyncMock(return_value=b"a"),close=AsyncMock()),
           Obj(filename="b.txt",content_type="text/plain",read=AsyncMock(return_value=b"b"),close=AsyncMock())]
    result=await service.upload_batch(files)
    assert [x["resume_id"] for x in result["accepted"]]==[1,2]
    assert [x["status"] for x in result["accepted"]]==["failed","success"]
    assert not result["rejected"] and all(f.close.await_count==1 for f in files)

async def test_refresh_failure_is_reported_without_new_task_tables(monkeypatch):
    """gxd-画像故障保留主档，返回失败提示并停止后续向量写入。"""
    from contextlib import nullcontext
    from app.services.talent_profile_service import TalentService
    from app.services.talent_retrieval_service import TalentRetrievalService
    obj=Obj(id=1)
    db=Obj(begin_nested=lambda: nullcontext(),refresh=AsyncMock())
    service=TalentService(db,context=CTX)
    service.get_talent=AsyncMock(return_value=obj)
    service.generate_portrait=AsyncMock(side_effect=AIError("模型不可用"))
    sync=AsyncMock()
    monkeypatch.setattr(TalentRetrievalService,"sync_index",sync)
    result=await service.refresh_derived(1)
    assert result["portrait"]["status"]=="failed" and result["index"]["status"]=="not_run"
    db.refresh.assert_awaited_once_with(obj)
    sync.assert_not_awaited()