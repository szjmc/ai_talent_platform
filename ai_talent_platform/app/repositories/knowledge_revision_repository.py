# 仓库提供文档及版本查询、行锁和版本号分配，提交由调用方负责。
"""新版本表与现有知识表的访问，不提交事务、不改变旧表结构。
标签：[RAG组] [P0]
"""
from sqlalchemy import select, func
from app.models.knowledge_revision import KnowledgeRevision
from app.models.knowledge import KnowledgeDoc, KnowledgeChunk
from app.models.file import File
from app.repositories.base import BaseRepository


class KnowledgeRevisionRepository(BaseRepository):
    model = KnowledgeRevision

    async def document(self, db, doc_id, lock=False):
        stmt = select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id, KnowledgeDoc.is_deleted.is_(False))
        return await db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)

    async def revision(self, db, revision_id, lock=False):
        stmt = self._base_query().where(KnowledgeRevision.id == revision_id)
        return await db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)

    async def list_revisions(self, db, doc_id, offset=0, limit=50):
        stmt = self._base_query().where(KnowledgeRevision.doc_id == doc_id)
        total = await db.scalar(select(func.count()).select_from(KnowledgeRevision).where(
            KnowledgeRevision.doc_id == doc_id, KnowledgeRevision.is_deleted.is_(False)))
        rows = list((await db.scalars(stmt.order_by(KnowledgeRevision.revision_no.desc()).offset(offset).limit(limit))).all())
        return rows, total

    async def next_number(self, db, doc_id):
        # 调用前必须锁定KnowledgeDoc行，以串行分配版本号。
        number = await db.scalar(select(KnowledgeRevision.revision_no).where(
            KnowledgeRevision.doc_id == doc_id).order_by(KnowledgeRevision.revision_no.desc()).limit(1).with_for_update())
        return (number or 0) + 1

    async def published(self, db, doc_id):
        return await db.scalar(self._base_query().where(
            KnowledgeRevision.doc_id == doc_id, KnowledgeRevision.status == 'published').with_for_update().execution_options(populate_existing=True))

    async def chunks(self, db, doc_id):
        return list((await db.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.doc_id == doc_id, KnowledgeChunk.is_deleted.is_(False)
        ).order_by(KnowledgeChunk.chunk_index))).all())

    async def file(self, db, file_id):
        if file_id is None:
            return None
        return await db.scalar(select(File).where(File.id == file_id, File.is_deleted.is_(False)))

    async def referenced_object(self, db, key, backend):
        return await db.scalar(select(File.id).where(File.file_path == key, File.storage_type == backend).limit(1))

    async def add(self, db, row):
        db.add(row)
        await db.flush()
        return row

