"""
功能：保存和读取匹配明细及独立批次快照。
输入：数据库会话、匹配记录或查询ID。
输出：记录列表、快照或不存在结果。
流程：写入/查询明细 → 查询快照 → 兼容历史无快照记录。
边界：只查询及flush，不自行commit；不计算匹配业务规则。

标签：[Agent组] [P1]（匹配结果与批次仓储）
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matching import MatchingBatch, MatchingRecord
from app.repositories.base import BaseRepository


class MatchingRepository(BaseRepository[MatchingRecord]):
    """人岗匹配仓库。"""

    model = MatchingRecord

    filterable_fields: set[str] = {"position_id", "talent_id", "batch_id", "strategy"}
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "score", "matched_at", "created_at"}
    default_order = "-matched_at"

    async def save_snapshot(self, db: AsyncSession, *, response: dict, request_scope: dict) -> None:
        """
        功能：保存可回放的完整报告。
        输入：响应字典和请求范围。
        输出：无返回值；记录已flush。
        步骤：构造MatchingBatch → flush；提交由上层事务负责。
        """
        from datetime import datetime
        db.add(MatchingBatch(
            batch_id=response["batch_id"], position_id=response["position_id"],
            matched_at=datetime.fromisoformat(response["matched_at"]),
            response=response, request_scope=request_scope,
        ))
        await db.flush()

    async def snapshot_by_id(self, db: AsyncSession, batch_id: str) -> MatchingBatch | None:
        stmt = select(MatchingBatch).where(MatchingBatch.batch_id == batch_id,
                                          MatchingBatch.is_deleted.is_(False))
        return (await db.execute(stmt)).scalar_one_or_none()

    async def latest_snapshot(self, db: AsyncSession, position_id: int) -> MatchingBatch | None:
        """
        功能：读取岗位最新快照。
        输入：数据库会话和岗位ID。
        输出：快照或None。
        步骤：按时间及ID查询；存在更晚旧式明细时返回None交兼容路径处理。
        """
        stmt = select(MatchingBatch).where(
            MatchingBatch.position_id == position_id, MatchingBatch.is_deleted.is_(False)
        ).order_by(MatchingBatch.matched_at.desc(), MatchingBatch.id.desc()).limit(1)
        snapshot = (await db.execute(stmt)).scalar_one_or_none()
        if snapshot is None:
            return None
        newer = select(MatchingRecord.id).where(
            MatchingRecord.position_id == position_id,
            MatchingRecord.matched_at > snapshot.matched_at,
        ).limit(1)
        return None if (await db.execute(newer)).scalar_one_or_none() else snapshot

    # ---------------------------------------------------------------- 写入
    # *kwq* 新增：批量保存一批匹配结果。
    # 原因：一次跑批可能几十上百条，逐条 flush 会放大事务开销；
    #      统一交给 create_many（已处理唯一键冲突 -> DuplicateError，不抛 500）。
    async def save_batch(
        self, db: AsyncSession, records: Sequence[MatchingRecord]
    ) -> list[MatchingRecord]:
        """批量写入匹配记录（空列表直接返回）。"""
        if not records:
            return []
        return list(await self.create_many(db, list(records)))

    # ---------------------------------------------------------------- 查询
    # *kwq* 新增：按批次取明细（比分倒序，同分按人才 ID 升序保证稳定）。
    async def get_batch(
        self,
        db: AsyncSession,
        batch_id: str,
        *,
        position_id: int | None = None,
    ) -> list[MatchingRecord]:
        """读取批次全部记录。"""
        stmt = select(MatchingRecord).where(MatchingRecord.batch_id == batch_id)
        if position_id is not None:
            stmt = stmt.where(MatchingRecord.position_id == position_id)
        stmt = stmt.order_by(MatchingRecord.score.desc(), MatchingRecord.talent_id.asc())
        return list((await db.execute(stmt)).scalars().all())

    # *kwq* 新增：取某岗位最近一次跑批的 batch_id。
    # 注意：不能用 max(id) 推断 —— 历史数据回填/重算会打乱自增顺序，必须按 matched_at 排。
    async def latest_batch_id(self, db: AsyncSession, position_id: int) -> str | None:
        """返回该岗位最近一次跑批 ID（无记录返回 None）。"""
        stmt = (
            select(MatchingRecord.batch_id)
            .where(MatchingRecord.position_id == position_id)
            .order_by(MatchingRecord.matched_at.desc(), MatchingRecord.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # *kwq* 新增：取某岗位最近一次跑批的全部记录。
    async def latest_by_position(
        self, db: AsyncSession, position_id: int
    ) -> list[MatchingRecord]:
        """返回该岗位最新批次的明细（无批次返回空列表）。"""
        batch_id = await self.latest_batch_id(db, position_id)
        if not batch_id:
            return []
        return await self.get_batch(db, batch_id, position_id=position_id)

    # *kwq* 新增：按人才查询历史匹配记录。
    # 用途：HR 查看「这个人在哪些岗位上被评过、分数如何」，也是重算对比的入口。
    async def history_by_talent(
        self, db: AsyncSession, talent_id: int, limit: int = 50
    ) -> list[MatchingRecord]:
        """按人才查询历史批次记录（时间倒序）。"""
        stmt = (
            select(MatchingRecord)
            .where(MatchingRecord.talent_id == talent_id)
            .order_by(MatchingRecord.matched_at.desc(), MatchingRecord.id.desc())
            .limit(max(1, int(limit)))
        )
        return list((await db.execute(stmt)).scalars().all())

    # *kwq* 新增：取某岗位某人才在指定批次中的单条记录（解释接口复用已存证据）。
    async def get_record(
        self, db: AsyncSession, batch_id: str, position_id: int, talent_id: int
    ) -> MatchingRecord | None:
        """读取指定批次内的单条匹配记录。"""
        stmt = (
            select(MatchingRecord)
            .where(
                MatchingRecord.batch_id == batch_id,
                MatchingRecord.position_id == position_id,
                MatchingRecord.talent_id == talent_id,
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()


__all__ = ["MatchingRepository"]
