# 先用 MySQL 确定可见文档及当前切片，再执行语义和关键词召回。RRF 融合排名，最终从 SQL 回取原文。
"""权限先行的语义/关键词/混合检索与可插拔重排。
标签：[RAG组] [P0]。"""
import json
import logging
from app.ai.rag.bm25_index import search_index
from app.adapters.llm.rerank_adapter import rerank
from app.ai.rag.embedding_service import EmbeddingService
from app.adapters.vector_store.milvus_adapter import MilvusAdapter
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.knowledge_revision_repository import KnowledgeRevisionRepository
from app.services.file_service import FileService

logger = logging.getLogger(__name__)

class Retriever:
    def __init__(self, db, embeddings=None, store=None, reranker=None):
        self.db = db
        self.repo = KnowledgeRepository()
        self.embeddings = embeddings or EmbeddingService()
        self.store = store or MilvusAdapter()
        self.reranker = reranker

    async def search(self, query, user, category=None, top_k=5, mode="hybrid", score_threshold=-1.0, use_rerank=False, *, result_unit="auto", doc_id=None):
        docs = await self.repo.accessible_docs(self.db, user, category)
        if doc_id is not None:
            docs = [doc for doc in docs if doc.id == doc_id]
        group_documents = result_unit == "document" or (
            result_unit == "auto" and category == "resume" and doc_id is None
        )
        doc_map = {d.id: d for d in docs}
        ids = list(doc_map)
        if not ids:
            return []
        # 简历按文档返回，扩大候选池以降低同一份简历的切片挤占召回的概率。
        candidate_limit = 100 if group_documents else min(top_k * 4, 100)
        # 两条召回支路使用完全相同的可见文档白名单。
        semantic, keyword = [], []
        if mode in {"semantic", "hybrid"}:
            vector = await self.embeddings.embed_query(query)
            # 先按MySQL权限取得文档，再只查询这些文档涉及的合法分类/集合。
            from app.core.rag_constants import ALL_CATEGORIES
            categories = {d.category for d in docs if d.category in ALL_CATEGORIES}
            active_chunks = await self.repo.active_chunk_keys(self.db, ids)
            semantic = [h for h in await self.store.search(vector, ids, candidate_limit, categories=categories, active_chunks=active_chunks) if h["score"] >= score_threshold]
        if mode in {"keyword", "hybrid"}:
            lexical_hits = await search_index(self.db, self.repo, docs, query, candidate_limit)
            keyword = [row for row, score in lexical_hits]
        scores = {}
        # RRF 融合排名，不直接相加余弦相似度与关键词分数。
        for ranking in [[h["id"] for h in semantic], [c.id for c in keyword]]:
            for rank, pk in enumerate(ranking, 1):
                scores[pk] = scores.get(pk, 0) + 1 / (60 + rank)
        chunks = await self.repo.search_chunks_by_ids(self.db, list(scores), ids)
        candidates = [{"chunk_id": c.id, "doc_id": c.doc_id, "title": doc_map[c.doc_id].title, "category": doc_map[c.doc_id].category, "file_id": doc_map[c.doc_id].file_id, "content": c.content, "page": c.page_no, "score": scores[c.id], "semantic_score": next((h["score"] for h in semantic if h["id"] == c.id), None)} for c in chunks]
        candidates.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        revisions = await self.repo.published_revisions(self.db, ids)
        for candidate in candidates:
            revision = revisions.get(candidate['doc_id'])
            candidate['revision_id'] = revision.id if revision else None
            candidate['revision_no'] = revision.revision_no if revision else None
        if use_rerank:
            candidates = await (self.reranker or rerank)(query, candidates)
        if group_documents:
            by_document = {}
            for candidate in candidates:
                by_document.setdefault(candidate["doc_id"], candidate)
            candidates = list(by_document.values())
        results = candidates[:top_k]
        await self._attach_parent_context(results, chunks, revisions)
        return results

    async def _attach_parent_context(self, results, chunks, revisions):
        """仅扩展已授权的最终命中，使用当前发布版本的父片。"""
        by_id = {chunk.id: chunk for chunk in chunks}
        snapshots = {}
        for hit in results:
            doc_id = hit['doc_id']
            revision = revisions.get(doc_id)
            file_id = getattr(revision, 'preview_file_id', None)
            index = getattr(by_id.get(hit['chunk_id']), 'chunk_index', None)
            if file_id is None or type(index) is not int or index < 0:
                continue
            if doc_id not in snapshots:
                snapshots[doc_id] = {}
                try:
                    file = await KnowledgeRevisionRepository().file(self.db, file_id)
                    if file is not None:
                        value = json.loads(await FileService(self.db).read(file))
                        if isinstance(value, dict):
                            snapshots[doc_id] = value
                except Exception:
                    # 快照读取失败时仍可返回MySQL中已经命中的子片。
                    logger.warning('读取父片快照失败 doc_id=%s', doc_id, exc_info=True)
            snapshot = snapshots[doc_id]
            children = snapshot.get('chunks', [])
            parents = snapshot.get('parents', [])
            if not isinstance(children, list) or not isinstance(parents, list) or index >= len(children):
                continue
            child = children[index]
            if not isinstance(child, dict) or child.get('content') != hit['content']:
                continue
            metadata = child.get('metadata')
            if not isinstance(metadata, dict):
                continue
            # 简历区域定位也来自当前版本快照。
            for key in ('resume_field', 'resume_entry', 'image_bbox'):
                if key in metadata:
                    hit[key] = metadata[key]
            parent_index = metadata.get('parent_index')
            if type(parent_index) is int and 0 <= parent_index < len(parents) and isinstance(parents[parent_index], str):
                hit['parent_content'] = parents[parent_index]
                hit['heading_path'] = metadata.get('heading_path', [])
