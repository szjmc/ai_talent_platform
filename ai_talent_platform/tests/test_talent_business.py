"""人才档案业务规则离线测试。

标签：[RAG组] [P0]

使用仓库替身，不依赖 MySQL；真实 SQL 的集成验证沿用 mysql_ready 夹具。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.core.exceptions import BusinessRuleError, DataConflictError, DuplicateError
from app.core.pagination import PageParams
from app.models.talent import Talent
from app.repositories.talent_repository import TalentRepository
from app.schemas.talent import TalentCreate, TalentQuery, TalentUpdate
from app.services.talent_service import TalentService


def make_talent(**overrides) -> Talent:
    values = dict(
        id=1, name="张明远", gender="male", birth_date=None, mobile="13800138000", email="zhang@example.com",
        id_card="330106199001011234", education="硕士", school="浙江大学", major="计算机", work_years=8,
        current_company="智远科技", current_position="算法工程师", organization_id=3, skill_tags=["Python"],
        certificates=[], self_evaluation=None, status="active", potential_level="A", portrait_summary=None,
        source="manual", resume_id=None, version=1, created_at=None, updated_at=None, organization=None,
    )
    values.update(overrides)
    return Talent(**values)


def make_repo(talent: Talent | None = None):
    repo = SimpleNamespace()
    repo.get_by_mobile = AsyncMock(return_value=None)
    repo.get_by_id_or_404 = AsyncMock(return_value=talent)
    repo.check_version = Mock(side_effect=lambda obj, expected: TalentRepository.check_version(repo, obj, expected))
    repo.skill_conditions = Mock(return_value=[])

    async def create(_db, payload):
        item = make_talent(**{key: value for key, value in payload.items() if key in make_talent().__table__.columns})
        item.id = 10
        item.version = 1
        return item

    async def update(_db, item, payload):
        for key, value in payload.items():
            setattr(item, key, value)
        item.version += 1
        return item

    async def delete(_db, item, soft=True):
        item.soft_delete()
        return True

    repo.create = AsyncMock(side_effect=create)
    repo.update = AsyncMock(side_effect=update)
    repo.delete = AsyncMock(side_effect=delete)
    return repo


def make_org_repo():
    return SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=3, name="技术研发部")))


@pytest.mark.asyncio
async def test_create_talent_applies_business_defaults_and_masks_sensitive_fields():
    repo = make_repo()
    service = TalentService(AsyncMock(), repo=repo, org_repo=make_org_repo())
    result = await service.create_talent(TalentCreate(name="张明远", mobile="13800138000", organization_id=3, skill_tags=["Python", "Python"]))
    assert result.id == 10
    assert result.mobile == "138****8000"
    assert result.id_card == "330***********1234"
    assert result.organization_name == "技术研发部"
    assert result.skill_tags == ["Python"]


@pytest.mark.asyncio
async def test_create_talent_rejects_duplicate_active_mobile():
    repo = make_repo()
    repo.get_by_mobile.return_value = make_talent()
    service = TalentService(AsyncMock(), repo=repo, org_repo=make_org_repo())
    with pytest.raises(DuplicateError, match="手机号"):
        await service.create_talent(TalentCreate(name="重复人才", mobile="13800138000"))
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_update_talent_enforces_optimistic_lock():
    talent = make_talent(version=3)
    repo = make_repo(talent)
    service = TalentService(AsyncMock(), repo=repo, org_repo=make_org_repo())
    with pytest.raises(DataConflictError):
        await service.update_talent(1, TalentUpdate(name="新姓名", version=2))
    repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_delete_is_soft_and_releases_mobile_for_future_unique_check():
    talent = make_talent()
    repo = make_repo(talent)
    service = TalentService(AsyncMock(), repo=repo, org_repo=make_org_repo())
    await service.delete_talent(1)
    assert talent.is_deleted is True
    assert talent.mobile is None
    repo.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_page_rejects_inverted_work_year_range_before_query():
    service = TalentService(AsyncMock(), repo=make_repo(), org_repo=make_org_repo())
    with pytest.raises(BusinessRuleError, match="工作年限"):
        await service.page_list(PageParams(), TalentQuery(work_years_min=10, work_years_max=2))


def test_talent_schema_rejects_unknown_fields_and_normalizes_tags():
    with pytest.raises(ValidationError):
        TalentCreate(name="张三", not_a_talent_field="禁止写入")
    data = TalentCreate(name=" 张三 ", skill_tags=[" Python ", "Python", "", "RAG"])
    assert data.name == "张三"
    assert data.skill_tags == ["Python", "RAG"]
