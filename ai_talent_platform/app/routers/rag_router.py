# 兼容接口接收已有文件入库、问题向量、删除和切片查询请求，业务权限与文档访问范围分别校验。
"""B角色兼容接口，复用Embedding与权限过滤，不直连Milvus。

职责：接收入库指令、提供问题向量、受理删除，以及分页返回切片。
本文件只负责参数接收、依赖注入和统一响应；业务校验与数据处理交给Service。
路由由router.py汇总，再由application.py统一加上/api/v1前缀。
标签：[RAG组] [P1]。"""
from fastapi import APIRouter,Depends
from app.core.response import success
from app.core.dependencies import require_permission
from app.core.permissions import PermissionEnum
from app.schemas.rag import IngestRequest,EmbedQueryRequest
from app.ai.rag.embedding_service import EmbeddingService
from app.routers.knowledge_router import get_service
from app.services.ingestion_service import IngestionService
from fastapi import Query
from pydantic import BaseModel,Field
from app.core.rag_constants import CategoryLiteral
# 最终路径为/api/v1/ingestion/...；tags用于Swagger中的接口分组。
router=APIRouter(prefix='/ingestion',tags=['知识库'])

# require_permission返回已认证用户；Service还会检查文件是否属于该用户。
# scope='function'使提交/回滚在响应发送前完成，避免前端先收到成功再发生提交失败。
@router.post('/trigger', summary='触发已有文件解析与入库', description='对文件中心已有记录执行解析、清洗分块、向量化和索引写入。需要知识库管理权限及文件访问权限；file_id必须是MySQL文件ID，桶名和对象键必须匹配文件记录。当前同步处理，返回后检查索引状态。')
async def trigger(request:IngestRequest,user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),service=Depends(get_service,scope='function')):
    """触发已有文件入库：校验File元数据 → 读原件 → 复用知识库入库流程。

    request.file_id必须是MySQL文件ID，bucket_name/object_name须与文件记录匹配。
    当前会等待入库处理完成；调用方应检查返回的status，而非只看HTTP状态码。
    """
    # 使用同一个数据库会话，兼容入口不另开事务或创建第二套入库实现。
    bridge=IngestionService(service.db)
    # 复用同一事务的资源日志，提交/补偿仍由get_service管理。
    # 必须共享列表对象，bridge追加的资源与任务才能被外层事务依赖看到。
    bridge.created_assets=service.created_assets
    bridge.ingest_jobs=service.ingest_jobs
    return success(data=await bridge.process_document(request,user))
@router.post('/embed_query', summary='生成查询文本向量', description='将查询文本转换为与文档入库模型一致的向量，返回原文本、向量及实际维度。需要知识库读取权限；本接口不检索文档，也不向Milvus写入数据。')
async def embed(request:EmbedQueryRequest,user=Depends(require_permission(PermissionEnum.KNOWLEDGE_READ))):
    """仅生成问题向量，不执行检索，也不写入Milvus。"""
    # 与文档入库复用同一EmbeddingService，确保模型、维度和批量校验保持一致。
    vector=await EmbeddingService().embed_query(request.text)
    # dim为实际返回的向量维度；统一响应会额外带上code/message/trace_id。
    return success(data={'embedding':vector,'text':request.text,'dim':len(vector)})


class DeleteRequest(BaseModel):
    """删除请求：文件ID采用数字字符串，分类只允许平台约定的英文标识。"""
    # 这里是files表主键，不是knowledge_docs主键，也不是任意UUID。
    file_id:str=Field(pattern=r'^[1-9][0-9]*$')
    category:CategoryLiteral

@router.post('/delete',status_code=202, summary='删除知识文档索引', description='按文件ID和分类受理删除，先提交文档逻辑删除，再由恢复任务清理向量。需要知识库管理权限及文档归属权限；HTTP202和deletion_pending表示已受理，不代表立即清理完成。此接口不物理删除原始文件。')
async def delete(request:DeleteRequest,user=Depends(require_permission(PermissionEnum.KNOWLEDGE_MANAGE)),service=Depends(get_service,scope='function')):
    """受理逻辑删除；202表示已受理，不代表远程向量已立即清理完成。"""
    bridge=IngestionService(service.db)
    # Service先记录持久化任务，再逻辑删除；恢复worker确认事务结果后清理向量。
    # 共享任务列表让外层get_service正确释放锁，并保留待补偿任务。
    bridge.ingest_jobs=service.ingest_jobs
    return success(data=await bridge.delete_document(request.file_id,request.category,user),code=202,message='删除已受理，向量由补偿任务清理')

@router.get('/chunks/fetch', summary='分页查看文档切片', description='按分类和可选文件ID查看当前用户可访问且已索引的切片，返回切片ID、原文、文件ID、来源、页码和匹配范围total。需要知识库读取权限；支持offset/limit分页，每页最多1000条，无可靠页码时page_number为0。')
async def fetch(category:CategoryLiteral,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=1000),file_id:int|None=Query(None,ge=1),user=Depends(require_permission(PermissionEnum.KNOWLEDGE_READ)),service=Depends(get_service)):
    """分页查看切片原文：category必填；file_id可选；每页最多1000条。

    返回的total为当前授权且匹配筛选条件的切片总数，不是本页长度。
    """
    # 1. 先取得当前用户可访问且已成功索引的文档，避免先召回再做权限过滤。
    docs=await service.repo.accessible_docs(service.db,user,category)
    # 2. 在授权范围内缩小到指定文件；不传file_id则查看该分类的可见文档。
    if file_id is not None:docs=[d for d in docs if d.file_id==file_id]
    # doc_id到文档的映射用于补齐文件ID、标题等来源信息。
    by_id={d.id:d for d in docs}
    # 3. 查询MySQL中的可信切片原文；空文档范围会返回空列表，不会变成全表查询。
    rows,total=await service.repo.fetch_visible_chunks(service.db,list(by_id),offset,limit)
    # 4. 仅复用主键格式转换，不在Router创建或查询Milvus连接。
    # MySQL切片ID为整数，Milvus主键为ragdoc-{doc_id}-chunk-{chunk_id}字符串。
    from app.adapters.vector_store.milvus_adapter import MilvusAdapter
    # page_number=0表示没有可靠页码（如Word或Excel），不应当作真实第0页展示。
    return success(data={'items':[{'chunk_id':MilvusAdapter._prefix(c.doc_id)+str(c.id),'file_id':str(by_id[c.doc_id].file_id),'content':c.content,'source':by_id[c.doc_id].title,'page_number':c.page_no or 0} for c in rows],'total':total})
