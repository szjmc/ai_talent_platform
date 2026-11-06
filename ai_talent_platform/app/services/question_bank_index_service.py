# 题库索引服务连接题目向量化与向量存储。排查索引时要分别检查题库记录和向量写入。
"""题库导入与可重试索引同步；pending/error保存在题目表，不改变公共后台任务。
标签：[RAG组] [P1]。
"""
import hashlib
import logging

from sqlalchemy import func, select

from app.adapters.vector_store.question_bank_adapter import QuestionBankVectorAdapter
from app.ai.rag.question_embeddings import get_question_embeddings, question_embedding_signature
from app.core.exceptions import DataConflictError
from app.core.question_bank_constants import DIFFICULTY_LABELS, TYPE_LABELS
from app.repositories.question_bank_repository import QuestionBankRepository

logger = logging.getLogger(__name__)


def question_index_text(question):
    # 向量检索使用题干、选项和分类信息，标准答案留在题库记录中。
    lines = [
        '技术：' + question.technology,
        '等级：' + DIFFICULTY_LABELS[question.difficulty],
        '题型：' + TYPE_LABELS[question.question_type],
        '知识点：' + '、'.join(question.knowledge_points or []),
        '题干：' + question.stem,
    ]
    if question.options:
        lines.append('选项：')
        lines.extend(option['key'] + '. ' + option['text'] for option in question.options)
    return '\n'.join(lines)


class QuestionBankIndexService:
    def __init__(self, repo=None, embeddings=None, store=None):
        self.repo = repo or QuestionBankRepository()
        self.embeddings = embeddings
        self.store = store or QuestionBankVectorAdapter()

    async def import_questions(self, db, items, owner_id):
        ids = []
        for item in items:
            row = await self.repo.upsert_import(db, item, owner_id)
            ids.append(row.id)
        return ids

    async def sync(self, session_factory, engine, batch_size=32, force=False, question_ids=None):
        # 与平台入库任务一致使用MySQL命名锁，防止两个脚本将新旧版本乱序写入同一主键。
        lock_key = 'question-index:' + hashlib.sha256(self.store.collection.encode()).hexdigest()[:48]
        async with engine.connect() as connection:
            acquired = await connection.scalar(select(func.GET_LOCK(lock_key, 0)))
            await connection.commit()
            if acquired != 1:
                raise DataConflictError('题库索引同步正在执行，请稍后重试')
            try:
                return await self._sync_batches(session_factory, batch_size, force, question_ids)
            finally:
                await connection.execute(select(func.RELEASE_LOCK(lock_key)))
                await connection.commit()

    async def _sync_batches(self, session_factory, batch_size, force, question_ids=None):
        # 签名区分向量模型配置，游标按题目主键前进，避免失败记录阻塞当前批次。
        signature = question_embedding_signature()
        after_id = 0
        indexed, failed = 0, 0
        while True:
            async with session_factory() as db:
                rows = await self.repo.index_batch(db, after_id, batch_size, signature, force, question_ids)
            if not rows:
                break
            after_id = rows[-1].id
            # 每题独立提交；读取会话已关闭，模型调用不占用数据库事务。
            for row in rows:
                question_id, version = row.id, row.content_version
                async with session_factory() as db:
                    try:
                        if row.is_deleted or row.status != 'published':
                            await self.store.delete_questions([question_id])
                        else:
                            if self.embeddings is None:
                                self.embeddings = get_question_embeddings()
                            vectors = await self.embeddings.aembed_documents([question_index_text(row)])
                            await self.store.upsert([row], vectors, signature)
                        # 携带读到的内容版本回写，避免将并发修改后的题目标为已同步。
                        await self.repo.mark_indexed(db, question_id, version, signature)
                        await db.commit()
                        indexed += 1
                    except Exception as exc:
                        await db.rollback()
                        logger.exception('题库索引同步失败 question_id=%s', question_id)
                        await self.repo.mark_index_error(db, question_id, version, type(exc).__name__)
                        await db.commit()
                        failed += 1
        return {'indexed': indexed, 'failed': failed}
