# 封装题库集合的向量读写，将检索结果关联回题库记录。
"""独立题库Milvus适配器；共享连接，不变更既有知识集合。
标签：[RAG组] [P1]。
"""
import asyncio
import json
import math

from app.config.settings import settings
from app.core.exceptions import AIError


class QuestionBankVectorAdapter:
    FIELD_TYPES = {
        'question_id': 5, 'bank_id': 5, 'content_version': 5,
        'technology': 21, 'difficulty': 21, 'question_type': 21,
        'index_signature': 21, 'embedding': 101,
    }

    def __init__(self, client=None, collection=None):
        self.client = client
        self.collection = collection or settings.MILVUS_COLLECTION_QUESTIONS
        self._validated_dimension = None

    async def _client(self):
        if self.client is not None:
            return self.client
        from app.infrastructure.vector.milvus import get_milvus
        return await get_milvus()

    @staticmethod
    def validate_vector(vector):
        if (len(vector) != settings.EMBEDDING_DIM
                or not all(math.isfinite(value) for value in vector) or not any(vector)):
            raise AIError('题库向量维度或数值无效，请核对EMBEDDING_DIM与实际模型')

    async def ensure_collection(self):
        if self._validated_dimension == settings.EMBEDDING_DIM:
            return
        client = await self._client()
        if not await asyncio.to_thread(client.has_collection, collection_name=self.collection):
            raise AIError('题库向量集合不存在，请执行scripts/question_bank.py init')
        description = await asyncio.to_thread(client.describe_collection, collection_name=self.collection)
        fields = {field['name']: field for field in description.get('fields', [])}
        if any(name not in fields or int(fields[name]['type']) != kind for name, kind in self.FIELD_TYPES.items()):
            raise AIError('题库集合Schema不兼容，请使用独立集合名称，禁止覆盖已有集合')
        if description.get('auto_id') or not fields['question_id'].get('is_primary'):
            raise AIError('题库集合必须使用非自动生成的question_id主键')
        if int(fields['embedding'].get('params', {}).get('dim', 0)) != settings.EMBEDDING_DIM:
            raise AIError('题库集合维度与EMBEDDING_DIM不一致')
        self._validated_dimension = settings.EMBEDDING_DIM

    async def initialize_collection(self):
        """仅初始化脚本调用；幂等创建，不删除、重建或修改已有集合。"""
        from pymilvus import DataType
        client = await self._client()
        if not await asyncio.to_thread(client.has_collection, collection_name=self.collection):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field('question_id', DataType.INT64, is_primary=True)
            schema.add_field('bank_id', DataType.INT64)
            schema.add_field('content_version', DataType.INT64)
            for name, length in {'technology': 64, 'difficulty': 16, 'question_type': 16, 'index_signature': 64}.items():
                schema.add_field(name, DataType.VARCHAR, max_length=length)
            schema.add_field('embedding', DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM)
            indexes = client.prepare_index_params()
            indexes.add_index(field_name='embedding', index_type='AUTOINDEX', metric_type='COSINE')
            await asyncio.to_thread(client.create_collection, collection_name=self.collection, schema=schema, index_params=indexes)
        await self.ensure_collection()
        await asyncio.to_thread(client.load_collection, collection_name=self.collection)

    async def upsert(self, questions, vectors, signature):
        if len(questions) != len(vectors):
            raise AIError('题库向量条数与题目条数不一致')
        if not questions:
            return
        for vector in vectors:
            self.validate_vector(vector)
        await self.ensure_collection()
        rows = [{
            'question_id': question.id, 'bank_id': question.bank_id,
            'content_version': question.content_version, 'technology': question.technology,
            'difficulty': question.difficulty, 'question_type': question.question_type,
            'index_signature': signature, 'embedding': vector,
        } for question, vector in zip(questions, vectors)]
        client = await self._client()
        await asyncio.to_thread(client.upsert, collection_name=self.collection, data=rows)

    async def search(self, vector, request, question_type, candidates, signature, limit):
        if not candidates:
            return []
        self.validate_vector(vector)
        await self.ensure_collection()
        # 白名单来自MySQL权限及当前内容版本，旧向量不能挤占有效题目的召回配额。
        versions = {}
        for question_id, content_version in candidates:
            versions.setdefault(int(content_version), []).append(int(question_id))
        allowed = ' or '.join(
            '(content_version == ' + str(version) + ' and question_id in ' + json.dumps(ids) + ')'
            for version, ids in versions.items()
        )
        conditions = ['(' + allowed + ')']
        for field, value in {
            'technology': request.technology, 'difficulty': request.difficulty,
            'question_type': question_type, 'index_signature': signature,
        }.items():
            conditions.append(field + ' == ' + json.dumps(value))
        client = await self._client()
        result = await asyncio.to_thread(
            client.search, collection_name=self.collection, data=[vector], anns_field='embedding',
            filter=' and '.join(conditions), limit=limit,
            output_fields=['question_id', 'content_version'], consistency_level='Strong',
            search_params={'metric_type': 'COSINE'},
        )
        return [{
            'question_id': int(hit['entity']['question_id']),
            'content_version': int(hit['entity']['content_version']), 'score': float(hit['distance']),
        } for hit in result[0]]

    async def delete_questions(self, ids):
        if not ids:
            return
        await self.ensure_collection()
        client = await self._client()
        await asyncio.to_thread(client.delete, collection_name=self.collection, ids=[int(value) for value in ids])
