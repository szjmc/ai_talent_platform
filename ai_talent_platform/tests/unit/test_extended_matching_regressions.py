"""
扩展匹配回归：真实公共仓库签名、软删除约束、筛选与接口序列化。

标签：[Agent组] [P1]（匹配回归用例）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.dialects import mysql
from app.repositories.position_repository import PositionRepository
from app.repositories.warning_repository import WarningRepository
from app.models.reserve_warning import ReserveWarning
from app.services.reserve_warning_service import ReserveWarningService
from app.core.pagination import PageParams

@pytest.mark.asyncio
async def test_position_batch_query_filters_deleted_and_empty_ids():
    db = SimpleNamespace(execute=AsyncMock())
    repo = PositionRepository()
    assert await repo.get_by_ids(db, []) == []
    db.execute.assert_not_awaited()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    await repo.get_by_ids(db, [1, 2])
    query = str(db.execute.await_args.args[0].compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "positions.id IN (1, 2)" in query and "positions.is_deleted IS false" in query

def test_warning_repository_filters_are_effective():
    repo = WarningRepository()
    assert repo._model() is ReserveWarning
    query = repo._apply_filters(repo._base_query(), {"position_id": 7, "resolved": False, "warning_type": "position_reserve_shortage"})
    sql = str(query.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "position_id = 7" in sql and "resolved = false" in sql and "position_reserve_shortage" in sql

@pytest.mark.asyncio
async def test_warning_page_uses_real_paginate_signature_and_maps_items():
    # 真实BaseRepository.paginate可拒绝不支持参数；空结果无需数据库。
    repo = WarningRepository()
    repo.count = AsyncMock(return_value=0)
    service = ReserveWarningService(None, warning_repo=repo)
    service._position_titles = AsyncMock(return_value={})
    page = PageParams(page=1, page_size=10, order_by=None, order="desc")
    result = await service.page_list(page, position_id=7, resolved=False)
    assert result.items == [] and result.total == 0
    assert repo.count.await_args.kwargs["filters"] == {"position_id": 7, "resolved": False}
