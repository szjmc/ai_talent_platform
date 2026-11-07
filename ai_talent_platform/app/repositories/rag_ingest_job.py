# 先持久记录外部写入意图，命名锁绑定连接，事务提交后仍需显式释放锁。
"""入库补偿日志（跨存储后端） + 数据库命名锁，不创建数据库表。
标签：[RAG组] [P0]。

修复说明：本模块原先硬编码 `get_storage('minio')` 并直接调用 MinIO SDK 的
`client.put_object` —— 只要部署环境没有 MinIO（例如 STORAGE_BACKEND=local），
知识库 / 文件中心的每一次上传都会在「持久化意图」这一步直接失败
（对象存储连接失败，已重试3次），整个上传链路不可用。

现在：
- 存储后端跟随 settings.STORAGE_BACKEND（local / minio / oss / s3 均可）；
- 对象读写收敛到下面的 put/get/list/delete 帮助函数：对象型后端走原生
  client.put_object / list_keys，本地后端把对象键当相对路径写入上传根目录，
  两条路径的键语义一致（_rag_ingest_jobs/v1/xxx.json）。
"""
import hashlib
import json
from uuid import uuid4
from sqlalchemy import select, func
from app.infrastructure.database.session import get_engine
from app.infrastructure.database.advisory_lock import acquire_advisory_lock, release_advisory_lock
from app.adapters.storage.base_storage import get_storage
from app.core.exceptions import DataConflictError

PREFIX = '_rag_ingest_jobs/v1/'
ARCHIVE_PREFIX = '_rag_ingest_jobs/archive/v1/'


def _object_client(storage):
    """对象型后端（minio/oss/s3）的原生客户端；本地后端返回 None。"""
    client = getattr(storage, 'client', None)
    if client is not None and hasattr(client, 'put_object'):
        return client
    return None


def _file_storage():
    from app.infrastructure.storage import file_storage
    return file_storage


async def put_job_object(storage, key: str, data: bytes, content_type: str = 'application/json') -> None:
    client = _object_client(storage)
    if client is not None:
        await client.put_object(key, data, content_type)
        return
    # 本地存储：对象键即相对路径（safe_resolve 阻断路径穿越）
    _file_storage().write_bytes(key, data)


async def get_job_object(storage, key: str) -> bytes:
    client = _object_client(storage)
    if client is not None:
        return await client.get_object(key)
    return _file_storage().read_bytes(key)


async def list_job_keys(storage, prefix: str) -> list:
    client = _object_client(storage)
    lister = None
    if client is not None and hasattr(client, 'list_keys'):
        lister = client
    elif hasattr(storage, 'list_keys'):
        # 兼容在存储适配器层面直接提供列举的后端/测试桩
        lister = storage
    if lister is not None:
        return list(await lister.list_keys(prefix))
    fs = _file_storage()
    root = fs.storage_root() / prefix
    if not root.exists():
        return []
    base = fs.storage_root()
    return [str(p.relative_to(base)) for p in sorted(root.rglob('*.json'))]


async def delete_job_object(storage, key: str) -> None:
    client = _object_client(storage)
    if client is not None:
        await client.delete_object(key)
        return
    fs = _file_storage()
    if fs.exists(key):
        fs.remove(key)


async def archive_completed_job(storage, path, value):
    """先保存历史记录，再移除扫描项；失败时保留原项供下轮重试。"""
    if value.get('state') != 'done' or not path.startswith(PREFIX):
        return
    archive_path = ARCHIVE_PREFIX + path[len(PREFIX):]
    await put_job_object(storage, archive_path,
                         json.dumps(value, ensure_ascii=False).encode('utf-8'))
    await delete_job_object(storage, path)


class IngestJob:
    def __init__(self, connection, key, job_id):
        self.connection, self.key, self.id = connection, key, job_id
        # 后端跟随配置（不再硬编码 minio）；本地/对象存储都能跑
        self.storage = get_storage()
        self.values = {'id':job_id, 'lock_key':key, 'state':'pending', 'object_key':None,
                       'storage_type':getattr(self.storage, 'name', 'unknown'), 'doc_id':None}

    @property
    def path(self):
        return PREFIX + self.id + '.json'

    @classmethod
    async def start(cls, identity):
        key = 'rag:' + hashlib.sha256(identity.encode()).hexdigest()[:60]
        conn = await get_engine().connect()
        try:
            # 命名锁绑定物理连接，不随事务提交释放；跨 worker 串行到业务提交后。
            acquired = await acquire_advisory_lock(conn, key, 15)
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
        await put_job_object(self.storage, self.path,
                             json.dumps(self.values, ensure_ascii=False).encode('utf-8'))
        if self.values['state'] == 'done':
            await archive_completed_job(self.storage, self.path, self.values)

    async def close(self):
        try:
            await release_advisory_lock(self.connection, self.key)
            await self.connection.commit()
        except BaseException:
            await self.connection.invalidate()
            raise
        finally:
            await self.connection.close()
