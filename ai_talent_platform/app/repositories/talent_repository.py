"""人才档案数据访问层。

标签：[RAG组] [P0]

所有语句使用 SQLAlchemy 参数绑定；JSON 技能条件由 MySQL JSON_CONTAINS 执行。
本层不提交事务，也不做手机号查重等业务判断。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.talent import Talent, TalentPortrait
from app.repositories.base import BaseRepository


class TalentRepository(BaseRepository[Talent]):
    model = Talent
    filterable_fields = {"id", "mobile", "education", "status", "organization_id", "potential_level"}
    searchable_fields = {"name", "mobile", "email", "current_company", "current_position", "school", "major"}
    sortable_fields = {"id", "created_at", "updated_at", "name", "work_years"}
    default_order = "-id"

    async def get_by_mobile(
        self, db: AsyncSession, mobile: str, *, exclude_id: int | None = None
    ) -> Talent | None:
        stmt = select(Talent).where(Talent.mobile == mobile, Talent.is_deleted.is_(False))
        if exclude_id is not None:
            stmt = stmt.where(Talent.id != exclude_id)
        return (await db.execute(stmt.limit(1))).scalar_one_or_none()

    async def get_by_ids(self, db: AsyncSession, ids: Sequence[int]) -> list[Talent]:
        """向量召回后的批量回表；调用方负责按召回得分重排。"""
        if not ids:
            return []
        stmt = select(Talent).where(Talent.id.in_(list(ids)), Talent.is_deleted.is_(False))
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    def skill_conditions(skills: Sequence[str] | None) -> list[Any]:
        """每个技能都必须包含于 JSON 数组（AND），JSON 文本通过参数绑定传输。"""
        return [func.json_contains(Talent.skill_tags, json.dumps(skill, ensure_ascii=False)) for skill in skills or [] if skill]

    async def search_by_skills(self, db: AsyncSession, skills: Sequence[str]) -> list[Talent]:
        return list(await self.list(db, extra_where=self.skill_conditions(skills)))

    async def get_latest_portrait(self, db: AsyncSession, talent_id: int) -> TalentPortrait | None:
        stmt = (
            select(TalentPortrait)
            .where(TalentPortrait.talent_id == talent_id, TalentPortrait.is_deleted.is_(False))
            .order_by(TalentPortrait.version.desc(), TalentPortrait.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def count_by_organization(
        self, db: AsyncSession, org_ids: Sequence[int] | None = None
    ) -> list[dict[str, int | None]]:
        stmt = select(Talent.organization_id, func.count(Talent.id).label("value")).where(Talent.is_deleted.is_(False))
        if org_ids:
            stmt = stmt.where(Talent.organization_id.in_(list(org_ids)))
        rows = (await db.execute(stmt.group_by(Talent.organization_id).order_by(func.count(Talent.id).desc()))).all()
        return [{"organization_id": org_id, "value": int(value)} for org_id, value in rows]

    async def stats_by_education(self, db: AsyncSession) -> list[dict[str, str | int | None]]:
        stmt = (
            select(Talent.education, func.count(Talent.id).label("value"))
            .where(Talent.is_deleted.is_(False))
            .group_by(Talent.education)
            .order_by(func.count(Talent.id).desc())
        )
        return [{"education": education, "value": int(value)} for education, value in (await db.execute(stmt)).all()]

    async def stats_by_month(self, db: AsyncSession, year: int) -> list[dict[str, int]]:
        stmt = (
            select(func.month(Talent.created_at).label("month"), func.count(Talent.id).label("value"))
            .where(Talent.is_deleted.is_(False), func.year(Talent.created_at) == year)
            .group_by(func.month(Talent.created_at))
            .order_by(func.month(Talent.created_at))
        )
        return [{"month": int(month), "value": int(value)} for month, value in (await db.execute(stmt)).all()]
