# 递归分块按字符长度保留重叠。语义分块比较相邻单元的向量相似度，且不跨原始 Document 合并。
"""LangChain 递归切分，保留来源并生成稳定切片序号。
标签：[RAG组] [P1]。"""
import re
from app.config.settings import settings
import unicodedata
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

def split_document(documents, chunk_size=None, overlap=None):
    """流程：规范 Unicode → 清除控制字符 → 段落优先的重叠分块。长度单位为字符。"""
    chunk_size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
    overlap = settings.CHUNK_OVERLAP if overlap is None else overlap
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("必须满足 0 <= overlap < chunk_size")
    cleaned = []
    for doc in documents:
        text = unicodedata.normalize("NFKC", doc.page_content)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if text:
            cleaned.append(Document(page_content=text, metadata=dict(doc.metadata)))
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap, separators=["\n\n", "\n", "。", "；", " ", ""])
    chunks = splitter.split_documents(cleaned)
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index
    return chunks

async def split_semantic(documents, embeddings=None):
    """相邻句向量相似度断点分块；不跨页或跨工作表合并，长句继续递归切分。"""
    import math
    from app.ai.rag.embedding_service import EmbeddingService
    embeddings=embeddings or EmbeddingService()
    output=[]
    for doc in documents:
        sentences=[s.strip() for s in re.split(r'(?<=[。！？.!?])\s*|\n+',doc.page_content) if s.strip()]
        # 超长单句先切成长度受限的小段，保证模型输入可控。
        units=[]
        for sentence in sentences:
            units.extend(c.page_content for c in split_document([Document(page_content=sentence,metadata=doc.metadata)],overlap=0))
        if not units: continue
        vectors=await embeddings.embed_texts(units)
        current=''
        previous=None
        for text,vector in zip(units,vectors):
            similarity=1.0
            if previous is not None:
                denominator=math.sqrt(sum(x*x for x in previous)*sum(x*x for x in vector))
                similarity=sum(a*b for a,b in zip(previous,vector))/denominator if denominator else 0
            boundary=current and (similarity < settings.SEMANTIC_SPLIT_THRESHOLD or len(current)+len(text)+1>settings.CHUNK_SIZE)
            if boundary:
                output.append(Document(page_content=current,metadata=dict(doc.metadata)))
                # 仅在长度限制导致分块时保留尾部上下文，语义断点不带入上一主题。
                tail=current[-settings.CHUNK_OVERLAP:] if similarity>=settings.SEMANTIC_SPLIT_THRESHOLD and settings.CHUNK_OVERLAP else ''
                current=tail if len(tail)+len(text)+1<=settings.CHUNK_SIZE else ''
            current=(current+'\n'+text).strip()
            previous=vector
        if current: output.append(Document(page_content=current,metadata=dict(doc.metadata)))
    for i,chunk in enumerate(output): chunk.metadata['chunk_index']=i
    return output


# 切片规则更新后，旧草稿需要重新预览。
SECTION_SPLIT_VERSION = 1
_MARKDOWN_HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$')
_NUMBERED_HEADING = re.compile(r'^\s*(第[一二三四五六七八九十百0-9]+[章节篇部]|[0-9]+(?:\.[0-9]+)+)\s+(.{1,72})$')


def split_sections(doc):
    """按标题分节，保留原始页码和标题路径。"""
    if 'sheet' in doc.metadata or doc.metadata.get('ocr'):
        return [doc]
    markdown = str(doc.metadata.get('source', '')).lower().endswith('.md')
    word_headings = doc.metadata.get('heading_lines', {})
    headings, sections, lines = [], [], []
    fence = None

    def flush():
        text = '\n'.join(lines).strip()
        if text:
            metadata = dict(doc.metadata)
            metadata.pop('heading_lines', None)
            if headings:
                metadata['heading_path'] = [title for _, title in headings]
            sections.append(Document(page_content=text, metadata=metadata))
        lines.clear()

    for index, line in enumerate(doc.page_content.splitlines()):
        marker = re.match(r'^ {0,3}(\x60{3,}|~{3,})', line) if markdown else None
        if marker:
            delimiter = marker[1]
            if fence is None:
                fence = delimiter
            elif delimiter.startswith(fence) and not line[marker.end():].strip():
                fence = None
            lines.append(line)
            continue
        heading = None
        if not fence:
            match = _MARKDOWN_HEADING.match(line) if markdown else None
            numbered = _NUMBERED_HEADING.match(line) if not markdown else None
            level = word_headings.get(str(index))
            if level:
                heading = (level, line.strip())
            elif match:
                heading = (len(match[1]), match[2])
            elif numbered:
                heading = (numbered[1].count('.') + 1, line.strip())
        if heading:
            flush()
            level, title = heading
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
        lines.append(line)
    flush()
    return sections


