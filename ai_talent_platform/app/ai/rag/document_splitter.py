"""
模块：app/ai/rag/document_splitter.py
职责：文档切分（Chunk）。

标签：[RAG组] [P1]（按 token 切分 + 重叠窗口，被索引流程依赖）

============================ 实现思路与代码要点 ============================
策略：
1. 递归字符切分（优先按段落 -> 句子 -> 字符），chunk_size=500，overlap=80；
2. 结构化文档按标题层级切分（制度条款「第 X 条」保持完整，避免语义割裂）；
3. 表格类内容不切分（整表作为一个 chunk）。
输出：list[Chunk{content, index, page_no, meta}]
注意：overlap 过大会导致召回重复，过小会丢上下文，需在测试中调参。
待实现清单：
    - [ ] split_text / split_by_headings / split_document
    - [ ] 参数从 config 读取（chunk_size / overlap）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
