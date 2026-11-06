"""人才邀约 / 笔试会话数据访问层。

标签：[Agent组] [P1]

本层不提交事务，不做过期/题库为空等业务判断（归 service）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_random_token
from app.models.invitation import ExamSession, TalentInvitation
from app.repositories.base import BaseRepository


class TalentInvitationRepository(BaseRepository[TalentInvitation]):
    model = TalentInvitation
    filterable_fields = {"id", "talent_id", "type", "status", "created_by"}
    searchable_fields = {"token", "title"}
    sortable_fields = {"id", "created_at", "interview_time", "status"}
    default_order = "-created_at"

    async def get_by_token(self, db: AsyncSession, token: str) -> TalentInvitation | None:
        """公开接口按 token 直查（token 本身即密钥，不做软删过滤）。"""
        if not token:
            return None
        stmt = select(TalentInvitation).where(TalentInvitation.token == token).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def mint_token(self, db: AsyncSession, *, length: int = 24) -> str:
        """生成唯一 token（冲突重试；概率极低）。"""
        for _ in range(6):
            token = generate_random_token(length)
            exists = await self.exists(db, filters={"token": token})
            if not exists:
                return token
        raise RuntimeError("邀约 token 生成冲突（重试耗尽）")  # pragma: no cover


class ExamSessionRepository(BaseRepository[ExamSession]):
    model = ExamSession
    filterable_fields = {"id", "invitation_id", "token", "status"}
    searchable_fields = {"token"}
    sortable_fields = {"id", "started_at", "submitted_at", "status"}
    default_order = "-id"

    async def get_by_token(self, db: AsyncSession, token: str) -> ExamSession | None:
        if not token:
            return None
        stmt = select(ExamSession).where(ExamSession.token == token).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_invitation(self, db: AsyncSession, invitation_id: int) -> ExamSession | None:
        stmt = select(ExamSession).where(ExamSession.invitation_id == invitation_id).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()
