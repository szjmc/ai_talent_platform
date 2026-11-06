# 先持久记录外部写入意图，命名锁绑定连接，事务提交后仍需显式释放锁。
"""MinIO 持久化补偿日志 + MySQL 命名锁，不创建数据库表。
标签：[RAG组] [P0]。"""
import hashlib
import json
from uuid import uuid4
from sqlalchemy import select, func
from app.infrastructure.database.session import get_engine
from app.adapters.storage.base_storage import get_storage
from app.core.exceptions import DataConflictError

PREFIX = '_rag_ingest_jobs/v1/'
ARCHIVE_PREFIX = '_rag_ingest_jobs/archive/v1/'


async def archive_completed_job(storage, path, value):
    """先保存历史记录，再移除扫描项；失败时保留原项供下轮重试。"""
    if value.get('state') != 'done' or not path.startswith(PREFIX):
        return
    archive_path = ARCHIVE_PREFIX + path[len(PREFIX):]
    await storage.client.put_object(
        archive_path, json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json'
    )
    await storage.delete(path)


class IngestJob:
    def __init__(self, connection, key, job_id):
        self.connection, self.key, self.id = connection, key, job_id
        self.storage = get_storage('minio')
        self.values = {'id':job_id, 'lock_key':key, 'state':'pending', 'object_key':None, 'storage_type':'minio', 'doc_id':None}

    @property
    def path(self):
        return PREFIX + self.id + '.json'

    @classmethod
    async def start(cls, identity):
        key = 'rag:' + hashlib.sha256(identity.encode()).hexdigest()[:60]
        conn = await get_engine().connect()
        try:
            # 命名锁绑定物理连接，不随事务提交释放；跨 worker 串行到业务提交后。
            acquired = await conn.scalar(select(func.GET_LOCK(key, 15)))
            await conn.commit()
            if acquired != 1:
                raise DataConflictError('相同文档正在上传，请稍后重试')
            job = cls(conn, key, uuid4().hex)
            await job.record()
            return job
        except BaseException:
            await conn.invalidate()
            await conn.close()
            raise

    async def record(self, **values):
        self.values.update(values)
        await self.storage.client.put_object(self.path, json.dumps(self.values,ensure_ascii=False).encode('utf-8'),'application/json')
        if self.values['state'] == 'done':
            await archive_completed_job(self.storage, self.path, self.values)

    async def close(self):
        try:
            await self.connection.execute(select(func.RELEASE_LOCK(self.key)))
            await self.connection.commit()
        except BaseException:
            await self.connection.invalidate()
            raise
        finally:
            await self.connection.close()
