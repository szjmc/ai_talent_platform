"""
模块：tests/conftest.py
职责：pytest 公共夹具（MySQL 测试库 + 覆盖数据库依赖的 AsyncClient）。

标签：[架构组] [P0]（全部测试的公共夹具：MySQL 测试库 + AsyncClient）

数据库说明（平台不使用 SQLite）：
- 测试与开发一致使用 **MySQL**，默认覆盖到独立测试库 `ai_talent_platform_test`，
  避免污染开发库；库不存在时自动创建（需要 CREATE DATABASE 权限）；
- 连接参数来源优先级：环境变量 > .env > 默认值；
  可用 TEST_DB_NAME / TEST_DB_HOST 等覆盖，CI 中直接注入环境变量即可；
- 每个测试会话建表一次，用例之间通过唯一用户名 / 编码隔离，互不影响。

运行：
    pytest -q                      # 需要本机 MySQL 可用
    TEST_DB_NAME=mytest pytest -q   # 指定测试库
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# 在导入 app 之前把环境切到测试配置（MySQL 测试库）
os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "true",
        "DB_DRIVER": "mysql",
        "DB_NAME": os.getenv("TEST_DB_NAME", "ai_talent_platform_test"),
        "LOG_LEVEL": "WARNING",
        "PGSQL_ENABLED": "false",      # 单元/集成测试不依赖分析库
        "REDIS_ENABLED": "false",
        "MILVUS_ENABLED": "false",
        "JWT_SECRET_KEY": "test-secret-key-for-pytest-only-0123456789",
        "BCRYPT_ROUNDS": "4",          # 测试中降低哈希强度，加快执行
    }
)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.core.application import create_application  # noqa: E402
from app.core.permissions import RoleEnum  # noqa: E402
from app.infrastructure.database.session import get_db  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.role import Role  # noqa: E402

TEST_DATABASE_URL = settings.DATABASE_URL

app = create_application()

# 测试引擎
engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"charset": settings.DB_CHARSET, "autocommit": False},
)
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


@pytest.fixture(scope="session")
def mysql_ready() -> Generator[None, None, None]:
    """确保 MySQL 测试库存在 -> 建表 -> 写入内置角色（会话级，执行一次）。

    非 autouse：只有「需要数据库」的测试显式依赖它（或在模块内声明 autouse），
    这样不依赖数据库的健康检查类用例在没装 MySQL 的环境也能运行。
    MySQL 不可用时 skip 并打印连接目标与原始错误，避免误判为代码失败。
    """
    import asyncio

    async def _setup() -> None:
        # 1) 确保测试库存在（连服务器级 DSN）
        server_engine = create_async_engine(settings.MYSQL_SERVER_URL, pool_pre_ping=True)
        try:
            async with server_engine.begin() as conn:
                await conn.execute(
                    text(
                        f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                        f"DEFAULT CHARACTER SET {settings.DB_CHARSET} "
                        f"COLLATE {settings.DB_CHARSET}_general_ci"
                    )
                )
        finally:
            await server_engine.dispose()

        # 2) 建表（先删后建，保证结构最新）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        # 3) 内置角色
        async with TestingSessionLocal() as db:
            for role_enum in RoleEnum:
                db.add(Role(code=role_enum.value, name=role_enum.value, is_active=True))
            await db.commit()

    try:
        asyncio.run(_setup())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            "MySQL 测试库不可用，跳过集成测试。\n"
            f"  连接目标：{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}\n"
            f"  错误信息：{exc}\n"
            "  请先启动 MySQL 并确认 .env 中 DB_* 配置（测试库可用 TEST_DB_NAME 覆盖）。",
            allow_module_level=True,
        )
    yield
    asyncio.run(engine.dispose())


@pytest_asyncio.fixture
async def db_session(mysql_ready) -> AsyncGenerator[AsyncSession, None]:
    """直接操作数据库的会话（用于准备 / 校验数据）。"""
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[object, None]:
    """异步 HTTP 客户端（ASGI 直连，无需起服务；不要求数据库可用）。"""
    from httpx import ASGITransport, AsyncClient

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