async def split_for_index(documents, category=None):
    """子片用于召回，父片在版本快照中只保存一次。"""
    import asyncio

    if category == 'resume':
        return await asyncio.to_thread(split_resume, documents), []

    sections, parents = [], []
    for doc in documents:
        for section in split_sections(doc):
            if not section.metadata.get('heading_path'):
                sections.append(section)
                continue
            # 限制父片大小，长章节沿同一标题路径继续拆分。
            for parent in split_document([section], chunk_size=settings.CHUNK_SIZE * 4, overlap=0):
                parent.metadata['parent_index'] = len(parents)
                parents.append(parent.page_content)
                sections.append(parent)
    if settings.CHUNK_STRATEGY == 'semantic':
        chunks = await split_semantic(sections)
    else:
        chunks = await asyncio.to_thread(split_document, sections)
    return chunks, parents


RESUME_SPLIT_VERSION = 1
_RESUME_FIELDS = {
    '基本信息': '基本信息', '个人信息': '基本信息', '个人简介': '个人简介', '求职意向': '求职意向',
    '专业技能': '技能', '个人技能': '技能', '技能特长': '技能', '技能': '技能', 'skills': '技能',
    '工作经历': '工作经历', '工作经验': '工作经历', '实习经历': '实习经历',
    'work experience': '工作经历', 'experience': '工作经历', 'internships': '实习经历',
    '项目经历': '项目经历', '项目经验': '项目经历', 'projects': '项目经历',
    '教育经历': '教育经历', '教育背景': '教育经历', 'education': '教育经历',
    '证书': '证书', '荣誉证书': '证书', 'certifications': '证书', '自我评价': '自我评价',
}
_RESUME_ENTRY = re.compile(
    r'(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?\s*(?:至|到|[-–—~～]+)\s*'
    r'(?:(?:19|20)\d{2}|至今|现在|present)', re.IGNORECASE,
)


def split_resume(documents):
    """按简历字段及可识别的经历分组，在单个来源页内递归切分。"""
    output = []
    field, entry = '简历概况', ''
    for doc in documents:
        is_image = 'image_bbox' in doc.metadata
        lines = []

        def flush():
            if not lines:
                return
            label = '图片文字' if is_image else field
            prefix = label + (f'｜{entry[:80]}' if entry and not is_image else '') + '\n'
            # 给字段标签留空间，确保带标签的子片仍满足配置上限。
            prefix = prefix[:settings.CHUNK_SIZE // 3]
            size = settings.CHUNK_SIZE - len(prefix)
            metadata = {**doc.metadata, 'resume_field': label}
            metadata.pop('heading_lines', None)
            if entry and not is_image:
                metadata['resume_entry'] = entry
            body = Document(page_content='\n'.join(lines), metadata=metadata)
            for chunk in split_document([body], chunk_size=size, overlap=min(settings.CHUNK_OVERLAP, size - 1)):
                chunk.page_content = prefix + chunk.page_content
                chunk.metadata['chunk_index'] = len(output)
                output.append(chunk)
            lines.clear()

        for line in doc.page_content.splitlines():
            line = line.strip()
            if not line:
                continue
            if is_image:
                lines.append(line)
                continue
            parts = re.split(r'[:：]', line.lstrip('# '), maxsplit=1)
            title = parts[0]
            detail = parts[1].strip() if len(parts) == 2 else ''
            section = _RESUME_FIELDS.get(title.strip().casefold())
            if section:
                flush()
                field, entry = section, ''
                if detail:
                    lines.append(detail)
                continue
            # 日期区间或显式项目/公司名作为新经历边界。
            if field in {'工作经历', '实习经历', '项目经历', '教育经历'} and (
                (len(line) <= 120 and _RESUME_ENTRY.search(line))
                or re.match(r'^(?:项目名称|项目名|公司名称|公司名)\s*[:：]\s*\S', line)
            ):
                flush()
                entry = line
            lines.append(line)
        flush()
    return output
