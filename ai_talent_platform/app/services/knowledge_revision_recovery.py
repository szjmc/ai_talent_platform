# 先核对 MySQL 的发布结果及当前切片，再按日志里的精确 ID 清理旧向量或未提交向量。
"""版本对象与发布向量补偿，严格按资源/切片ID清理。
标签：[RAG组] [P0]
"""
import json
from app.repositories.knowledge_revision_repository import KnowledgeRevisionRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.file_service import FileService
from app.adapters.storage.base_storage import get_storage
from app.adapters.vector_store.milvus_adapter import MilvusAdapter


async def recover_revision_job(db, value):
    repo = KnowledgeRevisionRepository()
    if value['operation'] == 'revision_assets':
        for resource in value.get('objects', []):
            # SQL事务已提交则文件记录存在；提交结果不确定时先核实，不盲删文件。
            exists = await repo.referenced_object(db, resource['key'], resource['backend'])
            if exists is None:
                await get_storage(resource['backend']).delete(resource['key'])
        return
    doc_id = value['doc_id']
    doc = await repo.document(db, doc_id, True)
    revision = await repo.revision(db, value['revision_id'])
    committed = False
    if revision is not None and revision.index_manifest:
        file = await repo.file(db, revision.index_manifest)
        if file is not None:
            manifest = json.loads(await FileService(db).read_object(file))
            committed = manifest.get('chunk_ids') == value.get('new_chunk_ids')
    current_ids = {c.id for c in await repo.chunks(db, doc_id)} if doc is not None else set()
    candidates = value.get('old_chunk_ids', []) if committed else value.get('new_chunk_ids', [])
    await MilvusAdapter().delete_chunks(doc_id, [cid for cid in candidates if cid not in current_ids])
    if doc is not None and doc.status == 'indexed':
        from app.ai.rag.bm25_index import search_index
        await search_index(db, KnowledgeRepository(), [doc], '', 1, strict=True)
