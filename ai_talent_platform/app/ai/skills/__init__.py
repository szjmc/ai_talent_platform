"""
模块：app/ai/skills/__init__.py
职责：Skills 可编排技能组件库（Agent / LLM 的工具层）。

标签：[架构组] [P0]

导出约定：
- 基类与结果封装：BaseSkill / SkillResult / SkillContext / 各类 Skill 异常；
- 注册与发现：bootstrap_skills / get_skill / list_skills / list_tools；
- 具体技能类与函数式入口：knowledge_search / matching_* / resume_* / sql_query。

用法：
    from app.ai.skills import bootstrap_skills, get_skill
    bootstrap_skills()                       # 应用启动时调用一次
    result = await get_skill("sql_query").run(question="各部门人才数量")
    if result.ok: ...

注意：技能内部只从 AI 上下文取身份与权限，忽略 LLM 传入的身份参数。
"""

from __future__ import annotations

from app.ai.skills.base import (
    BaseSkill,
    SkillContext,
    SkillError,
    SkillParamError,
    SkillResult,
    SkillTimeoutError,
    SkillUnavailableError,
    get_skill,
    import_dependency,
    list_skills,
    list_tools,
    load_skill_context,
    require_dependency,
)
from app.ai.skills.knowledge_skill import KnowledgeSearchSkill, search_knowledge
from app.ai.skills.matching_skill import (
    CandidateListSkill,
    MatchingScoreSkill,
    PositionSearchSkill,
    calculate_matching_score,
    compute_match_score,
    list_candidates,
    search_positions,
)
from app.ai.skills.registry_bootstrap import (
    EXPECTED_SKILLS,
    bootstrap_skills,
    describe_skills,
)
from app.ai.skills.resume_skill import (
    ResumeDedupeSkill,
    ResumeParseSkill,
    ResumeSkillExtractSkill,
    dedupe_resume,
    extract_skills,
    extract_skills_by_dictionary,
    parse_resume,
)
from app.ai.skills.sql_skill import SqlQuerySkill, query_database

__all__ = [
    # 基类与异常
    "BaseSkill",
    "SkillContext",
    "SkillResult",
    "SkillError",
    "SkillParamError",
    "SkillTimeoutError",
    "SkillUnavailableError",
    "import_dependency",
    "load_skill_context",
    "require_dependency",
    # 注册与发现
    "EXPECTED_SKILLS",
    "bootstrap_skills",
    "describe_skills",
    "get_skill",
    "list_skills",
    "list_tools",
    # 知识库检索技能
    "KnowledgeSearchSkill",
    "search_knowledge",
    # 人岗匹配技能
    "MatchingScoreSkill",
    "PositionSearchSkill",
    "CandidateListSkill",
    "compute_match_score",
    "calculate_matching_score",
    "search_positions",
    "list_candidates",
    # 简历技能
    "ResumeParseSkill",
    "ResumeSkillExtractSkill",
    "ResumeDedupeSkill",
    "parse_resume",
    "extract_skills",
    "extract_skills_by_dictionary",
    "dedupe_resume",
    # 数据查询技能
    "SqlQuerySkill",
    "query_database",
]
