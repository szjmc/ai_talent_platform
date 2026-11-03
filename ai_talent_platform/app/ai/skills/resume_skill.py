"""
模块：app/ai/skills/resume_skill.py
职责：简历处理技能（供 Agent 调用）。

============================ 实现思路与代码要点 ============================
技能方法：
    parse_resume(file_id) -> dict          解析简历，返回结构化数据
    extract_skills(text) -> list[str]      技能抽取（用于打标签）
    dedupe_resume(parsed) -> bool          与库中人才查重（向量相似度）
要点：
- Skill 是 Agent 的「工具」，内部调用 Service（保持权限与事务一致）；
- 每个技能需声明：名称 / 描述 / 入参 Schema（供 LLM function calling 使用）。
待实现清单：
    - [ ] 上述方法 + 工具描述（JSON Schema）
    - [ ] 注册到 skill_registry
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
