"""手动执行 MinIO 入库日志恢复；不修改表结构。
标签：[RAG组] [P1]。"""
import os
os.environ.setdefault('USERNAME','Windows')
import asyncio
from app.services.rag_recovery import recover
from app.infrastructure.database.session import close_db
from app.infrastructure.vector.milvus import close_milvus
async def main():
    try: await recover()
    finally:
        await close_db()
        await close_milvus()
if __name__=='__main__': asyncio.run(main())
