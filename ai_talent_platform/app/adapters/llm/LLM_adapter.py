"""旧问答代码兼容门面；不新建DeepSeek或向量客户端。
标签：[架构组] [P1]。"""
from app.ai.core.llm_manager import llm_manager
from app.ai.rag.embedding_service import EmbeddingService

class DeepSeekClient:
    async def chat(self,messages,temperature=0.3,max_tokens=1500,purpose='knowledge'):
        # LLM_PROVIDER=deepseek时由门面取得现有DeepSeekAdapter，复用配置、重试与计量。
        result=await llm_manager.chat(messages,purpose=purpose,temperature=temperature,max_tokens=max_tokens)
        return result.content.strip()

deepseek_client=DeepSeekClient()
# 兼容方若需要向量化必须使用这一路径，与入库模型保持一致。
embedding_service=EmbeddingService()
