# 题库的数据库查询集中在此处，调用方负责业务判断与事务边界。
"""题库数据范围、筛选及导入访问；不提交事务。
标签：[RAG组] [P1]。
"""
from sqlalchemy import and_, func, or_, select, update

from app.models.question_bank import QuestionBankQuestion
from app.repositories.base import BaseRepository

Q = QuestionBankQuestion


class QuestionBankRepository(BaseRepository):
    model = Q

    def selection_query(self, request, user, question_type):
        stmt = self._base_query().where(
            Q.status == 'published', Q.technology == request.technology,
            Q.difficulty == request.difficulty, Q.question_type == question_type,
        )
        if not user.is_superuser and 'super_admin' not in user.role_codes:
            scopes = [Q.permission_scope == 'public', Q.created_by == user.id]
            if user.organization_id is not None:
                scopes.append(and_(Q.permission_scope == 'dept', Q.organization_id == user.organization_id))
            stmt = stmt.where(or_(*scopes))
        if request.bank_ids:
            stmt = stmt.where(Q.bank_id.in_(request.bank_ids))
        if request.exclude_question_ids:
            stmt = stmt.where(Q.id.not_in(request.exclude_question_ids))
        return stmt

    async def random_questions(self, db, request, user, question_type, count):
        stmt = self.selection_query(request, user, question_type).order_by(func.rand()).limit(count)
        return list((await db.scalars(stmt)).all())

    async def candidate_batches(self, db, request, user, question_type, signature, batch_size=500):
        """按主键分页，只取已同步且有权读取的题号，避免加载全部题干。"""
        after_id = 0
        while True:
            stmt = self.selection_query(request, user, question_type).with_only_columns(Q.id, Q.content_version).where(
                Q.id > after_id, Q.index_status == 'indexed', Q.indexed_version == Q.content_version,
                Q.index_signature == signature,
            ).order_by(Q.id).limit(batch_size)
            rows = list((await db.execute(stmt)).all())
            if not rows:
                return
            yield rows
            after_id = rows[-1].id

    async def selected_questions(self, db, request, user, question_type, ids):
        if not ids:
            return []
        stmt = self.selection_query(request, user, question_type).where(Q.id.in_(ids))
        return list((await db.scalars(stmt)).all())

    async def upsert_import(self, db, item, owner_id):
        row = await db.scalar(select(Q).where(
            Q.bank_id == item.bank_id, Q.external_id == item.external_id,
        ).with_for_update())
        values = item.model_dump(mode='json')
        if row is None:
            row = Q(**values, created_by=owner_id, updated_by=owner_id)
            db.add(row)
        elif any(getattr(row, key) != value for key, value in values.items()) or row.is_deleted:
            for key, value in values.items():
                setattr(row, key, value)
            row.restore()
            row.content_version += 1
            row.version += 1
            row.index_status, row.index_error = 'pending', None
            row.updated_by = owner_id
        await db.flush()
        return row

    async def index_batch(self, db, after_id, batch_size, signature, force=False, question_ids=None):
        stmt = select(Q).where(Q.id > after_id)
        if question_ids is not None:
            stmt = stmt.where(Q.id.in_(question_ids))
        if not force:
            stmt = stmt.where(or_(
                Q.index_status != 'indexed', Q.indexed_version != Q.content_version,
                Q.index_signature.is_(None), Q.index_signature != signature,
            ))
        return list((await db.scalars(stmt.order_by(Q.id).limit(batch_size))).all())

    async def mark_indexed(self, db, question_id, content_version, signature):
        # 索引期间题目若已修改，保留pending供下一次脚本同步。
        await db.execute(update(Q).where(Q.id == question_id, Q.content_version == content_version).values(
            index_status='indexed', indexed_version=content_version, index_signature=signature, index_error=None,
        ))

    async def mark_index_error(self, db, question_id, content_version, error):
        await db.execute(update(Q).where(Q.id == question_id, Q.content_version == content_version).values(
            index_status='error', index_error=error[:255],
        ))


    async def import_target(self, db, bank_id, external_id):
        return await db.scalar(select(Q).where(
            Q.bank_id == bank_id, Q.external_id == external_id,
        ).with_for_update())

    async def import_index_counts(self, db, ids, signature):
        rows = (await db.execute(select(
            Q.index_status, Q.indexed_version, Q.content_version, Q.index_signature,
        ).where(Q.id.in_(ids)))).all()
        indexed, failed = 0, 0
        for row in rows:
            if (row.index_status == 'indexed' and row.indexed_version == row.content_version
                    and row.index_signature == signature):
                indexed += 1
            elif row.index_status == 'error':
                failed += 1
        return {'indexed_count': indexed, 'failed_count': failed, 'pending_count': len(ids) - indexed - failed}
