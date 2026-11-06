"""只读重放已上传Excel的解析和语义分块，不写数据库或向量库。
标签：[RAG组] [P1]
"""
import asyncio
import tempfile
from pathlib import Path
from app.adapters.storage.base_storage import get_storage
from app.ai.rag.document_loader import load
from app.ai.rag.document_splitter import split_semantic

async def main():
    key = 'knowledge_revisiontraining/2026/11/82f556decdb24f8d8754b0f09ac526fa.xlsx'
    data = await get_storage('minio').read(key)
    with tempfile.TemporaryDirectory(dir='data') as folder:
        path = Path(folder) / 'diagnose.xlsx'
        path.write_bytes(data)
        docs = load(str(path))
        print('PARSED_ROWS', len(docs), flush=True)
        chunks = await split_semantic(docs)
        print('PREVIEW_OK', len(chunks), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
