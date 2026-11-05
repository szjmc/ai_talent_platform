"""
模块：app/ai/rag/document_loader.py
职责：文档读取（PDF / Word / Excel / TXT / 图片 OCR）。

标签：[RAG组] [P1]  依赖：[架构组] 文件存储（pdf/docx/txt 解析）

============================ 实现思路与代码要点 ============================
统一接口：
    load(file_path: str) -> list[Document]     # Document = {content, metadata{source, page}}
实现要点：
- 按扩展名分派：pdf（pdfplumber）、docx（python-docx）、xlsx（openpyxl）、txt、图片（PaddleOCR）；
- metadata 保留页码 / 工作表名，便于回答时给出精确出处；
- 大文件流式读取，避免一次性占满内存；
- 解析失败抛 core.exceptions.FileError，并记录到知识文档的 index_error。
待实现清单：
    - [ ] load_pdf / load_docx / load_excel / load_txt / load_image
    - [ ] 统一 load() 分派
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
