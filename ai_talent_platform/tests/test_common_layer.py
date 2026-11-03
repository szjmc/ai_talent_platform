"""
模块：tests/test_common_layer.py
职责：公共层验收测试（对应「项目公共层」清单）。

覆盖项（需要可用的 MySQL，未启动时整文件 skip）：
    [x] FastAPI 正常启动（应用可创建、路由注册成功）
    [x] 总路由注册完成（/api/v1 前缀下可访问）
    [x] Swagger 正常访问（/docs、/openapi.json 返回 200）
    [x] ORM Base 完成（模型注册、建表）
    [x] Repository 基类完成（通用 CRUD）
    [x] 统一响应格式完成（code/message/data/trace_id）
    [x] 全局异常处理完成（404 / 参数校验 / 业务异常）
    [x] 认证链路（注册 -> 登录 -> 带 Token 访问 /auth/me -> 无权限 403）
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _require_mysql(mysql_ready):
    """本文件的用例都需要真实 MySQL（注册/登录/CRUD 全链路）。"""
    return mysql_ready


async def test_health_and_root(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["status"] == "up"
    assert "trace_id" in body

    resp = await client.get("/")
    assert resp.status_code == 200
    root_body = resp.json()
    assert root_body["data"]["api_prefix"] == "/api/v1"


async def test_swagger_available(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/auth/login" in schema["paths"]
    assert "BearerAuth" in schema["components"]["securitySchemes"]


async def test_health_ready(client):
    """就绪探针：MySQL 必须可用（PgSQL 在测试环境关闭）。"""
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mysql"] == "up"
    assert data["pgsql"] == "disabled"
    assert data["status"] == "up"


async def test_404_unified_response(client):
    resp = await client.get("/api/v1/not-exists")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert "message" in body and "trace_id" in body


async def test_validation_error_unified(client):
    # password 太短 + 不含数字，触发 422
    resp = await client.post(
        "/api/v1/auth/register", json={"username": "ab", "password": "123"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert isinstance(body["data"]["errors"], list)


async def test_register_login_and_me(client):
    payload = {
        "username": "test_user_01",
        "password": "Passw0rd123",
        "real_name": "测试用户",
        "mobile": "13800000001",
    }
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["username"] == payload["username"]
    assert "password_hash" not in resp.json()["data"]

    # 重复注册 -> 409
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == 3002

    # 登录
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["token_type"] == "bearer"
    assert data["user"]["roles"] == ["employee"]
    token = data["access_token"]

    # 带 Token 访问当前用户
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["username"] == payload["username"]

    # 普通员工访问用户列表 -> 403
    resp = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 2004


async def test_wrong_password_and_no_token(client):
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "test_user_01", "password": "WrongPass123"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 2003

    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


async def test_super_admin_full_flow(client, db_session):
    """超管：创建部门 -> 创建用户 -> 列表查询，覆盖 Organization/User 服务。"""
    from app.core.security import hash_password
    from app.models.role import Role
    from app.models.user import User
    from sqlalchemy import select

    # 造一个超管
    admin = User(
        username="test_admin",
        password_hash=hash_password("Admin12345"),
        real_name="测试超管",
        is_superuser=True,
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()
    role = (
        await db_session.execute(select(Role).where(Role.code == "super_admin"))
    ).scalar_one()
    from app.repositories.user_repository import UserRepository

    await UserRepository().set_roles(db_session, admin, [role.code])
    await db_session.commit()

    login = await client.post(
        "/api/v1/auth/login", json={"username": "test_admin", "password": "Admin12345"}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    # 1) 创建根部门
    resp = await client.post(
        "/api/v1/organizations",
        json={"code": "TEST_HQ", "name": "测试总部", "sort": 1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    root = resp.json()["data"]
    assert root["level"] == 1 and root["path"] == "/"

    # 2) 创建子部门
    resp = await client.post(
        "/api/v1/organizations",
        json={"code": "TEST_RD", "name": "测试研发部", "parent_id": root["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    child = resp.json()["data"]
    assert child["level"] == 2
    assert child["path"].endswith(f"/{root['id']}/")

    # 3) 组织树
    resp = await client.get("/api/v1/organizations/tree", headers=headers)
    assert resp.status_code == 200
    tree = resp.json()["data"]
    names = [n["name"] for n in tree]
    assert "测试总部" in names

    # 4) 创建用户并挂到子部门
    resp = await client.post(
        "/api/v1/users",
        json={
            "username": "test_emp_02",
            "password": "Emp123456",
            "real_name": "员工二号",
            "organization_id": child["id"],
            "role_codes": ["employee"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert created["organization_id"] == child["id"]
    assert created["roles"][0]["code"] == "employee"

    # 5) 用户列表分页
    resp = await client.get("/api/v1/users?page=1&page_size=10", headers=headers)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] >= 2
    assert page["page"] == 1 and page["page_size"] == 10
    assert all("password_hash" not in item for item in page["items"])

    # 6) 同级重名 -> 409
    resp = await client.post(
        "/api/v1/organizations",
        json={"code": "TEST_RD2", "name": "测试研发部", "parent_id": root["id"]},
        headers=headers,
    )
    assert resp.status_code == 409

    # 7) 有子部门时删除 -> 400 业务异常
    resp = await client.delete(f"/api/v1/organizations/{root['id']}", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1002
