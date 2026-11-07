"""
模块：app/services/resume_ingest.py
职责：把「知识库上传原件」自动接到「人才档案链路」（revison -> 原件 -> 简历 -> 人才 -> 画像/向量）。

标签：[RAG组] [P0]（上传后自动入库的唯一调度点）

============================ 触发规则（团队约定） ============================
1. 只有 category == "resume" 的版本才触发人才处理；其它分类只走知识库。
2. 只在「原件 + 版本记录已经提交到 MySQL」之后触发一次：
     单份上传：路由里先提交草稿，再排后台任务；
     批量上传：每份文件草稿提交后各排一次。
   预览 / 发布 / 恢复 / 编辑都不触发（它们不产生新的原件）。
3. 身份只来自当前登录用户（user_id），后台任务用 user_id 从数据库重建 AIContext，
   不信任任何请求体里传入的角色或人才 ID。
4. 处理是幂等的：同一原件（同一内容哈希）重复上传只会复用已有简历与人才档案。

============================ 为什么不用持久化任务表 ==========================
人才链路的重试状态已经可以无损重建：
    - 简历解析状态在 resumes.status / parse_error；
    - 「待复核」在 resumes.parsed_data.duplicates；
    - 画像在 talent_portraits；
    - 向量在 Milvus 里按 talent_id 直接读回。
所以这里只排一次进程内后台任务，失败原因写进简历的 parsed_data.ingest 段，
重试走 POST /talents/{id}/index/refresh（只补缺失阶段），不再引入第三张状态表。
=========================================================================
"""

from __future__ import annotations

import logging

from app.core.exceptions import BusinessException
from app.core.permissions import PermissionEnum, has_permission

logger = logging.getLogger(__name__)

RESUME_CATEGORY = "resume"
# 版本状态为 failed 时原件仍已保存，解析失败属于知识库侧结论，人才侧可以自己再试一次；
# 但若原件本身没落库（没有 revision_id），则不该排任务。
SKIP_STATUSES = {"rejected"}


def ingest_roles(user) -> list[str]:
    """从认证用户取角色（不读请求体）；失败时按无角色处理，宁可跳过也不越权。"""
    try:
        roles = [str(code) for code in (getattr(user, "role_codes", None) or [])]
    except Exception:  # noqa: BLE001 - 角色关系未加载时不能把用户当成管理员
        roles = []
    if getattr(user, "is_superuser", False) and "super_admin" not in roles:
        roles.append("super_admin")
    return roles


def can_ingest(user) -> bool:
    """人才链路入口权限：简历解析（头像/向量创建权限在服务内按阶段校验）。"""
    return has_permission(ingest_roles(user), PermissionEnum.RESUME_PARSE)


def plan(result: dict | None, user) -> dict:
    """判断一次上传结果是否需要触发人才处理，并给出可返回前端的说明。

    只做判断，不写库、不提交事务 —— 调用方必须在自己提交成功后再调用本函数。
    """
    result = result or {}
    revision_id = result.get("revision_id")
    category = result.get("category")
    payload = {"category": category, "revision_id": revision_id, "scheduled": False}
    if category != RESUME_CATEGORY:
        payload["reason"] = "not_resume_category"
        return payload
    if not revision_id or result.get("status") in SKIP_STATUSES:
        payload["reason"] = "no_committed_revision"
        return payload
    if not can_ingest(user):
        # 例如只有知识库管理权限、没有简历解析权限的账号：原件仍然保存，只是不自动建模。
        payload["reason"] = "no_resume_parse_permission"
        return payload
    payload.update(scheduled=True, source_file_id=result.get("source_file_id"),
                   resume_ingest="queued", status=result.get("status"))
    return payload


async def run_revision_ingest(revision_id: int, user_id: int, trace_id: str | None = None) -> dict:
    """后台任务入口：独立会话 + 从 user_id 重建可信身份，然后执行人才链路。

    任何异常都在这里收敛为日志 + 失败结果：上传接口已经成功返回，不能让后台任务
    把用户已经拿到的响应变成错误。
    """
    from app.infrastructure.database.session import session_scope
    from app.services.talent_resume_service import ResumeService

    try:
        async with session_scope() as db:
            context = await load_context(db, user_id, trace_id)
            if context is None:
                logger.warning("简历自动入库跳过：用户不存在或已删除 user_id=%s revision_id=%s", user_id, revision_id)
                return {"revision_id": revision_id, "status": "skipped", "reason": "unknown_user"}
            service = ResumeService(db, context=context)
            outcome = await service.process_rag_revision(revision_id)
            logger.info(
                "简历自动入库完成 revision_id=%s resume_id=%s talent_id=%s status=%s reused=%s derived=%s",
                revision_id, outcome.get("resume_id"), outcome.get("talent_id"),
                outcome.get("processing_status"), outcome.get("reused"),
                (outcome.get("refresh") or {}).get("status"),
            )
            return {
                "revision_id": revision_id,
                "source_file_id": outcome.get("source_file_id"),
                "resume_id": outcome.get("resume_id"),
                "talent_id": outcome.get("talent_id"),
                "status": outcome.get("processing_status"),
                "reused": outcome.get("reused"),
                "refresh": outcome.get("refresh"),
            }
    except BusinessException as exc:
        logger.warning("简历自动入库被拒绝 revision_id=%s reason=%s", revision_id, exc.message)
        return {"revision_id": revision_id, "status": "failed", "message": exc.message}
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给已完成的请求
        logger.exception("简历自动入库失败 revision_id=%s type=%s", revision_id, type(exc).__name__)
        return {"revision_id": revision_id, "status": "failed",
                "message": "自动入库失败，请检查服务日志后重试"}


async def load_context(db, user_id: int, trace_id: str | None = None):
    """按 user_id 从数据库重建 AIContext（角色从库里的角色关系读取）。"""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.ai.context import AIContext
    from app.models.user import User

    stmt = select(User).options(selectinload(User.roles)).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None or getattr(user, "is_deleted", False):
        return None
    roles = ingest_roles(user)
    return AIContext(
        user_id=user.id,
        username=getattr(user, "username", None),
        roles=roles,
        organization_id=getattr(user, "organization_id", None),
        trace_id=trace_id or "-",
    )


__all__ = [
    "RESUME_CATEGORY",
    "can_ingest",
    "ingest_roles",
    "load_context",
    "plan",
    "run_revision_ingest",
]
