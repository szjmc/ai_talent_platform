"""
脚本：scripts/seed_data.py
职责：初始化演示数据（部门树 + 各角色测试账号）。

标签：[架构组] [P1]（部门树 + 各角色测试账号，演示前置）

使用方式：
    python scripts/seed_data.py

生成内容：
    部门：总公司 -> (人力资源部 / 技术研发部 -> (算法组 / 后端组) / 市场运营部)
    账号（密码统一 Test@12345）：
        admin    超级管理员
        hr01     人力资源
        mgr01    部门管理者（技术研发部）
        emp01    普通员工
幂等：已存在同名部门 / 用户名则跳过。
注意：仅用于开发与演示环境，切勿在生产执行。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.role import Role  # noqa: E402,F401
from app.models.user import User  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

PASSWORD = "Test@12345"

ORG_TREE = [
    {"code": "HQ", "name": "总公司", "parent": None, "sort": 1},
    {"code": "HR", "name": "人力资源部", "parent": "HQ", "sort": 1},
    {"code": "RD", "name": "技术研发部", "parent": "HQ", "sort": 2},
    {"code": "RD_ALGO", "name": "算法组", "parent": "RD", "sort": 1},
    {"code": "RD_BACKEND", "name": "后端组", "parent": "RD", "sort": 2},
    {"code": "MKT", "name": "市场运营部", "parent": "HQ", "sort": 3},
]

USERS = [
    {"username": "admin", "real_name": "超级管理员", "org": "HQ", "roles": ["super_admin"], "super": True},
    {"username": "hr01", "real_name": "人事专员", "org": "HR", "roles": ["hr"], "super": False},
    {"username": "mgr01", "real_name": "研发经理", "org": "RD", "roles": ["manager"], "super": False},
    {"username": "emp01", "real_name": "普通员工", "org": "RD_BACKEND", "roles": ["employee"], "super": False},
]


async def seed() -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            # -------------------------------------------------- 1) 部门
            org_map: dict[str, Organization] = {}
            for item in ORG_TREE:
                org = (
                    await db.execute(
                        select(Organization).where(Organization.code == item["code"])
                    )
                ).scalar_one_or_none()
                if org is None:
                    parent = org_map.get(item["parent"]) if item["parent"] else None
                    org = Organization(
                        parent_id=parent.id if parent else None,
                        code=item["code"],
                        name=item["name"],
                        sort=item["sort"],
                        level=(parent.level + 1) if parent else 1,
                        path=(parent.path + str(parent.id) + "/") if parent else "/",
                    )
                    db.add(org)
                    await db.flush()
                org_map[item["code"]] = org

            # -------------------------------------------------- 2) 用户
            for item in USERS:
                exists = (
                    await db.execute(select(User).where(User.username == item["username"]))
                ).scalar_one_or_none()
                if exists is not None:
                    print(f"跳过已存在用户：{item['username']}")
                    continue

                org = org_map.get(item["org"])
                user = User(
                    username=item["username"],
                    password_hash=hash_password(PASSWORD),
                    real_name=item["real_name"],
                    organization_id=org.id if org else None,
                    is_active=True,
                    is_superuser=item["super"],
                )
                db.add(user)
                await db.flush()

                # 绑定角色：统一走 UserRepository.set_roles（内部会 refresh 关系集合，
                # 避免异步会话下直接赋值触发 MissingGreenlet）
                await UserRepository().set_roles(db, user, item["roles"])

            await db.commit()

        print("✅ 演示数据初始化完成")
        print(f"   账号：{', '.join(u['username'] for u in USERS)}")
        print(f"   统一密码：{PASSWORD}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(seed())
