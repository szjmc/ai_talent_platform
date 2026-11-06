"""分块断点、中文多词召回与重排固定样例验证。
标签：[RAG组] [P1]。"""
import asyncio
from types import SimpleNamespace
from langchain_core.documents import Document
from app.ai.rag.document_splitter import split_semantic
from app.ai.rag.lexical_search import bm25_rank,lexical_rerank
class FakeEmbedding:
 async def embed_texts(self,texts):
  return [[1.,0.] if 'Python' in text else [0.,1.] for text in texts]
async def main():
 chunks=await split_semantic([Document(page_content='Python开发岗位。Python异步编程。培训报销制度。',metadata={'page':2,'file_id':9})],FakeEmbedding())
 assert len(chunks)==2 and all(c.metadata['page']==2 for c in chunks)
 assert 'Python' in chunks[0].page_content and '报销' in chunks[1].page_content
 print('SEMANTIC_BOUNDARY_SOURCE_PASS')
 rows=[SimpleNamespace(id=1,content='员工申请培训费用报销，需要提交发票。'),SimpleNamespace(id=2,content='Python开发岗位需要异步编程能力。'),SimpleNamespace(id=3,content='培训课程每周三开展。')]
 ranked=bm25_rank('培训 报销 发票',rows,3)
 assert ranked and ranked[0][0].id==1 and all(row.id!=2 for row,score in ranked)
 print('CHINESE_MULTI_TERM_BM25_PASS')
 ranked=await lexical_rerank('培训 报销 发票',[{'chunk_id':3,'content':rows[2].content,'score':0.1},{'chunk_id':1,'content':rows[0].content,'score':0.05}])
 assert ranked[0]['chunk_id']==1 and ranked[0]['rerank_method']=='lexical_coverage'
 print('OPTIONAL_LEXICAL_RERANK_PASS')
if __name__=='__main__':asyncio.run(main())
