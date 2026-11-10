"""
模块：app/routers/router.py
职责：总路由注册（全平台唯一对外入口聚合点）。

标签：[架构组] [P1]

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
from app.core.response import ApiResponse, success
from app.routers.a_rag_router import router as a_rag_router  # [合并 lz] RAG 问答链路
from app.routers.agent_router import router as agent_router
from app.routers.ai_interview_router import router as ai_interview_router  # [AI 面试] 数字人面试官
from app.routers.analytics_router import router as analytics_router  # [架构组合并] 分析 MVP 真实路由
from app.routers.assessment_router import router as assessment_router  # [合并 origin/develop] 测评 Agent 落地
from app.routers.auth_router import router as auth_router
from app.routers.dify_router import router as dify_router
from app.routers.exam_router import router as exam_router  # 候选人笔试公开入口（HMAC 令牌免登录）
from app.routers.audit_router import router as audit_router  # [接口分组治理] 审计查询（替换 501 占位）
from app.routers.file_router import router as file_router  # [接口分组治理] 文件中心（替换 501 占位）
from app.routers.knowledge_router import router as knowledge_router  # [合并 lz] 知识库文档与检索
from app.routers.matching_router import router as matching_router
from app.routers.organization_router import router as organization_router
from app.routers.position_router import router as position_router
from app.routers.rag_router import router as rag_router  # [合并 lz] 向量检索
from app.routers.resume_router import router as resume_router  # [合并 lz] 简历解析
from app.routers.talent_router import router as talent_router
from app.routers.text2answer_router import router as text2answer_router
from app.routers.training_router import router as training_router
from app.routers.user_router import router as user_router
# 人才邀约（笔试 / 测评 / 面试间）：接口早已实现，但历史上只 import 没有 include_router，
# 导致 /api/v1/invites/* 全部 404，前端邀约弹窗、笔试页、面试间全部不可用。
from app.routers.invite_router import router as invite_router

logger = logging.getLogger(__name__)

api_router = APIRouter()

# ------------------------------------------------------------------ 已实现模块
# 注意：每个 router 只 include 一次。重复 include 不会报错，但会在 OpenAPI 里出现
# 同路径同方法的重复登记，排查「到底哪个版本生效」时非常费劲（合并时踩过）。
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(organization_router)
api_router.include_router(analytics_router)  # [架构组合并] 统计概览 / NL2SQL 问数（分析 MVP）
api_router.include_router(agent_router)
# AI 数字人面试：HR 创建/复盘 + 候选人 token 免登录作答
api_router.include_router(ai_interview_router)
# 培训管理：14 条路径 / 16 个方法，六节点全链路 + 站内信（见 routers/training_router.py）
api_router.include_router(training_router)
# *kwq* 新增（MATCH-010/011）：岗位画像与匹配接口从占位切换为真实实现。
# 修改原因：matching / positions 原为 placeholder（返回 501 提示），匹配功能无法被调用；
# 影响范围：仅这两个前缀的路由来源变化，路径与权限码保持不变，前端无需改调用地址。
# [岗位匹配接入] 注册岗位画像与匹配路由；合并时保留其他组路由。
api_router.include_router(position_router)
api_router.include_router(matching_router)
api_router.include_router(dify_router)
api_router.include_router(assessment_router)
api_router.include_router(exam_router)
api_router.include_router(knowledge_router)
api_router.include_router(resume_router)
api_router.include_router(text2answer_router)
api_router.include_router(a_rag_router)
api_router.include_router(rag_router)
api_router.include_router(talent_router)
# [接口分组治理] 之前漏挂 / 只挂占位的模块，此处补真实实现
api_router.include_router(invite_router)  # 人才邀约（前端 InvitePage / InterviewRoomPage 依赖）
api_router.include_router(file_router)    # 文件中心（原 /files 仅 501 占位）
api_router.include_router(audit_router)   # 审计查询（原 /audit 仅 501 占位）

# ------------------------------------------------------------------ 已实现模块清单
# 说明：启动日志与 /system/info 的 implemented_modules 共用本清单，避免两处各写一份、
#      上线后一处改了另一处没改（历史上出现过「已实现仍显示待实现」的误判）。
IMPLEMENTED_MODULES: list[str] = [
    "auth",
    "users",
    "organizations",
    "agents",
    "ai_interviews",
    "talents",
    "analytics",
    "knowledge",
    "resumes",
    "text2answer",
    "a_rag",
    "rag",
    "dify",
    # [岗位匹配接入] 合并 origin/develop 时补齐：以下三个模块本分支已是真实路由
    "positions",
    "matchings",
    "trainings",
    # [合并 origin/develop] 测评 Agent 上线后，assessments 由占位切换为真实路由
    "assessments",
    # [接口分组治理] 以下模块已有真实实现，之前遗漏在清单外（对外表现为「未实现」）
    "invites",   # 人才邀约：接口早已实现，但从未 include_router（/invites/* 一直 404）
    "files",     # 文件中心：file_router（原为 501 占位）
    "audit",     # 审计查询：audit_router（原为 501 占位）
]

# ------------------------------------------------------------------ 待实现模块
# 说明：这些路由目前只提供占位接口（返回 501 提示），待对应负责人实现后，
#      把下面两行替换为 real router 的 include，例如：
#          from app.routers.talent_router import router as talent_router
#          api_router.include_router(talent_router)
# 已实现模块必须同时从本清单移除：同前缀「501 占位 + 真实接口」并存会让联调方看到
# 自相矛盾的清单，且占位路由实际不可达（真实路由先注册）。test_router_stubs 会守这条。
# [接口分组治理] 本轮清空：清单内 6 个模块都已有真实路由（positions/assessments/
# trainings/matchings 早就是真实实现，files/audit 本轮补齐）。曾经「同前缀 501 占位 +
# 真实接口并存」，Swagger 里同一分组既有「（待实现）接口占位」又有真实接口，
# 联调方无法判断哪个可用 —— 这正是用户反馈的「接口分组混乱」的直接成因。
# 保留空清单与循环：后续新增未实现模块时按同样方式登记，_stub.py 与契约测试继续有效。
_STUB_MODULES: list[tuple[str, str, str]] = []

for _prefix, _tag, _desc in _STUB_MODULES:  # pragma: no cover - 清单为空时不会进入循环
    api_router.include_router(placeholder_router(prefix=_prefix, tag=_tag, description=_desc))

logger.info(
    "总路由注册完成：已实现 %s 个模块（%s）+ %s 个占位模块",
    len(IMPLEMENTED_MODULES),
    "/".join(IMPLEMENTED_MODULES),
    len(_STUB_MODULES),
)


system_router = APIRouter(prefix="/system", tags=["系统"])


@system_router.get("/info", summary="后端框架信息", response_model=ApiResponse[dict])
async def system_info():
    """返回当前后端框架装配情况，便于联调时快速确认公共层是否就绪。"""
    from app.config.settings import settings
    from app.infrastructure.database.session import check_all_connections
    from app.infrastructure.scheduler import get_sync_status
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
            # [架构组合并] 文件存储摘要 + 分析模块部分实现标注
            "storage": settings.storage_summary,
            # 分析库同步：是否启用 / 是否在跑 / 上次执行结果（架构组，合并 origin/develop）
            "analytics_sync": get_sync_status(),
            # [培训接入] 与启动日志同源：统一取模块级 IMPLEMENTED_MODULES，
            # 不再在这里手写一份（两份清单必然漂移，联调页会显示过期信息）。
            "implemented_modules": list(IMPLEMENTED_MODULES),
            "stub_modules": [prefix for prefix, _tag, _desc in _STUB_MODULES],
            "partial_modules": {"analytics": ["nl2sql"]},
        },
        message="OK",
    )


@system_router.get("/skills", summary="AI 技能清单", response_model=ApiResponse[list])
async def system_skills():
    """返回已注册的 Skills（名称 / 描述 / 参数 Schema / 依赖 / 所需权限）。"""
    from app.ai.skills.registry_bootstrap import describe_skills

    return success(data=describe_skills(), message="OK")


api_router.include_router(system_router)
