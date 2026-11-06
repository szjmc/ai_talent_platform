"""
模块：app/repositories/matching_review_repository.py
职责：HR 复核状态表与流水表的数据访问（只读写，不写业务规则）。

设计要点：
1. 乐观锁用「一条带 version 条件的 UPDATE」实现，而不是「先读后写」——
   先读后写存在竞态窗口：两个请求同时读到 version=1，都判断"没变"，然后
   都写成功，后者静默覆盖前者。带条件的 UPDATE 让数据库来裁定谁赢。
2. 状态表的唯一键 (batch_id, talent_id) 是幂等 upsert 的依据；
   `_flush_with_unique_guard` 已在 BaseRepository 处理唯一冲突，这里复用。
3. 漏斗统计用 GROUP BY 在库里算，不把全部记录拉到内存里数。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matching import MatchingBatch
from app.models.matching_review import MatchingReview, MatchingReviewEvent
from app.repositories.base import BaseRepository


class MatchingReviewRepository(BaseRepository[MatchingReview]):
    """【新建】matching_reviews 仓库。"""

    model = MatchingReview
    filterable_fields = {"batch_id", "position_id", "talent_id", "review_status", "reason_code"}
    sortable_fields = {"id", "created_at", "updated_at", "decided_at", "score_snapshot"}
    default_order = "-id"

    async def get_by_batch_talent(
        self, db: AsyncSession, *, batch_id: str, talent_id: int
    ) -> MatchingReview | None:
        """取批次内某人才的复核记录（幂等 upsert 的查重依据）。"""
        stmt = select(MatchingReview).where(
            MatchingReview.batch_id == batch_id,
            MatchingReview.talent_id == talent_id,
            MatchingReview.is_deleted.is_(False),
        )
        return (await db.execute(stmt)).scalars().first()

    async def list_by_batch_talents(
        self, db: AsyncSession, *, batch_id: str, talent_ids: list[int]
    ) -> list[MatchingReview]:
        """一次取回批次内多个候选人的复核记录（用于横向对比，避免 N+1）。

        为什么必须批量：对比最多 8 人，若逐人查询就是 8 次往返；
        而对比接口本身要求 <500ms，8 次往返在跨机房链路上足以吃掉一半预算。
        """
        if not talent_ids:
            return []
        stmt = select(MatchingReview).where(
            MatchingReview.batch_id == batch_id,
            MatchingReview.talent_id.in_([int(t) for t in talent_ids]),
            MatchingReview.is_deleted.is_(False),
        )
        return list((await db.execute(stmt)).scalars().all())

    async def update_with_version(
        self,
        db: AsyncSession,
        *,
        review_id: int,
        expected_version: int,
        values: dict[str, Any],
    ) -> bool:
        """带版本条件的原子更新；返回 False 表示版本已被他人改过（并发冲突）。

        注意：这里不 flush，由调用方在写完流水后统一 commit，保证
        「状态 + 流水」在同一事务里落地（要么都成功，要么都回滚）。
        """
        stmt = (
            update(MatchingReview)
            .where(
                MatchingReview.id == review_id,
                MatchingReview.version == expected_version,
                MatchingReview.is_deleted.is_(False),
            )
            .values(**values, version=MatchingReview.version + 1)
            # 关键：本项目 session 是 expire_on_commit=False，UPDATE 之后身份映射里的
            # 实例仍保留旧的 version。用 fetch 让 SQLAlchemy 把受影响对象标记过期，
            # 否则回读会拿到旧版本号，前端下次提交必然 409。
            .execution_options(synchronize_session="fetch")
        )
        result = await db.execute(stmt)
        return (result.rowcount or 0) > 0

    async def get_by_id_fresh(
        self, db: AsyncSession, review_id: int
    ) -> MatchingReview | None:
        """强制从库里重新加载（populate_existing），用于拿 UPDATE 后的最新 version。

        为什么不能直接用 BaseRepository.get_by_id：它会命中身份映射缓存，
        在 expire_on_commit=False 的配置下返回内存里的旧对象。
        """
        stmt = (
            select(MatchingReview)
            .where(MatchingReview.id == review_id, MatchingReview.is_deleted.is_(False))
            .execution_options(populate_existing=True)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def funnel_counts(self, db: AsyncSession, *, position_id: int) -> dict[str, int]:
        """按岗位统计各状态人数（库内 GROUP BY，不拉全量到内存）。"""
        stmt = (
            select(MatchingReview.review_status, func.count(MatchingReview.id))
            .where(
                MatchingReview.position_id == position_id,
                MatchingReview.is_deleted.is_(False),
            )
            .group_by(MatchingReview.review_status)
        )
        rows = (await db.execute(stmt)).all()
        return {str(status): int(count) for status, count in rows}

    async def talent_names_by_batch(
        self, db: AsyncSession, batch_ids: set[str]
    ) -> dict[tuple[str, int], str]:
        """批量补人才姓名（从匹配批次快照里读，一次查询解决整页）。

        为什么读快照而不是读 talents 表：
            1. 姓名属于「匹配当时看到的样子」，读快照与单条结果口径一致；
            2. 不越界访问其他模块维护的表。
        降级：批次过多时直接放弃补名（返回空），不能让一个展示字段拖慢整页。
        """
        if not batch_ids or len(batch_ids) > 20:
            return {}
        stmt = select(MatchingBatch.batch_id, MatchingBatch.response).where(
            MatchingBatch.batch_id.in_(list(batch_ids))
        )
        names: dict[tuple[str, int], str] = {}
        for batch_id, response in (await db.execute(stmt)).all():
            for item in (response or {}).get("items") or []:
                talent_id = item.get("talent_id")
                name = item.get("talent_name")
                if talent_id is not None and name:
                    names[(str(batch_id), int(talent_id))] = str(name)
        return names


class MatchingReviewEventRepository(BaseRepository[MatchingReviewEvent]):
    """【新建】matching_review_events 仓库（只插入与查询，不提供更新/删除）。"""

    model = MatchingReviewEvent
    filterable_fields = {"review_id", "batch_id", "position_id", "talent_id", "to_status"}
    sortable_fields = {"id", "created_at"}
    default_order = "-id"

    async def append(self, db: AsyncSession, event: MatchingReviewEvent) -> MatchingReviewEvent:
        """追加一条流水（append-only：本仓库刻意不提供 update / delete 方法）。"""
        db.add(event)
        await db.flush()
        return event

    async def list_by_review(
        self, db: AsyncSession, *, review_id: int, limit: int = 100
    ) -> list[MatchingReviewEvent]:
        """取某条复核的全部流水（按时间正序，便于前端画时间线）。"""
        stmt = (
            select(MatchingReviewEvent)
            .where(MatchingReviewEvent.review_id == review_id)
            .order_by(MatchingReviewEvent.id.asc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_by_position(
        self, db: AsyncSession, *, position_id: int, limit: int = 200
    ) -> list[MatchingReviewEvent]:
        """取某岗位的动作流水（按时间倒序，最近发生的在前）。"""
        stmt = (
            select(MatchingReviewEvent)
            .where(MatchingReviewEvent.position_id == position_id)
            .order_by(MatchingReviewEvent.id.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())


__all__ = ["MatchingReviewRepository", "MatchingReviewEventRepository"]
