"""
gxd-按原架构约定补充向量库抽象接口和适配器注册表。

标签：[RAG组] [P0]（向量库抽象接口，供各后端实现）
"""
from abc import ABC, abstractmethod
from app.patterns.registry import Registry

vector_registry = Registry("vector_store")

class BaseVectorStore(ABC):
    @abstractmethod
    async def create_collection(self, name, dim):
        """gxd-幂等创建指定维度的集合。"""

    @abstractmethod
    async def upsert(self, collection, ids, vectors, metadata):
        """gxd-写入或更新带元数据的向量。"""

    @abstractmethod
    async def search(self, collection, vector, top_k=10, expr=None):
        """gxd-在过滤范围内查询相似向量。"""

    @abstractmethod
    async def delete(self, collection, ids=None, expr=None):
        """gxd-按主键或条件删除向量。"""

    @abstractmethod
    async def drop(self, collection):
        """gxd-显式删除集合，仅供管理操作。"""