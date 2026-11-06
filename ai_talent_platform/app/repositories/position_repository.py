"""
模块：app/repositories/position_repository.py
职责：岗位数据访问层。

标签：[Agent组] [P0]（list_open_by_org / search_by_title，被 position_search 技能依赖）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 岗位结构化要求存在 positions.requirements(JSON)，多版本能力模型存 position_requirements；
- 匹配只使用「已确认」的画像版本（确认标记见 PositionProfileService）；
- 岗位状态约束：closed 不参与匹配，paused 需要提示（由 Service 判定）。

* 实装说明（匹配 Agent）：
- 原文件为骨架（model 未指定），岗位读取与画像版本管理全部缺失；
- 本次只补匹配链路需要的读/写能力，不改表结构、不改既有字段语义。
=========================================================================
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position import Position, PositionRequirement
from app.repositories.base import BaseRepository


class PositionRepository(BaseRepository[Position]):
    """岗位仓库。"""

    model = Position

    # *kwq* 新增：白名单字段（岗位列表按部门/状态/层级筛选）。
    filterable_fields: set[str] = {"organization_id", "status", "level"}
    searchable_fields: set[str] = {"title", "code"}
    sortable_fields: set[str] = {"id", "title", "created_at", "updated_at", "publish_at"}
    default_order = "-id"

    # [岗位匹配接入] 反向匹配/预警批量读岗位；其他模块可复用，默认排除软删。
    async def get_by_ids(
        self, db: AsyncSession, position_ids: Sequence[int], *, include_deleted: bool = False
    ) -> list[Position]:
        """批量读取岗位：空ID不查库；默认排除软删除，状态判断交给业务层。"""
        # 步骤1：去重并限制到指定ID，避免误查全部岗位。
        ids = list(dict.fromkeys(int(pid) for pid in position_ids))
        if not ids:
            return []
        # 步骤2：复用公共查询的软删除约束，一次SQL完成批量读取。
        stmt = self._base_query(include_deleted=include_deleted).where(Position.id.in_(ids))
        return list((await db.execute(stmt)).scalars().all())

    # ---------------------------------------------------------------- 画像版本
    # *kwq* 新增：查询岗位最新画像版本。
    # 用途：匹配时必须把「当时用的版本号」写进结果，否则后续无法解释分数变化。
    async def latest_requirement(
        self, db: AsyncSession, position_id: int
    ) -> PositionRequirement | None:
        """取岗位最新一版能力模型（无版本记录返回 None）。"""
        stmt = (
            select(PositionRequirement)
            .where(PositionRequirement.position_id == position_id)
            .order_by(PositionRequirement.version.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # *kwq* 新增：按版本号取画像（指定版本复算历史结果时使用）。
    async def get_requirement(
        self, db: AsyncSession, position_id: int, version: int
    ) -> PositionRequirement | None:
        """取指定版本的岗位能力模型。"""
        stmt = (
            select(PositionRequirement)
            .where(
                PositionRequirement.position_id == position_id,
                PositionRequirement.version == version,
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # *kwq* 新增：计算下一个版本号。
    # 原因：版本号必须由数据库当前最大值推导（而不是内存计数），
    #      否则并发确认会写出重复版本（唯一键冲突抛 500）。
    async def next_requirement_version(self, db: AsyncSession, position_id: int) -> int:
        """返回该岗位下一个可用版本号（无记录时为 1）。"""
        stmt = select(func.max(PositionRequirement.version)).where(
            PositionRequirement.position_id == position_id
        )
        current = (await db.execute(stmt)).scalar()
        return int(current or 0) + 1

    # *kwq* 新增：列出岗位的全部画像版本（倒序，供审计与回滚）。
    async def list_requirements(
        self, db: AsyncSession, position_id: int, limit: int = 20
    ) -> Sequence[PositionRequirement]:
        """按版本倒序列出画像版本。"""
        stmt = (
            select(PositionRequirement)
            .where(PositionRequirement.position_id == position_id)
            .order_by(PositionRequirement.version.desc())
            .limit(max(1, int(limit)))
        )
        return list((await db.execute(stmt)).scalars().all())

    # *kwq* 新增：新增一版画像（只 flush，事务由 get_db 统一提交）。
    async def add_requirement(
        self,
        db: AsyncSession,
        *,
        position_id: int,
        version: int,
        dimensions: dict,
        weights: dict | None = None,
        source: str = "manual",
    ) -> PositionRequirement:
        """写入一版岗位能力模型。"""
        obj = PositionRequirement(
            position_id=position_id,
            version=version,
            dimensions=dimensions,
            weights=weights,
            source=source,
        )
        db.add(obj)
        await db.flush()
        return obj


__all__ = ["PositionRepository"]
