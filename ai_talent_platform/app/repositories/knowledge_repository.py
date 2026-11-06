# 检索范围由状态、分类和权限共同决定。语义与关键词两路共享同一文档白名单。
"""知识文档和切片 ORM 访问，事务由调用方管理。
标签：[RAG组] [P0]。"""
from sqlalchemy import select, or_, and_, func
from app.models.knowledge import KnowledgeDoc, KnowledgeChunk
from app.repositories.base import BaseRepository

class KnowledgeRepository(BaseRepository):
    model = KnowledgeDoc
    filterable_fields = {"category", "status"}
    searchable_fields = {"title"}
    sortable_fields = {"id", "created_at"}

    async def accessible_docs(self, db, user, category=None):
        stmt = self._base_query().where(KnowledgeDoc.status == "indexed")
        if not user.is_superuser:
            rules = [KnowledgeDoc.permission_scope == "public"]
            if user.organization_id is not None:
                rules.append(and_(KnowledgeDoc.permission_scope == "dept", KnowledgeDoc.organization_id == user.organization_id))
            for role in user.role_codes:
                rules.append(and_(KnowledgeDoc.permission_scope == "role", func.JSON_CONTAINS(KnowledgeDoc.allowed_roles, func.JSON_QUOTE(role)) == 1))
            stmt = stmt.where(or_(*rules))
        if category:
            stmt = stmt.where(KnowledgeDoc.category == category)
        return list((await db.scalars(stmt)).all())

    async def save_chunks(self, db, chunks):
        db.add_all(chunks)
        await db.flush()
        return chunks

    async def active_chunk_keys(self, db, doc_ids):
        """以当前事务快照中的切片主键隔离待发布/历史向量，不改动旧切片表。"""
        if not doc_ids:
            return []
        return list((await db.execute(select(KnowledgeChunk.doc_id, KnowledgeChunk.id).where(
            KnowledgeChunk.doc_id.in_(doc_ids), KnowledgeChunk.is_deleted.is_(False)))).all())

    async def published_revisions(self, db, doc_ids):
        from app.models.knowledge_revision import KnowledgeRevision
        if not doc_ids:
            return {}
        rows = (await db.scalars(select(KnowledgeRevision).where(
            KnowledgeRevision.doc_id.in_(doc_ids), KnowledgeRevision.status == 'published',
            KnowledgeRevision.is_deleted.is_(False)))).all()
        return {r.doc_id: r for r in rows}

    async def search_chunks_by_ids(self, db, ids, doc_ids):
        if not ids or not doc_ids:
            return []
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids), KnowledgeChunk.doc_id.in_(doc_ids), KnowledgeChunk.is_deleted.is_(False))
        return list((await db.scalars(stmt)).all())

    async def keyword_search(self, db, query, doc_ids, limit=50):
        if not doc_ids:
            return []
        # contains(autoescape=True) 将百分号等作为普通文本，防止无意扩大匹配范围。
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(doc_ids), KnowledgeChunk.is_deleted.is_(False), KnowledgeChunk.content.contains(query, autoescape=True)).order_by(KnowledgeChunk.id).limit(limit)
        return list((await db.scalars(stmt)).all())

    async def find_upload(self, db, file_hash, category, owner_id):
        """去重仅限同一上传人、分类和文件内容，防止跨用户泄露。"""
        from app.models.file import File
        stmt = select(KnowledgeDoc).join(File, KnowledgeDoc.file_id == File.id).where(
            File.file_hash == file_hash, File.is_deleted.is_(False),
            KnowledgeDoc.is_deleted.is_(False), KnowledgeDoc.category == category,
            KnowledgeDoc.created_by == owner_id).order_by(KnowledgeDoc.id.desc()).limit(1)
        return await db.scalar(stmt)

    async def clear_chunks(self, db, doc_id):
        from sqlalchemy import delete
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))

    async def lock_document(self, db, doc_id):
        stmt = self._base_query().where(KnowledgeDoc.id == doc_id).with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def lexical_corpus(self, db, doc_ids):
        """只读取已授权文档的切片；按主键流式分批，避免单次回表峰值。"""
        stmt=select(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(doc_ids),KnowledgeChunk.is_deleted.is_(False)).order_by(KnowledgeChunk.id)
        result=await db.stream_scalars(stmt.execution_options(yield_per=500))
        return [row async for row in result]

    async def by_file(self,db,file_id):
        return await db.scalar(self._base_query().where(KnowledgeDoc.file_id==file_id).with_for_update())
    async def fetch_visible_chunks(self,db,doc_ids,offset,limit):
        if not doc_ids:return [],0
        from sqlalchemy import func
        condition=(KnowledgeChunk.doc_id.in_(doc_ids),KnowledgeChunk.is_deleted.is_(False))
        total=await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(*condition))
        rows=list((await db.scalars(select(KnowledgeChunk).where(*condition).order_by(KnowledgeChunk.id).offset(offset).limit(limit))).all())
        return rows,total
