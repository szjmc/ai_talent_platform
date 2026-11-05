"""人才档案业务服务：校验、事务内写入、响应组装与统计编排。

标签：[RAG组] [P0]
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, DuplicateError, NotFoundError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.core.security import mask_sensitive
from app.models.talent import Talent, TalentPortrait
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.talent_repository import TalentRepository
from app.schemas.talent import (
    TalentCreate,
    TalentDetailResponse,
    TalentPortraitResponse,
    TalentQuery,
    TalentResponse,
    TalentStatsResponse,
    TalentUpdate,
)

logger = logging.getLogger(__name__)


class TalentService:
    def __init__(
        self,
        db: AsyncSession,
        repo: TalentRepository | None = None,
        org_repo: OrganizationRepository | None = None,
    ) -> None:
        self.db = db
        self.repo = repo or TalentRepository()
        self.org_repo = org_repo or OrganizationRepository()

    async def page_list(self, page: PageParams, query: TalentQuery | None = None) -> PageResult[TalentResponse]:
        query = query or TalentQuery()
        if query.work_years_min is not None and query.work_years_max is not None and query.work_years_min > query.work_years_max:
            raise BusinessRuleError("工作年限下限不能大于上限")
        filters = {"education": query.education, "organization_id": query.organization_id, "status": query.status}
        extra_where = self.repo.skill_conditions([query.skill] if query.skill else [])
        if query.work_years_min is not None:
            extra_where.append(Talent.work_years >= query.work_years_min)
        if query.work_years_max is not None:
            extra_where.append(Talent.work_years <= query.work_years_max)
        result = await self.repo.paginate(self.db, page, filters=filters, keyword=query.keyword, extra_where=extra_where)
        result.items = [self._to_response(talent) for talent in result.items]
        return result

    async def get_detail(self, talent_id: int) -> TalentDetailResponse:
        talent = await self.repo.get_by_id_or_404(self.db, talent_id, message="人才档案不存在")
        portrait = await self.repo.get_latest_portrait(self.db, talent_id)
        return TalentDetailResponse(**self._to_response(talent).model_dump(), portrait=self._portrait_response(portrait))

    async def get_portrait(self, talent_id: int) -> TalentPortraitResponse:
        await self.repo.get_by_id_or_404(self.db, talent_id, message="人才档案不存在")
        portrait = await self.repo.get_latest_portrait(self.db, talent_id)
        if portrait is None:
            raise NotFoundError("该人才尚未生成画像")
        return self._portrait_response(portrait)

    async def create_talent(self, data: TalentCreate, *, operator: User | None = None) -> TalentResponse:
        await self._validate_organization(data.organization_id)
        if data.mobile and await self.repo.get_by_mobile(self.db, data.mobile):
            raise DuplicateError("该手机号已存在未删除的人才档案")
        payload = self._payload(data)
        payload.update(created_by=operator.id if operator else None, updated_by=operator.id if operator else None)
        talent = await self.repo.create(self.db, payload)
        logger.info("创建人才档案 id=%s operator=%s", talent.id, operator.id if operator else None)
        return self._to_response(talent, await self._organization_name(talent.organization_id))

    async def update_talent(
        self, talent_id: int, data: TalentUpdate, *, operator: User | None = None
    ) -> TalentResponse:
        talent = await self.repo.get_by_id_or_404(self.db, talent_id, message="人才档案不存在")
        payload = self._payload(data, exclude_unset=True)
        expected_version = payload.pop("version", None)
        self.repo.check_version(talent, expected_version)
        if not payload:
            return self._to_response(talent)
        if "organization_id" in payload:
            await self._validate_organization(payload["organization_id"])
        mobile = payload.get("mobile")
        if mobile and mobile != talent.mobile and await self.repo.get_by_mobile(self.db, mobile, exclude_id=talent_id):
            raise DuplicateError("该手机号已被其他人才档案使用")
        payload["updated_by"] = operator.id if operator else None
        await self.repo.update(self.db, talent, payload)
        logger.info("更新人才档案 id=%s fields=%s operator=%s", talent_id, list(payload), operator.id if operator else None)
        return self._to_response(talent, await self._organization_name(talent.organization_id))

    async def delete_talent(self, talent_id: int, *, operator: User | None = None) -> None:
        talent = await self.repo.get_by_id_or_404(self.db, talent_id, message="人才档案不存在")
        # mobile+is_deleted 唯一约束只允许一条删除记录；释放手机号以支持重复建档/删除周期。
        talent.mobile = None
        talent.updated_by = operator.id if operator else None
        await self.repo.delete(self.db, talent, soft=True)
        logger.info("逻辑删除人才档案 id=%s operator=%s", talent_id, operator.id if operator else None)

    async def get_stats(self, *, year: int | None = None) -> TalentStatsResponse:
        year = year or datetime.now().year
        monthly = {item["month"]: item["value"] for item in await self.repo.stats_by_month(self.db, year)}
        return TalentStatsResponse(
            total=await self.repo.count(self.db),
            by_organization=await self.repo.count_by_organization(self.db),
            by_education=await self.repo.stats_by_education(self.db),
            by_month=[{"month": month, "value": monthly.get(month, 0)} for month in range(1, 13)],
        )

    async def _validate_organization(self, organization_id: int | None) -> None:
        if organization_id and not await self.org_repo.get_by_id(self.db, organization_id):
            raise NotFoundError("所属组织不存在或已删除")

    async def _organization_name(self, organization_id: int | None) -> str | None:
        if not organization_id:
            return None
        organization = await self.org_repo.get_by_id(self.db, organization_id)
        return organization.name if organization else None

    @staticmethod
    def _payload(data: TalentCreate | TalentUpdate, *, exclude_unset: bool = False) -> dict[str, Any]:
        payload = data.model_dump(exclude_unset=exclude_unset)
        return {key: value.value if isinstance(value, Enum) else value for key, value in payload.items()}

    @staticmethod
    def _to_response(talent: Talent, organization_name: str | None = None) -> TalentResponse:
        organization = getattr(talent, "organization", None)
        if organization_name is None and organization and organization.id == talent.organization_id:
            organization_name = organization.name
        return TalentResponse(
            id=talent.id, name=talent.name, gender=talent.gender, birth_date=talent.birth_date, age=talent.age,
            mobile=mask_sensitive(talent.mobile) or None, email=talent.email,
            id_card=mask_sensitive(talent.id_card, 3, 4) or None,
            education=talent.education, school=talent.school, major=talent.major, work_years=talent.work_years,
            current_company=talent.current_company, current_position=talent.current_position,
            organization_id=talent.organization_id, organization_name=organization_name,
            skill_tags=talent.skill_tags or [], certificates=talent.certificates or [], self_evaluation=talent.self_evaluation,
            status=talent.status, potential_level=talent.potential_level, portrait_summary=talent.portrait_summary,
            source=talent.source, resume_id=talent.resume_id, version=talent.version,
            created_at=talent.created_at, updated_at=talent.updated_at,
        )

    @staticmethod
    def _portrait_response(portrait: TalentPortrait | None) -> TalentPortraitResponse | None:
        if portrait is None:
            return None
        return TalentPortraitResponse(
            id=portrait.id, talent_id=portrait.talent_id, version=portrait.version,
            dimensions=portrait.dimensions or {}, labels=portrait.labels or [], strengths=portrait.strengths or [],
            weaknesses=portrait.weaknesses or [], suggested_positions=portrait.suggested_positions or [],
            potential_level=portrait.potential_level, model_name=portrait.model_name,
            prompt_version=portrait.prompt_version, generated_at=portrait.generated_at, created_at=portrait.created_at,
        )
