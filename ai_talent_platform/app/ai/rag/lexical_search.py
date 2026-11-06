# 将问题和切片转换为可比较的词项，BM25 使用词频与语料统计计算相关性。
"""中文 BM25 和本地词项重排；仅处理已授权候选。
标签：[RAG组] [P0]。"""
import re
import jieba
_STOP = {'的','了','是','和','与','在','有','请','什么','哪些','需要','如何'}

def tokenize(text):
    return [t.lower() for t in jieba.cut(text) if t.strip() and t not in _STOP and re.search(r'[\w\u4e00-\u9fff]',t)]

def bm25_rank(query, rows, limit):
    if not rows: return []
    corpus=[row.tokens if hasattr(row,"tokens") else tokenize(row.content) for row in rows]
    tokens=tokenize(query)
    if not tokens or not any(corpus): return []
    # BM25Plus 使用正 IDF，避免小样本中高频但有效的查询词得分为零。
    from rank_bm25 import BM25Plus
    scores=BM25Plus(corpus,delta=0).get_scores(tokens)
    ranked=[(row,float(score)) for row,score,words in zip(rows,scores,corpus) if set(words)&set(tokens)]
    ranked.sort(key=lambda pair:(-pair[1],pair[0].id))
    return ranked[:limit]

async def lexical_rerank(query, candidates):
    """本地词项覆盖重排，不冒充跨编码器模型；无匹配词时保留召回顺序。"""
    terms=set(tokenize(query))
    if not terms: return candidates
    result=[]
    for item in candidates:
        overlap=len(terms & set(tokenize(item['content']))) / len(terms)
        result.append({**item,'rerank_score':overlap,'rerank_method':'lexical_coverage'})
    return sorted(result,key=lambda item:(-item['rerank_score'],-item['score'],item['chunk_id']))
