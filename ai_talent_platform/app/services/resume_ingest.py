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
5. 同一 revision 的并发触发要合并：批量上传 / 双击 / 两个请求撞在一起时，
   用进程内「进行中」集合 + 数据库行锁去重，避免同一份简历被并发解析两次
   （并发解析会重复调用模型、重复生成画像，并可能撞画像版本唯一键）。

============================ 漏触发怎么补 ============================
后台任务是进程内的：进程重启 / 任务被取消 / worker 崩溃时，这一次调度会丢失。
补法是「按数据状态补扫」，不新建任务表：
    resume_ingest.pending_revision_ids() 找出「简历分类原件已提交、但还没有成功/待复核
    入库记录」的版本；resume_ingest.sweep() 对它们重新调度一次。
    - 已在处理中的不重复调度（claim_revision 的幂等判断）；
    - 已成功/待复核的不会再次进入（只有失败或没跑过的才补）；
    - 由恢复 worker 周期调用，也可手动触发 POST /talents/ingest/sweep。

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

import asyncio
import logging

from app.core.exceptions import BusinessException
from app.core.permissions import PermissionEnum, has_permission

logger = logging.getLogger(__name__)

RESUME_CATEGORY = "resume"

# 进程内「正在处理」的 revision：后台任务是单进程内联执行的，跨进程并发靠下面的行锁。
_inflight: set[int] = set()
_inflight_lock = asyncio.Lock()
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

    async with _inflight_lock:
        if revision_id in _inflight:
            logger.info("简历自动入库跳过：同一版本正在处理 revision_id=%s", revision_id)
            return {"revision_id": revision_id, "status": "skipped", "reason": "in_flight"}
        _inflight.add(revision_id)
    try:
        async with session_scope() as db:
            context = await load_context(db, user_id, trace_id)
            if context is None:
                logger.warning("简历自动入库跳过：用户不存在或已删除 user_id=%s revision_id=%s", user_id, revision_id)
                return {"revision_id": revision_id, "status": "skipped", "reason": "unknown_user"}
            service = ResumeService(db, context=context)
            # 跨进程/多 worker 的重复触发：同一版本行加锁后再判断是否已经处理过。
            claimed = await service.claim_revision(revision_id)
            if not claimed:
                logger.info("简历自动入库跳过：该版本已处理过 revision_id=%s", revision_id)
                return {"revision_id": revision_id, "status": "skipped", "reason": "already_ingested"}
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
    finally:
        # 无论成功失败都要释放：失败后允许人工再次触发（原件已保存，重试是安全的）。
        async with _inflight_lock:
            _inflight.discard(revision_id)


async def pending_revision_ids(limit: int = 20) -> list[tuple[int, int]]:
    """列出「已提交但还没入库成功」的简历分类版本：[(revision_id, uploader_id)]。

    判定完全基于已有数据，不依赖任务表：
        knowledge_revisions.category = 'resume'
        且 source_file_id 非空、status 不是 rejected/archived
        且没有简历行的 parsed_data.ingest.revision_id 指向它、状态为 succeeded/needs_review
    """
    from sqlalchemy import select

    from app.infrastructure.database.session import session_scope
    from app.models.knowledge_revision import KnowledgeRevision

    async with session_scope() as db:
        rows = (await db.execute(
            select(KnowledgeRevision)
            .where(
                KnowledgeRevision.category == RESUME_CATEGORY,
                KnowledgeRevision.source_file_id.is_not(None),
                KnowledgeRevision.status.not_in(tuple(SKIP_STATUSES | {"archived"})),
                KnowledgeRevision.is_deleted.is_(False),
            )
            .order_by(KnowledgeRevision.id.desc())
            .limit(max(1, limit) * 5)
        )).scalars().all()
        candidates = [(row.id, row.created_by or 0) for row in rows]

    from app.services.talent_resume_service import ResumeService

    pending: list[tuple[int, int]] = []
    for revision_id, uploader_id in candidates:
        if not uploader_id:
            continue
        async with session_scope() as db:
            context = await load_context(db, uploader_id)
            if context is None or not can_ingest_ctx(context):
                continue
            try:
                claimable = await ResumeService(db, context=context).claim_revision(revision_id)
            except Exception as exc:  # noqa: BLE001 - 单个版本探测失败不影响其它版本
                logger.warning("补扫探测失败 revision_id=%s type=%s", revision_id, type(exc).__name__)
                continue
        if claimable:
            pending.append((revision_id, uploader_id))
        if len(pending) >= limit:
            break
    return pending


def can_ingest_ctx(context) -> bool:
    """AIContext 版权限判断（后台任务/补扫用，不信任任何请求体）。"""
    return has_permission(context.roles, PermissionEnum.RESUME_PARSE)


async def sweep(limit: int = 10) -> dict:
    """补扫漏掉的自动入库：按版本重新排队，已处理过的自动跳过（幂等）。"""
    try:
        pending = await pending_revision_ids(limit=limit)
    except Exception as exc:  # noqa: BLE001 - 恢复 worker 不能因为补扫失败而中断
        logger.warning("简历自动入库补扫失败 type=%s", type(exc).__name__)
        return {"scanned": 0, "scheduled": 0, "reason": "scan_failed"}
    results = []
    for revision_id, uploader_id in pending:
        results.append(await run_revision_ingest(revision_id, uploader_id, "sweep"))
    scheduled = sum(1 for item in results if item.get("status") not in ("skipped", "failed"))
    if scheduled:
        logger.info("简历自动入库补扫完成 scheduled=%s of %s", scheduled, len(pending))
    return {"scanned": len(pending), "scheduled": scheduled, "results": results}


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
    "can_ingest_ctx",
    "ingest_roles",
    "load_context",
    "pending_revision_ids",
    "plan",
    "run_revision_ingest",
    "sweep",
]
