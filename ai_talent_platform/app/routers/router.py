"""
模块：app/routers/router.py
职责：总路由注册（全平台唯一对外入口聚合点）。

实现思路：
1. 每个业务模块一个 APIRouter（带自己的 prefix 和 tags）；
2. 在这里汇总成 api_router，再由 core/application.py 以 settings.API_PREFIX（/api/v1）挂载；
3. 已实现的模块直接 include_router；尚未实现的模块使用 routers/_stub.py 提供的
   placeholder_router，保证 Swagger 中能看到「待实现」的接口占位（不会 404 得莫名其妙）；
4. 新增模块时：写 xxx_router.py -> 在此 import -> include_router 一行，其它人无需改动。

最终接口路径示例：
    POST /api/v1/auth/login
    GET  /api/v1/users
    GET  /api/v1/organizations/tree
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.ai.agent import ai_runtime_status

from app.routers._stub import placeholder_router
from app.schemas.common import SuccessResponse
from app.core.response import success
from app.routers.auth_router import router as auth_router
from app.routers.organization_router import router as organization_router
from app.routers.user_router import router as user_router

logger = logging.getLogger(__name__)

api_router = APIRouter()

# ------------------------------------------------------------------ 已实现模块
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(organization_router)

# ------------------------------------------------------------------ 待实现模块
# 说明：这些路由目前只提供占位接口（返回 501 提示），待对应负责人实现后，
#      把下面两行替换为 real router 的 include，例如：
#          from app.routers.talent_router import router as talent_router
#          api_router.include_router(talent_router)
_STUB_MODULES: list[tuple[str, str, str]] = [
    ("positions", "岗位管理", "岗位 CRUD、岗位说明书 AI 解析、岗位向量化（见 routers/position_router.py）"),
    ("talents", "人才档案", "人才档案 CRUD、画像查询、语义检索、档案合并（见 routers/talent_router.py）"),
    ("resumes", "简历管理", "简历上传、AI 解析、解析结果查询（见 routers/resume_router.py）"),
    ("assessments", "人才测评", "测评创建、答题、提交、结果分析（见 routers/assessment_router.py）"),
    ("trainings", "培训管理", "课程、培训计划、学习进度（见 routers/training_router.py）"),
    ("matchings", "人岗匹配", "执行匹配、匹配结果、匹配解释（见 routers/matching_router.py）"),
    ("knowledge", "知识库", "知识文档上传、RAG 检索、智能问答（见 routers/knowledge_router.py）"),
    ("analytics", "数据分析", "统计概览、NL2SQL 问数、报告导出（见 routers/analytics_router.py）"),
    ("agents", "AI 智能体", "Agent 对话、任务执行、SSE 流式输出（见 routers/agent_router.py）"),
    ("files", "文件管理", "文件上传、下载、预览（见 routers/file_router.py）"),
]

for _prefix, _tag, _desc in _STUB_MODULES:
    api_router.include_router(placeholder_router(prefix=_prefix, tag=_tag, description=_desc))

logger.info("总路由注册完成：已实现 3 个模块 + %s 个占位模块", len(_STUB_MODULES))


system_router = APIRouter(prefix="/system", tags=["系统"])


@system_router.get("/info", summary="后端框架信息", response_model=SuccessResponse[dict])
async def system_info():
    """返回当前后端框架装配情况，便于联调时快速确认公共层是否就绪。"""
    from app.config.settings import settings
    from app.infrastructure.database.session import check_all_connections
    from app.models import Base
    from app.models.analytics import ANALYTICS_TABLE_NAMES

    return success(
        data={
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
            "api_prefix": settings.API_PREFIX,
            "databases": settings.database_summary(),
            "connection_status": await check_all_connections(),  # 含失败原因与修复建议
            "orm_tables": sorted(Base.metadata.tables.keys()),
            "pgsql_enabled": settings.PGSQL_ENABLED,
            "pgsql_schema": settings.PGSQL_SCHEMA,
            "pgsql_tables": list(ANALYTICS_TABLE_NAMES),
            "redis_enabled": settings.REDIS_ENABLED,
            "milvus_enabled": settings.MILVUS_ENABLED,
            # AI 运行时状态：LLM 适配器 / 已注册技能 / 已注册 Agent（架构组）
            "ai_ready": ai_runtime_status(),
            "implemented_modules": ["auth", "users", "organizations"],
        },
        message="OK",
    )


@system_router.get("/skills", summary="AI 技能清单", response_model=SuccessResponse[list])
async def system_skills():
    """返回已注册的 Skills（名称 / 描述 / 参数 Schema / 依赖 / 所需权限）。"""
    from app.ai.skills.registry_bootstrap import describe_skills

    return success(data=describe_skills(), message="OK")


api_router.include_router(system_router)
