"""章节边界与父片快照兼容性验证。
标签：[RAG组] [P1]。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.ai.rag import retriever
from app.ai.rag.document_splitter import split_for_index
from app.config.settings import settings


@pytest.mark.asyncio
async def test_markdown_chunks_keep_section_boundaries(monkeypatch):
    monkeypatch.setattr(settings, 'CHUNK_STRATEGY', 'recursive')
    monkeypatch.setattr(settings, 'CHUNK_SIZE', 35)
    monkeypatch.setattr(settings, 'CHUNK_OVERLAP', 5)
    fence = chr(96) * 3
    doc = Document(page_content=f'# 制度\n## 请假\n请假应提前审批。\n{fence}\n# 示例代码\n{fence}\n## 报销\n报销需要凭证。',
                   metadata={'source': 'policy.md', 'page': 3})
    chunks, parents = await split_for_index([doc])
    assert len(parents) == 3
    assert all(not ('请假应' in chunk.page_content and '报销需要' in chunk.page_content) for chunk in chunks)
    assert all(chunk.metadata['page'] == 3 for chunk in chunks)
    leave = next(chunk for chunk in chunks if '请假应' in chunk.page_content)
    assert leave.metadata['heading_path'] == ['制度', '请假']
    assert '# 示例代码' in parents[leave.metadata['parent_index']]


@pytest.mark.asyncio
@pytest.mark.parametrize('snapshot_has_parent', [True, False])
async def test_parent_context_preserves_hit_and_supports_old_snapshots(monkeypatch, snapshot_has_parent):
    snapshot = {'chunks': [{'content': '命中正文', 'metadata': {'parent_index': 0, 'heading_path': ['制度']}}]}
    if snapshot_has_parent:
        snapshot['parents'] = ['完整章节上下文']
    monkeypatch.setattr(retriever.KnowledgeRevisionRepository, 'file', AsyncMock(return_value=object()))
    monkeypatch.setattr(retriever.FileService, 'read', AsyncMock(return_value=json.dumps(snapshot)))
    hit = {'doc_id': 2, 'chunk_id': 5, 'content': '命中正文', 'score': 0.8}
    await retriever.Retriever(None)._attach_parent_context(
        [hit], [SimpleNamespace(id=5, chunk_index=0)], {2: SimpleNamespace(preview_file_id=10)},
    )
    assert hit['content'] == '命中正文' and hit['score'] == 0.8
    assert hit.get('parent_content') == ('完整章节上下文' if snapshot_has_parent else None)



@pytest.mark.asyncio
async def test_resume_chunks_keep_field_entry_and_source(monkeypatch):
    monkeypatch.setattr(settings, 'CHUNK_SIZE', 65)
    monkeypatch.setattr(settings, 'CHUNK_OVERLAP', 10)
    documents = [
        Document(page_content='专业技能：Python\n项目经历\n项目名称：缓存系统\n' + '优化缓存命中率。' * 15,
                 metadata={'page': 1}),
        Document(page_content='降低接口延迟。\n项目名称：报表系统\n完成数据聚合。', metadata={'page': 2}),
        Document(page_content='图表显示吞吐量增长', metadata={'page': 2, 'image_bbox': [1, 2, 3, 4]}),
    ]
    chunks, parents = await split_for_index(documents, category='resume')
    assert not parents and max(len(c.page_content) for c in chunks) <= 65
    assert chunks[0].metadata['resume_field'] == '技能'
    continuation = next(c for c in chunks if '降低接口延迟' in c.page_content)
    assert continuation.metadata['page'] == 2
    assert '缓存系统' in continuation.metadata['resume_entry']
    assert '报表系统' not in continuation.page_content
    assert chunks[-1].metadata['image_bbox'] == [1, 2, 3, 4]
