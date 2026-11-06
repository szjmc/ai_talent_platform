# 题库向量化集中在此层，供索引和题目查询复用，题库向量与文档切片分别管理。
"""知识库与题库共用 LangChain Embeddings，沿用平台向量配置。
标签：[RAG组] [P1]。
"""
import hashlib
import json

from langchain_core.embeddings import Embeddings

from app.adapters.llm.provider_config import resolve_api_key, resolve_base_url
from app.ai.core.model_selector import select_embedding_model, select_provider
from app.config.settings import settings
from app.core.exceptions import AIError


def question_embedding_signature() -> str:
    provider = select_provider('embed')
    config = [provider, resolve_base_url(provider), select_embedding_model(provider), settings.EMBEDDING_DIM]
    return hashlib.sha256(json.dumps(config, ensure_ascii=False).encode()).hexdigest()


def get_embeddings() -> Embeddings:
    provider = select_provider('embed')
    model = select_embedding_model(provider)
    if not model:
        raise AIError('请配置LLM_EMBED_PROVIDER和LLM_EMBED_MODEL')
    if provider == 'ollama':
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model=model, base_url=resolve_base_url(provider))
    if provider == 'deepseek':
        raise AIError('DeepSeek不提供向量接口，请单独配置LLM_EMBED_PROVIDER')
    from langchain_openai import OpenAIEmbeddings
    key = resolve_api_key(provider)
    if not key:
        raise AIError('向量供应商未配置API密钥')
    return OpenAIEmbeddings(
        model=model, api_key=key, base_url=resolve_base_url(provider),
        chunk_size=settings.EMBEDDING_BATCH_SIZE,
        request_timeout=settings.LLM_TIMEOUT, max_retries=settings.LLM_MAX_RETRIES,
        check_embedding_ctx_length=False,
    )


get_question_embeddings = get_embeddings
