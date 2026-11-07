"""
文件：tests/integration/conftest.py
职责：匹配链路集成测试夹具（真实 MySQL，不使用 Mock 数据源）。

为什么要独立一套引擎：
- tests/conftest.py 的引擎是模块级单例（默认连接池），而它的建表夹具用 asyncio.run
  在独立事件循环里执行；pytest-asyncio 每个用例又是新的循环，
  池中连接跨循环复用会触发 aiomysql「Event loop is closed」（已实测复现）。
- 本文件用 NullPool（每次连接用完即关）彻底规避跨事件循环复用，
  只影响本目录的集成测试，不改动公共 conftest 与其他人的用例。

数据隔离：
- 每个用例使用唯一 org code / position code，互不影响；
- 所有写入真实 commit，保证 API 请求（另一会话）能读到数据。

标签：[架构组] [P2]（集成测试夹具，依赖真实数据库）
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.core.application import create_application
from app.core.permissions import RoleEnum
from app.infrastructure.database.session import get_db
from app.models import Base
from app.models.organization import Organization
from app.models.position import Position, PositionRequirement
from app.models.role import Role
from app.models.talent import Talent
from app.schemas.position import JobProfileConfirmRequest
from app.services.position_profile_service import PositionProfileService

# 独立引擎：NullPool 避免跨事件循环复用连接
engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# 独立 app 实例（与公共 conftest 的 app 互不影响）
app = create_application()


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """用测试会话替换生产依赖（事务边界与生产一致）。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session", autouse=True)
def prepare_db():
    """建库 -> 建表（先删后建）-> 写入内置角色。"""

    async def _setup() -> None:
        # 1) 建库（SQLite 单文件模式无需建库）
        if not settings.is_sqlite:
            server = create_async_engine(settings.MYSQL_SERVER_URL, poolclass=NullPool)
            try:
                async with server.begin() as conn:
                    await conn.execute(
                        text(
                            f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                            f"DEFAULT CHARACTER SET {settings.DB_CHARSET}"
                        )
                    )
            finally:
                await server.dispose()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        async with SessionLocal() as db:
            for role_enum in RoleEnum:
                db.add(Role(code=role_enum.value, name=role_enum.value, is_active=True))
            await db.commit()

    asyncio.run(_setup())
    yield
    asyncio.run(engine.dispose())


@pytest.fixture
async def db_session(prepare_db) -> AsyncGenerator[AsyncSession, None]:
    """直接操作数据库的会话（用于准备 / 校验数据）。"""
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def client(prepare_db) -> AsyncGenerator[Any, None]:
    """异步 HTTP 客户端（ASGI 直连，真实走中间件、依赖与异常处理）。"""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ------------------------------------------------------------------ 种子数据
PROFILE: dict[str, Any] = {
    "job_title": "后端开发工程师",
    "responsibilities": ["负责招聘平台后端服务开发", "参与接口设计与性能优化"],
    "required_conditions": [{"text": "统招本科及以上学历"}, {"text": "3 年以上后端开发经验"}],
    "required_skills": ["Python", "FastAPI", "MySQL"],
    "preferred_skills": ["Redis"],
    "min_education": "本科",
    "min_work_years": 3,
    "source": "manual",
    "prompt_version": "manual-v1",
}

TALENTS: list[tuple[str, str | None, int, list[str] | None, str | None]] = [
    ("张伟", "本科", 5, ["Python", "FastAPI", "MySQL"], "A"),
    ("李娜", "硕士", 4, ["Python", "Django"], "B"),
    ("王强", None, 0, None, None),
    ("赵敏", "大专", 2, ["Python", "FastAPI"], "C"),
    ("陈晨", "本科", 6, ["Python", "FastAPI", "MySQL", "Redis"], "B"),
]


@dataclass
class SeedData:
    """集成测试种子数据句柄。"""

    org_id: int
    position_id: int
    position_code: str
    talent_ids: dict[str, int]
    profile_version: int


async def _create_position(db: AsyncSession, org_id: int, title: str = "后端开发工程师") -> Position:
    position = Position(
        title=title,
        code=f"P-{uuid.uuid4().hex[:10]}",
        organization_id=org_id,
        requirements={"weights": {"skill": 0.4, "experience": 0.3, "education": 0.2, "quality": 0.1}},
    )
    db.add(position)
    await db.flush()
    return position


@pytest.fixture
async def seed_data(db_session) -> SeedData:
    """创建「组织 + 已确认画像的岗位 + 5 位候选人」。"""
    org = Organization(code=f"ORG-{uuid.uuid4().hex[:8]}", name="研发部", level=1, path="/")
    db_session.add(org)
    await db_session.flush()

    position = await _create_position(db_session, org.id)
    view = await PositionProfileService(db_session).confirm(
        position.id,
        JobProfileConfirmRequest(profile=PROFILE, note="集成测试画像"),
        user_id=1,
    )

    talent_ids: dict[str, int] = {}
    for name, education, years, skills, potential in TALENTS:
        talent = Talent(
            name=name,
            education=education,
            work_years=years,
            skill_tags=skills,
            potential_level=potential,
            status="active",
        )
        db_session.add(talent)
        await db_session.flush()
        talent_ids[name] = talent.id

    await db_session.commit()
    return SeedData(
        org_id=org.id,
        position_id=position.id,
        position_code=position.code,
        talent_ids=talent_ids,
        profile_version=view.version,
    )


@pytest.fixture
async def unconfirmed_position(db_session, seed_data) -> int:
    """创建一个「有版本记录但未确认」的岗位（验证未确认画像不得匹配）。"""
    position = await _create_position(db_session, seed_data.org_id, title="未确认岗位")
    db_session.add(
        PositionRequirement(
            position_id=position.id,
            version=1,
            dimensions={**PROFILE, "_meta": {"confirmed": False}},
            source="manual",
        )
    )
    await db_session.commit()
    return position.id
