# 题库业务查询由服务层协调，题库记录和索引结果通过稳定标识关联。
"""知识库选题编排：标签过滤、向量召回、可信回表、配额与权限。
标签：[RAG组] [P1]。
"""
from app.adapters.vector_store.question_bank_adapter import QuestionBankVectorAdapter
from app.ai.rag.question_embeddings import get_question_embeddings, question_embedding_signature
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import PermissionEnum, check_permission, has_permission
from app.repositories.question_bank_repository import QuestionBankRepository
from app.schemas.question_bank import QuestionSearchResponse, SelectedQuestion


class QuestionBankService:
    def __init__(self, db, repo=None, embeddings=None, store=None):
        self.db = db
        self.repo = repo or QuestionBankRepository()
        self.embeddings = embeddings
        self.store = store or QuestionBankVectorAdapter()

    async def search(self, request, user):
        if not user.is_superuser:
            check_permission(user.role_codes, PermissionEnum.KNOWLEDGE_READ)
        if request.include_answers and not (user.is_superuser or has_permission(user.role_codes, PermissionEnum.ASSESSMENT_MANAGE)):
            raise PermissionDeniedError('返回标准答案需要assessment:manage权限')
        vector = None
        signature = None
        if request.query:
            signature = question_embedding_signature()
        items, shortage = [], {}
        for question_type, count in request.counts.items():
            if count == 0:
                continue
            if request.query:
                # 没有已授权候选题时不调用模型或Milvus。
                candidates = []
                async for batch in self.repo.candidate_batches(
                    self.db, request, user, question_type, signature,
                ):
                    if vector is None:
                        embeddings = self.embeddings or get_question_embeddings()
                        vector = await embeddings.aembed_query(request.query)
                    hits = await self.store.search(
                        vector, request, question_type, batch, signature, limit=count,
                    )
                    allowed = dict(batch)
                    candidates.extend(hit for hit in hits
                                      if allowed.get(hit['question_id']) == hit['content_version'])
                    candidates = sorted(candidates, key=lambda h: (-h['score'], h['question_id']))[:count]
                scores = {hit['question_id']: hit for hit in candidates}
                rows = await self.repo.selected_questions(
                    self.db, request, user, question_type, list(scores),
                )
                rows = [row for row in rows if row.content_version == scores[row.id]['content_version']
                        and row.index_status == 'indexed' and row.index_signature == signature
                        and row.indexed_version == row.content_version]
                rows.sort(key=lambda row: (-scores[row.id]['score'], row.id))
            else:
                rows = await self.repo.random_questions(self.db, request, user, question_type, count)
                scores = {}
            seen = set()
            selected = []
            for row in rows:
                if row.id not in seen and len(selected) < count:
                    seen.add(row.id)
                    selected.append(SelectedQuestion(
                        question_id=row.id, bank_id=row.bank_id, external_id=row.external_id,
                        content_version=row.content_version, technology=row.technology,
                        difficulty=row.difficulty, question_type=row.question_type,
                        knowledge_points=row.knowledge_points, stem=row.stem, options=row.options,
                        score=scores[row.id]['score'] if row.id in scores else None,
                        answer=row.answer if request.include_answers else None,
                        analysis=row.analysis if request.include_answers else None,
                    ))
            items.extend(selected)
            if len(selected) < count:
                shortage[question_type] = count - len(selected)
        return QuestionSearchResponse(
            mode='semantic' if request.query else 'random', requested_count=sum(request.counts.values()),
            returned_count=len(items), shortage=shortage, items=items,
        )
