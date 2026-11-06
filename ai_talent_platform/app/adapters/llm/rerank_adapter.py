"""SiliconFlow 兼容重排接口；只发送已授权候选。
标签：[架构组] [P1]。"""
import math
import httpx
from app.config.settings import settings
from app.core.exceptions import AIError

async def rerank(query,candidates):
    if not candidates: return []
    if not settings.RERANK_MODEL:
        raise AIError('未配置 RERANK_MODEL，不能启用模型重排')
    # 单独配置重排凭证；可复用现有 OpenAI 兼容供应商配置，不打印密钥或响应正文。
    key=settings.RERANK_API_KEY or settings.OPENAI_API_KEY
    url=(settings.RERANK_BASE_URL or settings.OPENAI_BASE_URL).rstrip('/')+'/rerank'
    try:
        async with httpx.AsyncClient(timeout=settings.RERANK_TIMEOUT) as client:
            response=await client.post(url,headers={'Authorization':'Bearer '+key},json={
                'model':settings.RERANK_MODEL,'query':query,
                'documents':[c['content'] for c in candidates],'top_n':len(candidates),'return_documents':False})
            if response.status_code!=200:
                raise AIError('模型重排服务返回 HTTP '+str(response.status_code))
            results=response.json()['results']
        seen=set();ranked=[]
        for result in results:
            index=result['index'];score=float(result['relevance_score'])
            if type(index) is not int or not 0<=index<len(candidates) or index in seen or not math.isfinite(score):
                raise ValueError('invalid rerank result')
            seen.add(index)
            ranked.append({**candidates[index],'rerank_score':score,'rerank_method':settings.RERANK_MODEL})
        if len(seen)!=len(candidates): raise ValueError('incomplete rerank result')
        return sorted(ranked,key=lambda item:-item['rerank_score'])
    except AIError:
        raise
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
        raise AIError('模型重排失败，请检查连接和响应格式') from exc
