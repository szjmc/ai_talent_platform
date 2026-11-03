"""
脚本：scripts/create_admin.py
职责：创建 / 重置超级管理员账号。

标签：[架构组] [P1]（超管创建 / 重置，运维脚本）

使用方式：
    python scripts/create_admin.py                        # 交互式输入
    python scripts/create_admin.py -u admin -p Admin@12345 -n 系统管理员
    python scripts/create_admin.py -u admin --reset        # 重置已有账号密码

实现思路：
1. 复用 UserRepository + core.security.hash_password，保证与业务逻辑一致（bcrypt）；
2. 创建 super_admin 角色账号，并绑定 roles 表中全部内置角色；
3. 幂等：账号已存在时提示；带 --reset 则重置密码并解锁、启用；
4. 不写入明文密码到任何日志或文件。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.core.permissions import RoleEnum  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

DEFAULT_ROLES = [RoleEnum.SUPER_ADMIN.value, RoleEnum.ADMIN.value]


async def run(username: str, password: str, real_name: str, reset: bool) -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            repo = UserRepository()
            user = await repo.get_by_username(db, username)

            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    real_name=real_name,
                    is_active=True,
                    is_superuser=True,
                )
                db.add(user)
                await db.flush()
                await repo.set_roles(db, user, DEFAULT_ROLES)
                await db.commit()
                print(f"✅ 管理员创建成功：{username}（id={user.id}，角色={DEFAULT_ROLES}）")
            elif reset:
                user.password_hash = hash_password(password)
                user.is_superuser = True
                user.is_active = True
                user.fail_count = 0
                user.locked_until = None
                await repo.set_roles(db, user, DEFAULT_ROLES)
                await db.commit()
                print(f"✅ 已重置管理员密码并解锁：{username}（id={user.id}）")
            else:
                print(f"ℹ️ 用户已存在：{username}（如需重置密码请加 --reset）")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="创建/重置超级管理员")
    parser.add_argument("-u", "--username", default=None, help="登录名（默认 admin）")
    parser.add_argument("-p", "--password", default=None, help="密码（不传则交互输入）")
    parser.add_argument("-n", "--name", default="系统管理员", help="真实姓名")
    parser.add_argument("--reset", action="store_true", help="已存在时重置密码")
    args = parser.parse_args()

    username = args.username or input("登录名 [admin]: ").strip() or "admin"
    password = args.password or getpass.getpass("密码（至少 8 位，含字母和数字）: ")
    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        print("❌ 密码不符合要求：至少 8 位且包含字母和数字")
        sys.exit(1)

    asyncio.run(run(username, password, args.name, args.reset))


if __name__ == "__main__":
    setup_logging()
    main()
