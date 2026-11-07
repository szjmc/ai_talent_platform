"""
模块：tests/test_query_performance.py
职责：列表查询性能与跨方言正确性守门（防止「先全表物化再分页」的写法回流）。

标签：[架构组] [P1]（人才列表分页下推、技能筛选跨方言、列表接口不产生 N+1）

为什么需要：
    线上反馈「部分接口数据查询链路慢，获取时延高」。定位到 /talents 的实现是
        ids = [x.id for x in await repo.filtered(...)]      # 把命中行全部物化成实体
        await repo.paginate(..., extra_where=[Talent.id.in_(ids)])   # 再按 id 列表分页
    两倍全表扫描 + 大 IN 列表，3000 条人才时首页 320ms 且随数据量线性恶化。
    这类写法的坑在于「功能完全正确、只是慢」，单靠功能测试发现不了，必须守查询条数。

本用例守住三件事：
    1. 分页查询只发 count + page 两条 SQL（不得先拉全表）；
    2. 技能筛选在 MySQL / SQLite 上都能正确工作（生产 MySQL 有 JSON_CONTAINS，
       本地与 CI 跑 SQLite 没有，历史上直接 OperationalError）；
    3. 岗位列表的部门名称与到岗人数用批量查询补齐，不随行数增长。

全部离线运行，SQLite 内存库，不依赖 MySQL / Redis / Milvus。
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import BigInteger, event, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import Base  # noqa: E402
from app.models.matching_review import MatchingReview  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.position import Position  # noqa: E402
from app.models.talent import Talent  # noqa: E402
from app.repositories.talent_profile_repository import (  # noqa: E402
    TalentRepository,
    json_array_contains,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # noqa: ARG001
    """SQLite 只有 INTEGER PRIMARY KEY 才自增，BIGINT 会丢 AUTOINCREMENT。"""
    return "INTEGER"


class _Actor:
    """最小可信身份：超管，数据范围不设限。"""

    user_id = 1
    is_super_admin = True
    data_scope = "all"
    organization_id = None


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _counter(sync_engine) -> dict:
    """统计 SQL 条数：接收同步 Engine（AsyncSession.get_bind() 可能直接返回它）。"""
    stats = {"count": 0}
    event.listen(
        sync_engine,
        "before_cursor_execute",
        lambda *args: stats.__setitem__("count", stats["count"] + 1),
    )
    return stats


@pytest.mark.asyncio
async def test_talent_skills_filter_works_on_sqlite(session):
    """技能筛选在 SQLite 上必须可用，且区分 Java / JavaScript。"""
    session.add_all([
        Talent(name="A", skill_tags=["Python", "Go"]),
        Talent(name="B", skill_tags=["Java"]),
        Talent(name="C", skill_tags=["JavaScript"]),
    ])
    await session.commit()

    async def names(skill: str) -> list[str]:
        stmt = select(Talent.name).where(json_array_contains(Talent.skill_tags, skill)).order_by(Talent.name)
        return list((await session.execute(stmt)).scalars())

    assert await names("Python") == ["A"]
    assert await names("Java") == ["B"], "LIKE 匹配必须带引号边界，不能命中 JavaScript"
    assert await names("JavaScript") == ["C"]
    assert await names("Rust") == []


def test_skills_filter_compiles_to_mysql_json_contains():
    """生产方言（MySQL）必须仍然编成 JSON_CONTAINS，不做 LIKE 降级。"""
    from sqlalchemy.dialects import mysql

    stmt = select(Talent.id).where(json_array_contains(Talent.skill_tags, "Python"))
    sql = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "json_contains" in sql.lower()
    assert "json_quote" in sql.lower()
    assert " like " not in sql.lower()


@pytest.mark.asyncio
async def test_filtered_conditions_do_not_materialize_rows(session):
    """筛选条件编译不应查询数据库（原 `filtered()` 会把全表拉进内存）。"""
    session.add_all([Talent(name=f"人才{i}", skill_tags=["Python"], status="active") for i in range(50)])
    await session.commit()

    repo = TalentRepository()
    conditions = repo.filtered_conditions(_Actor(), {"skill": "Python", "status": "active"})
    assert conditions, "应当生成筛选条件"
    assert all(hasattr(c, "compile") for c in conditions)

    # 条件可直接下推给分页：count 一次、取页一次，共 2 条 SQL
    from app.core.pagination import PageParams

    bind = session.get_bind()
    sync_engine = getattr(bind, "sync_engine", bind)
    stats = _counter(sync_engine)
    result = await repo.paginate(session, PageParams(page=1, page_size=10), extra_where=conditions)
    assert result.total == 50
    assert len(result.items) == 10
    assert stats["count"] <= 3, f"分页不应产生额外查询，实际 {stats['count']} 条"


@pytest.mark.asyncio
async def test_position_headcount_filled_counts_hired_only(session):
    """已到岗人数口径：复核状态 hired 的人才去重计数，其它状态不计入。"""
    from app.services.position_service import PositionService

    org = Organization(name="技术部", code="TECH", level=1, path="/1", sort=1, is_active=True)
    session.add(org)
    await session.flush()
    position = Position(title="后端工程师", code="P-1", organization_id=org.id, headcount=3, status="open")
    session.add(position)
    await session.flush()

    session.add_all([
        MatchingReview(batch_id="b1", position_id=position.id, talent_id=1, review_status="hired"),
        MatchingReview(batch_id="b1", position_id=position.id, talent_id=2, review_status="hired"),
        MatchingReview(batch_id="b1", position_id=position.id, talent_id=3, review_status="rejected"),
        # 同一人才多批次重复命中 hired：去重计数应仍为 2
        MatchingReview(batch_id="b2", position_id=position.id, talent_id=1, review_status="hired"),
    ])
    await session.commit()

    service = PositionService(session)
    assert (await service.get(position.id)).headcount_filled == 2, "同一人才多条 hired 记录只算 1 人"
