"""
模块：app/services/position_service.py
职责：岗位管理业务服务（CRUD + 上下架 + 列表派生字段）。

标签：[Agent组] [P1]（岗位 CRUD：补齐前端岗位管理页依赖的接口）

为什么单独一个 Service：
    PositionProfileService 只负责「JD 解析 / 画像确认 / 版本查询」，
    岗位本身的增删改查长期没人实现，导致 /api/v1/positions 一直是 501 占位，
    前端岗位页只能提示「后端岗位 CRUD 接口尚未实现」。本服务补上这块。

实现要点：
1. 列表页需要「部门名称 + 已到岗人数」：
   - 部门名称用一次 IN 查询批取（不是逐行查 organizations）；
   - 已到岗人数用一次 GROUP BY 聚合，口径为
     `matching_reviews.review_status='hired'` 按 position_id 去重计数
     （人才档案上没有「入职岗位」字段，招聘漏斗的终点就是复核状态 hired，
     这是全库唯一可信的到岗事实来源；不用部门人数近似，避免给出编造数字）；
2. 编码唯一：不传 code 时自动生成 `POS-{id}`（先插入再回填，保证并发下仍唯一）；
3. 状态机：open ⇄ paused，closed 单向（closed 只能由管理员重新打开为 open，需显式传 status）；
4. 删除为逻辑删除；已有匹配记录的岗位不允许物理删除（保留历史可追溯）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.matching_review import MatchingReview
from app.models.organization import Organization
from app.models.position import Position
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.position_repository import PositionRepository
from app.schemas.position import (
    PositionCreate,
    PositionResponse,
    PositionStatusUpdate,
    PositionUpdate,
)

logger = logging.getLogger(__name__)

# 允许的状态流转（closed 是终态，只能显式重开为 open）
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"paused", "closed"},
    "paused": {"open", "closed"},
    "closed": {"open"},
}


class PositionService:
    """岗位业务服务。"""

    def __init__(
        self,
        db: AsyncSession,
        repo: PositionRepository | None = None,
        org_repo: OrganizationRepository | None = None,
    ) -> None:
        self.db = db
        self.repo = repo or PositionRepository()
        self.org_repo = org_repo or OrganizationRepository()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _to_response(
        position: Position,
        organization_name: str | None = None,
        headcount_filled: int = 0,
    ) -> PositionResponse:
        """ORM -> 视图（Decimal -> float；JSON 字段保证为可序列化类型）。"""
        return PositionResponse(
            id=position.id,
            title=position.title,
            code=position.code,
            organization_id=position.organization_id,
            organization_name=organization_name,
            level=position.level,
            headcount=position.headcount or 0,
            headcount_filled=headcount_filled,
            status=position.status,
            description=position.description,
            requirements=position.requirements,
            skill_tags=list(position.skill_tags or []),
            salary_min=float(position.salary_min) if position.salary_min is not None else None,
            salary_max=float(position.salary_max) if position.salary_max is not None else None,
            publish_at=position.publish_at,
            close_at=position.close_at,
            requirement_version=None,
            version=position.version or 0,
            created_at=position.created_at,
            updated_at=position.updated_at,
        )

    async def _org_names(self, org_ids: set[int]) -> dict[int, str]:
        """批量取部门名称（一次 IN 查询，避免逐行查询的 N+1）。"""
        ids = [oid for oid in org_ids if oid]
        if not ids:
            return {}
        stmt = select(Organization.id, Organization.name).where(Organization.id.in_(ids))
        return {oid: name for oid, name in (await self.db.execute(stmt)).all()}

    async def _latest_requirement_versions(self, position_ids: list[int]) -> dict[int, int]:
        """批量取每个岗位的最新画像版本（一次 GROUP BY，避免逐行查询）。"""
        if not position_ids:
            return {}
        from app.models.position import PositionRequirement

        stmt = (
            select(PositionRequirement.position_id, func.max(PositionRequirement.version))
            .where(PositionRequirement.position_id.in_(position_ids))
            .group_by(PositionRequirement.position_id)
        )
        return {pid: int(version) for pid, version in (await self.db.execute(stmt)).all()}

    async def _headcount_filled(self, position_ids: list[int]) -> dict[int, int]:
        """批量统计已到岗人数（一次 GROUP BY，口径：复核状态 hired 的人才去重计数）。"""
        if not position_ids:
            return {}
        stmt = (
            select(MatchingReview.position_id, func.count(distinct(MatchingReview.talent_id)))
            .where(
                MatchingReview.position_id.in_(position_ids),
                MatchingReview.review_status == "hired",
                MatchingReview.is_deleted.is_(False),
            )
            .group_by(MatchingReview.position_id)
        )
        return {pid: int(count) for pid, count in (await self.db.execute(stmt)).all()}

    async def _assert_org_exists(self, organization_id: int) -> None:
        org = await self.org_repo.get_by_id(self.db, organization_id)
        if org is None:
            raise ParamError(f"所属部门不存在（organization_id={organization_id}）")

    async def _generate_code(self, position: Position) -> str:
        """自动生成岗位编码（POS-{id}，唯一且可读；冲突时追加时长后缀）。"""
        code = f"POS-{position.id}"
        if await self.repo.exists(self.db, {"code": code}):
            code = f"POS-{position.id}-{datetime.now():%H%M%S}"
        return code

    # ---------------------------------------------------------------- 查询
    async def page_list(
        self,
        page: PageParams,
        *,
        keyword: str | None = None,
        organization_id: int | None = None,
        level: str | None = None,
        status: str | None = None,
    ) -> PageResult[PositionResponse]:
        """岗位分页列表（补部门名称 + 最新画像版本，两次批量查询）。"""
        result = await self.repo.paginate(
            self.db,
            page,
            filters={"organization_id": organization_id, "level": level, "status": status},
            keyword=keyword,
        )
        positions = list(result.items)
        if not positions:
            return PageResult[PositionResponse](items=[], total=0, page=page.page, page_size=page.page_size, pages=0)

        org_names = await self._org_names({p.organization_id for p in positions})
        versions = await self._latest_requirement_versions([p.id for p in positions])
        filled = await self._headcount_filled([p.id for p in positions])
        items = []
        for position in positions:
            item = self._to_response(
                position, org_names.get(position.organization_id), filled.get(position.id, 0)
            )
            item.requirement_version = versions.get(position.id)
            items.append(item)
        return PageResult[PositionResponse](
            items=items, total=result.total, page=result.page,
            page_size=result.page_size, pages=result.pages,
        )

    async def get(self, position_id: int) -> PositionResponse:
        """岗位详情。"""
        position = await self.repo.get_by_id(self.db, position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={position_id}）")
        names = await self._org_names({position.organization_id})
        versions = await self._latest_requirement_versions([position.id])
        filled = await self._headcount_filled([position.id])
        item = self._to_response(
            position, names.get(position.organization_id), filled.get(position.id, 0)
        )
        item.requirement_version = versions.get(position.id)
        return item

    # ---------------------------------------------------------------- 写入
    async def create(self, data: PositionCreate, *, user_id: int | None = None) -> PositionResponse:
        """新建岗位（编码不传则自动生成）。"""
        await self._assert_org_exists(data.organization_id)
        payload: dict[str, Any] = data.model_dump(exclude={"code"})
        payload["skill_tags"] = data.skill_tags
        payload["code"] = data.code or f"TMP-{datetime.now():%Y%m%d%H%M%S%f}"
        payload["publish_at"] = datetime.now() if data.status == "open" else None
        payload["created_by"] = user_id
        position = await self.repo.create(self.db, payload)
        if not data.code:
            position.code = await self._generate_code(position)
            await self.db.flush()
        return self._to_response(position, (await self._org_names({position.organization_id})).get(position.organization_id))

    async def update(
        self, position_id: int, data: PositionUpdate, *, user_id: int | None = None
    ) -> PositionResponse:
        """更新岗位（只更新传入字段；状态变更走状态机校验）。"""
        position = await self.repo.get_by_id(self.db, position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={position_id}）")
        payload = data.model_dump(exclude_unset=True)
        if not payload:
            raise BusinessRuleError("没有需要更新的字段")
        if "organization_id" in payload and payload["organization_id"]:
            await self._assert_org_exists(payload["organization_id"])
        if "status" in payload and payload["status"]:
            self._check_transition(position.status, payload["status"])
        if "salary_min" in payload or "salary_max" in payload:
            low = payload.get("salary_min", position.salary_min)
            high = payload.get("salary_max", position.salary_max)
            if low is not None and high is not None and float(low) > float(high):
                raise ParamError("薪资下限不能大于上限")
        if payload.get("status") == "open" and position.publish_at is None:
            payload["publish_at"] = datetime.now()
        if payload.get("status") == "closed":
            payload["close_at"] = datetime.now()
        payload["updated_by"] = user_id
        await self.repo.update(self.db, position, payload)
        names = await self._org_names({position.organization_id})
        return self._to_response(position, names.get(position.organization_id))

    @staticmethod
    def _check_transition(current: str, target: str) -> None:
        """状态机校验（防止 closed -> paused 之类的非法流转）。"""
        if current == target:
            return
        allowed = _ALLOWED_TRANSITIONS.get(current or "open", set())
        if target not in allowed:
            raise BusinessRuleError(f"岗位状态不允许从 {current} 变更为 {target}")

    async def set_status(
        self, position_id: int, data: PositionStatusUpdate, *, user_id: int | None = None
    ) -> PositionResponse:
        """上下架（独立接口，前端一键切换）。"""
        return await self.update(
            position_id,
            PositionUpdate(status=data.status),
            user_id=user_id,
        )

    async def delete(self, position_id: int, *, user_id: int | None = None) -> None:
        """逻辑删除岗位（有历史匹配记录的岗位仍保留行，避免历史数据成孤儿）。"""
        position = await self.repo.get_by_id(self.db, position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={position_id}）")
        position.updated_by = user_id
        await self.repo.delete(self.db, position, soft=True)
