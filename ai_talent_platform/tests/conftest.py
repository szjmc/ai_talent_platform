"""
模块：tests/conftest.py
职责：pytest 公共夹具（临时 SQLite 库 + 覆盖数据库依赖的 TestClient）。

实现思路：
1. 每个测试会话使用独立的临时 SQLite 文件，不污染开发库；
2. 通过 app.dependency_overrides[get_db] 把请求级 Session 指向测试引擎；
3. 建表 / 清表都在夹具中完成，保证用例之间互不影响；
4. 提供 client、db_session、auth_headers（登录后的鉴权头）等常用夹具。

运行：
    pytest -q                 # 全部用例
    pytest tests/integration  # 只跑集成测试
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# 在导入 app 之前把环境切到测试 SQLite，避免连到开发库
_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_talent_platform_test.db")
os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "true",
        "DB_DRIVER": "sqlite",
        "SQLITE_PATH": _TMP_DB,
        "LOG_LEVEL": "WARNING",
        "REDIS_ENABLED": "false",
        "MILVUS_ENABLED": "false",
        "JWT_SECRET_KEY": "test-secret-key-for-pytest-only-0123456789",
        "BCRYPT_ROUNDS": "4",  # 测试中降低哈希强度，加快执行
    }
)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.core.application import create_application  # noqa: E402
from app.infrastructure.database.session import get_db  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.core.permissions import RoleEnum  # noqa: E402

TEST_DATABASE_URL = f"sqlite+aiosqlite:///{_TMP_DB}"

app = create_application()

# 测试引擎（会话级）
engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """用测试 Session 替换生产依赖。"""
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session", autouse=True)
def prepare_database() -> Generator[None, None, None]:
    """建表 + 写入内置角色（会话级，执行一次）。"""
    import asyncio

    async def _setup() -> None:
        if os.path.exists(_TMP_DB):
            os.remove(_TMP_DB)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestingSessionLocal() as db:
            for role_enum in RoleEnum:
                db.add(Role(code=role_enum.value, name=role_enum.value, is_active=True))
            await db.commit()

    asyncio.run(_setup())
    yield
    asyncio.run(engine.dispose())
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """直接操作数据库的会话（用于准备 / 校验数据）。"""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端（ASGI 直连，无需起服务）。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def settings_override():
    """临时修改配置（自动还原）。"""
    original = {}
    for key in dir(settings):
        if key.isupper():
            original[key] = getattr(settings, key)

    def _apply(**kwargs):
        for k, v in kwargs.items():
            setattr(settings, k, v)

    yield _apply

    for k, v in original.items():
        setattr(settings, k, v)
