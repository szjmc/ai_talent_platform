"""在线监考 / 防作弊数据访问层（会话汇总 + 事件明细 + 可疑快照）。

标签：[Agent组] [P1]（防作弊监考 Repository：不提交事务，业务判断在 Service）

本层只负责「数据库怎么读写」：
- 不 commit（事务边界由 request 作用域或 Service 负责）；
- 不做风险打分 / 阈值判断（在 ai/proctoring/integrity.py 与 services/proctoring_service.py）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proctoring import ProctoringEvent, ProctoringSession, ProctoringSnapshot
from app.repositories.base import BaseRepository


class ProctoringSessionRepository(BaseRepository[ProctoringSession]):
    model = ProctoringSession
    filterable_fields = {"id", "subject_type", "subject_id", "subject_token", "risk_level", "candidate_name"}
    searchable_fields = {"candidate_name", "subject_token", "position_title"}
    sortable_fields = {"id", "created_at", "risk_score", "last_heartbeat_at", "started_at"}
    default_order = "-created_at"

    async def get_by_subject(
        self, db: AsyncSession, subject_type: str, subject_id: int
    ) -> ProctoringSession | None:
        """按对象查监考会话（一个面试 / 一份笔试卷只有一条）。"""
        stmt = (
            select(ProctoringSession)
            .where(
                ProctoringSession.subject_type == subject_type,
                ProctoringSession.subject_id == subject_id,
                ProctoringSession.is_deleted.is_(False),
            )
            .order_by(ProctoringSession.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_token(self, db: AsyncSession, token: str) -> ProctoringSession | None:
        """按候选人 token 查（公开接口只拿到 token）。"""
        if not token:
            return None
        stmt = (
            select(ProctoringSession)
            .where(ProctoringSession.subject_token == token, ProctoringSession.is_deleted.is_(False))
            .order_by(ProctoringSession.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()


class ProctoringEventRepository(BaseRepository[ProctoringEvent]):
    model = ProctoringEvent
    filterable_fields = {"id", "session_ref_id", "subject_type", "subject_id", "event_type", "source"}
    searchable_fields = {"event_type", "detail", "label"}
    sortable_fields = {"id", "created_at", "occurred_at", "weight"}
    default_order = "-id"

    async def list_by_session(
        self, db: AsyncSession, session_ref_id: int, *, limit: int = 200
    ) -> list[ProctoringEvent]:
        """按时间线倒序取会话事件（默认最多 200 条，供 HR 复盘）。"""
        stmt = (
            select(ProctoringEvent)
            .where(
                ProctoringEvent.session_ref_id == session_ref_id,
                ProctoringEvent.is_deleted.is_(False),
            )
            .order_by(ProctoringEvent.id.desc())
            .limit(max(1, limit))
        )
        return list((await db.execute(stmt)).scalars().all())

    async def count_by_session(self, db: AsyncSession, session_ref_id: int) -> int:
        return await self.count(db, filters={"session_ref_id": session_ref_id})


class ProctoringSnapshotRepository(BaseRepository[ProctoringSnapshot]):
    model = ProctoringSnapshot
    filterable_fields = {"id", "session_ref_id", "source", "trigger_type"}
    searchable_fields = {"trigger_type", "note"}
    sortable_fields = {"id", "created_at", "captured_at"}
    default_order = "-id"

    async def list_by_session(
        self, db: AsyncSession, session_ref_id: int, *, limit: int = 50
    ) -> list[ProctoringSnapshot]:
        stmt = (
            select(ProctoringSnapshot)
            .where(
                ProctoringSnapshot.session_ref_id == session_ref_id,
                ProctoringSnapshot.is_deleted.is_(False),
            )
            .order_by(ProctoringSnapshot.id.desc())
            .limit(max(1, limit))
        )
        return list((await db.execute(stmt)).scalars().all())

    async def count_by_session(self, db: AsyncSession, session_ref_id: int) -> int:
        return await self.count(db, filters={"session_ref_id": session_ref_id})


__all__ = [
    "ProctoringEventRepository",
    "ProctoringSessionRepository",
    "ProctoringSnapshotRepository",
]
