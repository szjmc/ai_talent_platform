"""
模块：app/repositories/ai_interview_repository.py
职责：AI 数字人面试会话 / 逐轮问答的数据访问层。

标签：[Agent组] [P1]（AI 面试 Repository）

约束：
- 本层不 commit、不判断状态机、不做评分（业务在 AiInterviewService）；
- token 查重必须直查 token 列：BaseRepository.exists(filters=...) 会对未列入
  filterable_fields 的字段静默丢弃，历史 bug 见 invitation_repository.mint_token；
- 公开接口与 host 接口共用同一张表，软删除过滤仍由 BaseRepository 统一处理。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_random_token
from app.models.ai_interview import AiInterviewSession, AiInterviewTurn
from app.repositories.base import BaseRepository


class AiInterviewSessionRepository(BaseRepository[AiInterviewSession]):
    """AI 面试会话仓库。"""

    model = AiInterviewSession
    filterable_fields = {
        "id",
        "talent_id",
        "position_id",
        "invitation_id",
        "assessment_id",
        "status",
        "created_by",
    }
    searchable_fields = {"candidate_name", "position_title", "title"}
    sortable_fields = {
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "total_score",
        "status",
    }
    default_order = "-created_at"

    async def get_by_token(self, db: AsyncSession, token: str) -> AiInterviewSession | None:
        """公开接口按 token 直查（不做软删过滤：token 本身即密钥）。"""
        if not token:
            return None
        stmt = select(AiInterviewSession).where(AiInterviewSession.token == token).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_token_for_update(self, db: AsyncSession, token: str) -> AiInterviewSession | None:
        """公开作答时加行锁，避免重复点击 / 并发请求造成同一题被记录两次。"""
        if not token:
            return None
        stmt = (
            select(AiInterviewSession)
            .where(AiInterviewSession.token == token)
            .with_for_update()
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def mint_token(self, db: AsyncSession, *, length: int = 24) -> str:
        """生成唯一 token（重试 6 次）。"""
        for _ in range(6):
            token = generate_random_token(length)
            stmt = select(AiInterviewSession.id).where(AiInterviewSession.token == token).limit(1)
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                return token
        raise RuntimeError("AI 面试 token 生成冲突（重试耗尽）")


class AiInterviewTurnRepository(BaseRepository[AiInterviewTurn]):
    """AI 面试逐轮问答仓库。"""

    model = AiInterviewTurn
    filterable_fields = {"id", "session_id", "role", "kind", "question_index"}
    searchable_fields = set()
    sortable_fields = {"id", "seq", "created_at"}
    default_order = "seq"

    async def list_by_session(self, db: AsyncSession, session_id: int) -> list[AiInterviewTurn]:
        """按 seq 升序返回整场对话。"""
        stmt = (
            select(AiInterviewTurn)
            .where(AiInterviewTurn.session_id == session_id)
            .order_by(AiInterviewTurn.seq.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def next_seq(self, db: AsyncSession, session_id: int) -> int:
        """会话内下一个顺序号（从 1 开始）。"""
        stmt = select(func.max(AiInterviewTurn.seq)).where(AiInterviewTurn.session_id == session_id)
        current = (await db.execute(stmt)).scalar_one_or_none()
        return int(current or 0) + 1


__all__ = ["AiInterviewSessionRepository", "AiInterviewTurnRepository"]
