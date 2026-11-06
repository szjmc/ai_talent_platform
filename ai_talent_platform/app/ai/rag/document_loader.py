# PDF 逐页优先提取文本，只有无文本页才走 OCR。页码和工作表行号随 Document 元数据传递，便于后续引用。
"""统一文档解析，保留页码、工作表及来源。
标签：[RAG组] [P1]。"""
from pathlib import Path
from langchain_core.documents import Document
from app.core.exceptions import FileError
from app.config.settings import settings

def _read_text(path: Path) -> str:
    """按常见编码回退读取文本：UTF-8(BOM) -> GB18030 -> UTF-8 -> Latin-1。

    中文简历/台账里 GBK（GB18030 超集）很常见，只按 UTF-8 读会直接
    UnicodeDecodeError，用户看到的是「文档解析失败」而无法自查编码。
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def load(file_path: str, metadata: dict | None = None) -> list[Document]:
    path = Path(file_path)
    meta = {'source':path.name, **(metadata or {})}
    try:
        suffix = path.suffix.lower()
        if suffix in {'.txt','.md'}:
            docs = [Document(page_content=_read_text(path),metadata=meta)]
        elif suffix == '.pdf':
            import pdfplumber
            import pypdfium2 as pdfium
            from app.ai.rag.ocr_service import recognize
            from app.ai.rag.resume_layout import resume_image_regions, resume_page_text

            is_resume = meta.get('category') == 'resume'
            docs=[]
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages)>settings.DOCUMENT_MAX_PAGES:
                    raise FileError('PDF 页数超过解析上限')
                renderer=None
                try:
                    for index,page in enumerate(pdf.pages):
                        text = resume_page_text(page) if is_resume else (page.extract_text() or '')
                        used_ocr=False
                        # 逐页判断，支持文字页与扫描页混排；不因首个空白页中止整份PDF。
                        if not text.strip() and settings.OCR_ENABLED:
                            if renderer is None:
                                renderer=pdfium.PdfDocument(str(path))
                            rendered_page=renderer[index]
                            try:
                                if rendered_page.get_width()*rendered_page.get_height()*settings.OCR_PDF_SCALE**2 > settings.OCR_MAX_PIXELS:
                                    raise FileError('PDF 渲染像素超过 OCR 上限')
                                bitmap=rendered_page.render(scale=settings.OCR_PDF_SCALE)
                                try:
                                    text=recognize(bitmap.to_pil())
                                finally:
                                    bitmap.close()
                            finally:
                                rendered_page.close()
                            used_ocr=True
                        if text.strip():
                            docs.append(Document(page_content=text,metadata={**meta,'page':index+1,'ocr':used_ocr}))
                            if is_resume and settings.OCR_ENABLED and not used_ocr:
                                for image_text, bounds in resume_image_regions(page, recognize):
                                    docs.append(Document(page_content=image_text, metadata={
                                        **meta, 'page': index + 1, 'ocr': True, 'image_bbox': bounds,
                                    }))
                finally:
                    if renderer is not None: renderer.close()
        elif suffix == '.docx':
            from docx import Document as WordDocument
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            word=WordDocument(path)
            parts=[]
            heading_lines = {}
            # 按正文真实顺序读取段落和表格，避免把所有表格搬到文末。
            for item in word.iter_inner_content():
                if isinstance(item, Paragraph):
                    # 内置标题样式保留层级，行号对应正文快照。
                    style = item.style.style_id if item.style else ''
                    level = style.removeprefix('Heading') if style.startswith('Heading') else ''
                    if level in tuple('123456789') and item.text.strip():
                        heading_lines[str(len(parts))] = int(level)
                    parts.extend(item.text.split('\n'))
                elif isinstance(item, Table):
                    for row in item.rows:
                        parts.extend(' | '.join(cell.text for cell in row.cells).split('\n'))
            docs = [Document(page_content='\n'.join(parts), metadata={**meta, 'heading_lines': heading_lines})]
        elif suffix == '.xlsx':
            from openpyxl import load_workbook
            book=load_workbook(path,read_only=True,data_only=True)
            docs=[]
            try:
                for sheet in book:
                    for index,row in enumerate(sheet.iter_rows(values_only=True),1):
                        text=' | '.join(str(c) if c is not None else '' for c in row)
                        if any(c is not None for c in row):
                            # 表内行号不是页码；正文保留工作表定位，现有数据库无需加字段。
                            docs.append(Document(page_content='工作表：'+sheet.title+'；行：'+str(index)+'\n'+text,metadata={**meta,'sheet':sheet.title,'row':index}))
            finally:
                book.close()
        elif suffix in {'.png','.jpg','.jpeg','.bmp','.tif','.tiff'}:
            if not settings.OCR_ENABLED:
                raise FileError('OCR 未启用')
            from PIL import Image, ImageSequence
            from app.ai.rag.ocr_service import recognize
            docs=[]
            with Image.open(path) as image:
                if getattr(image,'n_frames',1)>settings.DOCUMENT_MAX_PAGES:
                    raise FileError('图片帧数超过解析上限')
                for index,frame in enumerate(ImageSequence.Iterator(image),1):
                    text=recognize(frame.copy())
                    if text.strip(): docs.append(Document(page_content=text,metadata={**meta,'page':index,'ocr':True}))
        else:
            raise FileError('不支持该格式；旧版 doc/xls 请转换为 docx/xlsx')
        if not any(d.page_content.strip() for d in docs):
            raise FileError('未识别到有效文字，请检查扫描清晰度或 OCR 配置')
        return docs
    except FileError:
        raise
    except ImportError as exc:
        raise FileError('缺少解析依赖，请安装 requirements-rag.txt') from exc
    except Exception as exc:
        raise FileError('文档解析失败，请检查文件格式、编码或是否加密') from exc
