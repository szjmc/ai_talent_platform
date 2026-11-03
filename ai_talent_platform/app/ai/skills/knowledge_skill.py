"""
模块：app/ai/skills/knowledge_skill.py
职责：知识库检索技能。

============================ 实现思路与代码要点 ============================
技能方法：
    search_knowledge(question, top_k) -> list[{content, source, score}]
要点：
- 复用 ai/rag/retriever，权限过滤使用当前用户上下文（不信任 LLM 传入的参数）；
- 返回片段需附带来源，供答案溯源。
待实现清单：
    - [ ] search_knowledge
    - [ ] 注册到 skill_registry
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
