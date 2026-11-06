"""
模块：app/ai/agent.py
职责：AI Agent 统一调度入口（对外唯一门面）。

标签：[架构组] [P0]

============================ 实现思路与代码要点 ============================
定位：
    routers/agent_router.py -> AgentService -> 本文件（调度器） -> 具体 Agent
    ai/agents/*_agent.py 各自实现 build_prompt / parse_output / persist。

调度逻辑：
    1. 读取 agent_type（assistant / resume / talent / matching / assessment / training）
    2. 从 patterns.registry.agent_registry 取出对应 Agent 类
    3. 构造 Agent(db=..., context=get_context())
    4. await agent.run(payload) -> dict
    5. 统一埋点：token 统计、耗时、审计日志

要点：
- 未注册的 agent_type 抛 NotFoundError 并列出可用 Agent；
- 单个 Agent 失败不影响其它任务；超时统一控制（settings.LLM_TIMEOUT）；
- 这里是「编排」，不要写具体的提示词与业务逻辑。
=========================================================================
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from app.ai.context import AIContext, get_context
from app.core.exceptions import AIError, NotFoundError
from app.patterns.registry import agent_registry

logger = logging.getLogger(__name__)

# 平台支持的 Agent 类型（与 routers/agent_router.py、Dify 编排保持一致）
AGENT_TYPES: tuple[str, ...] = (
    "assistant",
    "resume",
    "talent",
    "matching",
    "assessment",
    "training",
    "analytics",
)

# 类型 -> 模块路径（延迟导入，Agent 组未实现的模块不阻塞调度器启动）
AGENT_MODULES: dict[str, str] = {
    "assistant": "app.ai.agents.assistant_agent",
    "resume": "app.ai.agents.resume_agent",
    "talent": "app.ai.agents.talent_agent",
    "matching": "app.ai.agents.matching_agent",
    "assessment": "app.ai.agents.assessment_agent",
    "training": "app.ai.agents.training_agent",
    "analytics": "app.ai.agents.analytics_agent",
}

_BOOTSTRAPPED = False


def bootstrap_ai() -> dict[str, Any]:
    """启动引导：注册 Skills + 尽量注册已实现的 Agent（幂等）。"""
    global _BOOTSTRAPPED
    from app.ai.skills.registry_bootstrap import bootstrap_skills

    skills = bootstrap_skills()
    if not _BOOTSTRAPPED:
        for agent_type, module in AGENT_MODULES.items():
            if agent_type in agent_registry:
                continue
            try:
                __import__(module)
            except Exception as exc:  # noqa: BLE001 - Agent 组未实现时忽略
                logger.debug("Agent 模块未就绪：%s（%s）", module, exc)
        _BOOTSTRAPPED = True
    return {"skills": skills, "agents": agent_registry.keys()}


def list_agents() -> list[str]:
    """已注册的 Agent 类型。"""
    bootstrap_ai()
    return agent_registry.keys()


def _resolve(agent_type: str) -> Any:
    """解析 Agent 类：类型校验 -> 按需导入 -> 注册表取值。"""
    bootstrap_ai()
    agent_type = (agent_type or "").strip()
    if agent_type not in AGENT_TYPES:
        raise NotFoundError(f"不支持的 Agent 类型：{agent_type}，支持：{list(AGENT_TYPES)}")
    agent_cls = agent_registry.try_get(agent_type)
    if agent_cls is None:
        raise NotFoundError(
            f"Agent 未注册：{agent_type}，已注册：{agent_registry.keys()}",
        )
    return agent_cls


async def dispatch(
    agent_type: str,
    payload: dict[str, Any] | None = None,
    db: Any | None = None,
    user: Any | None = None,
    context: AIContext | None = None,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """派发一次 Agent 任务，返回 Agent 的结构化结果。"""
    import time

    from app.config.settings import settings

    agent_cls = _resolve(agent_type)
    ctx = context or get_context()
    agent = agent_cls(db=db, user=user, context=ctx)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            agent.run(payload or {}), timeout=timeout or settings.LLM_TIMEOUT
        )
    except asyncio.TimeoutError as exc:
        raise AIError(f"Agent 执行超时（{timeout or settings.LLM_TIMEOUT}s）") from exc
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    if isinstance(result, dict):
        result.setdefault("agent", agent_type)
        result.setdefault("elapsed_ms", elapsed)
    logger.info("Agent %s 执行完成：ok=%s 耗时=%sms", agent_type, result.get("ok"), elapsed)
    return result


async def dispatch_stream(
    agent_type: str,
    payload: dict[str, Any] | None = None,
    db: Any | None = None,
    user: Any | None = None,
    context: AIContext | None = None,
) -> AsyncIterator[str]:
    """流式派发（SSE）：逐段产出模型输出。"""
    agent_cls = _resolve(agent_type)
    ctx = context or get_context()
    agent = agent_cls(db=db, user=user, context=ctx)
    async for chunk in agent.stream(payload or {}):
        yield chunk


def ai_runtime_status() -> dict[str, Any]:
    """AI 运行时状态（供 /system/info 的 ai_ready 字段使用）。"""
    from app.ai.core.llm_manager import llm_manager
    from app.ai.skills.registry_bootstrap import describe_skills
    from app.config.settings import settings

    bootstrap_ai()
    return {
        "llm": llm_manager.status(),
        "skills": [item["name"] for item in describe_skills()],
        "agents": agent_registry.keys(),
        "milvus": {
            "enabled": settings.MILVUS_ENABLED,
            "uri": settings.MILVUS_URI if settings.MILVUS_ENABLED else "disabled",
        },
        "redis": {"enabled": settings.REDIS_ENABLED},
        "pgsql": {"enabled": settings.PGSQL_ENABLED},
    }


__all__ = [
    "AGENT_MODULES",
    "AGENT_TYPES",
    "ai_runtime_status",
    "bootstrap_ai",
    "dispatch",
    "dispatch_stream",
    "list_agents",
]
