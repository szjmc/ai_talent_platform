"""
模块：app/ai/skills/registry_bootstrap.py
职责：Skills 注册引导 —— 导入即注册，供应用启动与 Agent 编排使用。

标签：[架构组] [P0]

============================ 实现思路与代码要点 ============================
- 各技能模块用 `@skill_registry.register("xxx")` 装饰器注册，只要模块被 import 一次
  即完成注册；本模块集中 import 全部技能模块，避免到处散落 import；
- `bootstrap_skills()` 幂等（重复调用不会重复注册、不会抛异常）；
- 提供 `describe_skills()` / `list_tools()` / `get_skill()`，供
  `/system/skills` 清单接口与 Agent function-calling 使用；
- 应用启动处调用一次即可（core/application.py lifespan 或 ai/agent.py 初始化）。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False

EXPECTED_SKILLS: tuple[str, ...] = (
    "candidate_list",
    "knowledge_search",
    "matching_score",
    "position_search",
    "resume_dedupe",
    "resume_parse",
    "resume_skill_extract",
    "sql_query",
    "training_lag_scan",
    "training_plan_generate",
)


def bootstrap_skills() -> list[str]:
    """导入全部技能模块完成注册（幂等）。返回已注册技能名。"""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return skill_registry.keys()

    # 延迟导入：模块顶层只有 docstring 级依赖，避免启动时形成循环 import
    import app.ai.skills.knowledge_skill  # noqa: F401
    import app.ai.skills.matching_skill  # noqa: F401
    import app.ai.skills.resume_skill  # noqa: F401
    import app.ai.skills.sql_skill  # noqa: F401
    import app.ai.skills.training_skill  # noqa: F401

    _BOOTSTRAPPED = True
    names = skill_registry.keys()
    missing = [name for name in EXPECTED_SKILLS if name not in names]
    if missing:
        logger.warning("以下技能未注册：%s", missing)
    else:
        logger.info("Skills 注册完成：%s", ", ".join(names))
    return names


def describe_skills(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    """技能清单（名称 / 描述 / 参数 Schema / 依赖 / 权限），供管理台展示。"""
    from app.ai.skills.base import list_skills

    return [skill.describe() for skill in list_skills(enabled_only=enabled_only)]


def list_tools(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    """LLM function-calling 工具描述列表。"""
    from app.ai.skills.base import list_tools as _list_tools

    return _list_tools(enabled_only=enabled_only)


def get_skill(name: str):  # noqa: ANN201 - 返回 BaseSkill 实例
    """按名称取技能实例（未注册抛 NotFoundError）。"""
    from app.ai.skills.base import get_skill as _get_skill

    return _get_skill(name)


__all__ = [
    "EXPECTED_SKILLS",
    "bootstrap_skills",
    "describe_skills",
    "get_skill",
    "list_tools",
]
