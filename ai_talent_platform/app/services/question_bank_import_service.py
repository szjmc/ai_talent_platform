# 导入服务负责将输入转换为题库记录，并协调持久化和索引步骤。
"""题库文件上传编排：全量校验、原子导入、提交后同步本批索引。
标签：[RAG组] [P1]。
"""
import logging
from pathlib import Path

from pydantic import ValidationError

from app.ai.rag.question_embeddings import question_embedding_signature
from app.config.settings import settings
from app.core.exceptions import ParamError, PermissionDeniedError
from app.core.permissions import PermissionEnum, check_permission
from app.infrastructure.database.session import get_engine, get_session_factory
from app.repositories.question_bank_repository import QuestionBankRepository
from app.schemas.question_bank import QuestionImportBatch, QuestionImportResponse
from app.services.question_bank_index_service import QuestionBankIndexService

logger = logging.getLogger(__name__)


class QuestionBankImportService:
    def __init__(self, repo=None, index_service=None, session_factory=None, engine=None):
        self.repo = repo or QuestionBankRepository()
        self.index_service = index_service or QuestionBankIndexService(repo=self.repo)
        self.session_factory = session_factory
        self.engine = engine

    async def upload(self, data, filename, user, sync_index=True):
        if not user.is_superuser:
            check_permission(user.role_codes, PermissionEnum.KNOWLEDGE_MANAGE)
        if Path(filename).suffix.lower() != '.json':
            raise ParamError('题库导入仅支持固定格式的JSON文件')
        if not data or len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise ParamError('题库文件为空或超过上传大小限制')
        try:
            text = data.decode('utf-8-sig')
            items = QuestionImportBatch.model_validate_json(text).root
        except UnicodeDecodeError:
            raise ParamError('题库JSON文件须使用UTF-8编码') from None
        except ValidationError as exc:
            first = exc.errors(include_input=False, include_url=False)[0]
            location = '.'.join(str(part) for part in first['loc']) or '文件'
            raise ParamError('题库格式错误（' + location + '）：' + first['msg']) from None

        factory = self.session_factory or get_session_factory()
        async with factory() as db:
            try:
                # 管理权限不自动授予他人私有题目的编辑权限。
                is_admin = user.is_superuser or bool({'admin', 'super_admin'} & set(user.role_codes))
                for item in items:
                    if not is_admin and item.permission_scope == 'dept' and item.organization_id != user.organization_id:
                        raise PermissionDeniedError('不能将题目导入其他部门')
                    target = await self.repo.import_target(db, item.bank_id, item.external_id)
                    if target is not None and not is_admin and target.created_by != user.id:
                        raise PermissionDeniedError('不能覆盖他人创建的题目')
                ids = await self.index_service.import_questions(db, items, user.id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        # 数据库已提交，再做外部向量写入；失败保留pending/error可重试，不声称已回滚题目。
        sync_error = None
        if sync_index:
            try:
                await self.index_service.sync(
                    factory, self.engine or get_engine(), batch_size=settings.EMBEDDING_BATCH_SIZE,
                    question_ids=ids,
                )
            except Exception:
                logger.exception('题库导入已提交，本批索引同步未完成')
                sync_error = '题目已导入，本批索引同步未完成，可重复上传同一文件重试或执行同步脚本'
        async with factory() as db:
            counts = await self.repo.import_index_counts(db, ids, question_embedding_signature())
        return QuestionImportResponse(
            imported_count=len(ids), question_ids=ids, sync_requested=sync_index, sync_error=sync_error, **counts,
        )
