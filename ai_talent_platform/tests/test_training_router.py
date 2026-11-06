"""

标签：[Dify组] [P1]（培训接口层回归，守 422/归属/口径）
培训模块接口层回归测试（Router 级，ASGI 直连 + 真实 MySQL 测试库）。

所属模块：智能培训赋能 Agent（培训模块）

为什么单独一个文件：
    test_training_service.py 直接调 Service，绕过了 FastAPI 的参数校验与依赖注入，
    因此「路径参数 + 请求体 Schema」之间的冲突测不出来——提交考核接口曾因
    ScoreSubmitRequest.plan_id 设为必填，导致 POST /trainings/plans/{id}/score
    恒定返回 422（Service 单测全绿，接口根本调不通）。本文件专门守这条缝。

覆盖的 8 个回归点：
    1. 提交考核：只给路径 plan_id、请求体不带 plan_id 必须成功（曾 422）
    2. 进度上报：他人 talent_id 必须被拒（曾 500 外键异常）
    3. 进度上报：计划外课程必须被拒（曾静默写入，污染完成率/通过率）
    4. 计划详情：total_weeks 按起止日期算（曾返回课程数）
    5. 学满 100% 只算完成、不算通过（曾写 passed=True，通过率虚高）
    6. 生成接口与详情接口的 total_hours 同源（曾 15.0 vs 15.5）
    7. 写进度接口的权限：只读角色不得越权写入（曾挂 TRAINING_READ）
    8. 并发上报撞唯一键：退化为更新，不整单 500

运行：
    pytest tests/test_training_router.py -q
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.core.application import create_application
from app.core.security import hash_password
from app.infrastructure.database.session import get_db
from app.models.role import Role
from app.models.talent import Talent
from app.models.training import Course
from app.models.user import User
from app.repositories.training_repository import TrainingRepository
from app.repositories.user_repository import UserRepository

pytestmark = pytest.mark.usefixtures("mysql_ready")

ADMIN_PASSWORD = "Admin12345"
DIMENSION = "需求分析"
GAPS = [
    {"dimension": DIMENSION, "current": 62, "required": 85, "weight": 0.30},
    {"dimension": "数据分析", "current": 55, "required": 80, "weight": 0.25},
]

# 本模块独立 app 实例：只为把 get_db 换成 NullPool 会话，
# 避免 conftest 里模块级 engine 的连接池跨事件循环复位（Windows + aiomysql 已知问题）。
app = create_application()


@pytest.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    """NullPool 会话 + 同步把 get_db 依赖指到同一个工厂。"""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with factory() as session:
            yield session
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.fixture()
async def client(db) -> AsyncGenerator[object, None]:
    """ASGI 直连客户端（依赖 db，确保依赖覆盖已生效）。"""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ------------------------------------------------------------------ 夹具
async def _admin_headers(client, db, suffix: str) -> dict[str, str]:
    """造一个超管并登录，返回鉴权头。"""
    username = f"tr_admin_{suffix}"
    admin = User(
        username=username,
        password_hash=hash_password(ADMIN_PASSWORD),
        real_name=f"培训测试超管{suffix}",
        is_superuser=True,
        is_active=True,
    )
    db.add(admin)
    await db.flush()
    role = (
        await db.execute(select(Role).where(Role.code == "super_admin"))
    ).scalar_one()
    await UserRepository().set_roles(db, admin, [role.code])
    await db.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _make_talent(db, suffix: str) -> Talent:
    talent = Talent(
        name=f"培训接口人才{suffix}",
        gender="male",
        mobile=f"1390000{suffix}",
        work_years=4,
        status="active",
        source="manual",
    )
    db.add(talent)
    await db.flush()
    return talent


async def _make_courses(db, suffix: str) -> dict[str, int]:
    """造两门课，返回 {课程名: id}。"""
    rows = {
        f"接口测试·需求分析{suffix}": (DIMENSION, "L3", 8),
        f"接口测试·数据分析{suffix}": ("数据分析", "L3", 6),
    }
    ids: dict[str, int] = {}
    for title, (category, level, hours) in rows.items():
        course = Course(
            title=title,
            category=category,
            level=level,
            duration_hours=hours,
            description=f"{category}接口测试课程",
            skill_tags=[category],
            is_active=True,
        )
        db.add(course)
        await db.flush()
        ids[title] = course.id
    await db.commit()
    return ids


async def _generate_plan(client, headers, talent_id: int) -> dict:
    resp = await client.post(
        "/api/v1/trainings/plans/generate",
        json={
            "talent_id": talent_id,
            "gaps": GAPS,
            "max_weeks": 4,
            "max_hours_per_week": 8,
            "use_ai": False,
            "auto_push": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ------------------------------------------------------------------ 1
async def test_提交考核只给路径plan_id即可通过(client, db):
    """回归：请求体不再强制 plan_id（曾导致该接口恒定 422，Service 单测却全绿）。"""
    headers = await _admin_headers(client, db, "s1")
    talent = await _make_talent(db, "s1")
    await _make_courses(db, "s1")
    plan = await _generate_plan(client, headers, talent.id)
    assert plan["id"] > 0

    resp = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score",
        json={"score": 88},  # 注意：不传 plan_id / talent_id
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["plan_id"] == plan["id"]
    assert data["talent_id"] == talent.id, "未传人才时应取计划所属人才"
    assert data["passed"] is True
    assert data["plan_status"] == "finished"


# ------------------------------------------------------------------ 2
async def test_进度上报拒绝他人人才(client, db):
    """回归：传别人的 talent_id 曾触发外键异常 500，且可能把进度记到别人名下。"""
    headers = await _admin_headers(client, db, "s2")
    talent = await _make_talent(db, "s2")
    other = await _make_talent(db, "s2b")
    await _make_courses(db, "s2")
    plan = await _generate_plan(client, headers, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])

    resp = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": other.id,  # 不是本计划的人才
            "progress": 30,
            "study_minutes": 60,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "不匹配" in resp.json()["message"]


# ------------------------------------------------------------------ 3
async def test_进度上报拒绝计划外课程(client, db):
    """回归：计划外课程曾静默写入记录，稀释完成率分母、污染按课程统计。"""
    headers = await _admin_headers(client, db, "s3")
    talent = await _make_talent(db, "s3")
    await _make_courses(db, "s3")
    plan = await _generate_plan(client, headers, talent.id)

    # 另造一门一定不在计划里的课
    outsider = Course(
        title="接口测试·计划外课程s3",
        category="项目管理",
        level="L2",
        duration_hours=4,
        is_active=True,
    )
    db.add(outsider)
    await db.flush()
    await db.commit()

    resp = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": outsider.id,
            "talent_id": talent.id,
            "progress": 20,
            "study_minutes": 40,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "不属于计划" in resp.json()["message"]


# ------------------------------------------------------------------ 4
async def test_计划详情周数按日期算而不是课程数(client, db):
    """回归：total_weeks 曾等于课程条数（2 门课 → 2 周），与实际 4 周周期不符。"""
    headers = await _admin_headers(client, db, "s4")
    talent = await _make_talent(db, "s4")
    await _make_courses(db, "s4")
    plan = await _generate_plan(client, headers, talent.id)

    resp = await client.get(f"/api/v1/trainings/plans/{plan['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["total_weeks"] == 4, "max_weeks=4 的计划，详情必须报 4 周"
    assert detail["assessment"]["week"] == 4, "结课考核落在最后一周"
    # 起止日期跨度应等于 4 周
    from datetime import date as _date

    span = (
        _date.fromisoformat(detail["end_date"]) - _date.fromisoformat(detail["start_date"])
    ).days + 1
    assert 28 <= span <= 34


# ------------------------------------------------------------------ 5
async def test_学满百分百只算完成不算通过(client, db):
    """回归：100% 进度曾被写成 passed=True，使未考核记录进入通过率分母并计为通过。"""
    headers = await _admin_headers(client, db, "s5")
    talent = await _make_talent(db, "s5")
    await _make_courses(db, "s5")
    plan = await _generate_plan(client, headers, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])

    resp = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": talent.id,
            "progress": 100,
            "study_minutes": 480,  # 学满 8 小时，不触发反作弊
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["data"]
    assert record["status"] == "completed"
    assert record["passed"] is None, "没考就是没考，不能因为学完就算通过"

    stats = await client.get(
        f"/api/v1/trainings/stats/pass-rate?plan_id={plan['id']}", headers=headers
    )
    assert stats.status_code == 200, stats.text
    assert stats.json()["data"]["total_examined"] == 0, "未考核记录不得进入通过率分母"


# ------------------------------------------------------------------ 6
async def test_生成与详情的总学时一致(client, db):
    """回归：生成接口返回排期学时、详情返回课程学时，同名字段两个数（15.0 vs 15.5）。"""
    headers = await _admin_headers(client, db, "s6")
    talent = await _make_talent(db, "s6")
    await _make_courses(db, "s6")
    plan = await _generate_plan(client, headers, talent.id)

    resp = await client.get(f"/api/v1/trainings/plans/{plan['id']}", headers=headers)
    detail = resp.json()["data"]
    assert plan["total_hours"] == detail["total_hours"], (
        f"生成 {plan['total_hours']} 与详情 {detail['total_hours']} 必须同源"
    )
    # 排期学时单独给出，差额来自课程实际时长，不再是「同一个字段两个值」
    assert plan["scheduled_hours"] is not None
    assert plan["scheduled_hours"] >= plan["total_hours"] - 0.01


# ------------------------------------------------------------------ 7
async def test_考核分数越界与人才错配被拦(client, db):
    """边界：分数越界 422；人才与计划不匹配 422（不写脏数据）。"""
    headers = await _admin_headers(client, db, "s7")
    talent = await _make_talent(db, "s7")
    other = await _make_talent(db, "s7b")
    await _make_courses(db, "s7")
    plan = await _generate_plan(client, headers, talent.id)

    bad_score = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score",
        json={"score": 120},
        headers=headers,
    )
    assert bad_score.status_code == 422

    mismatch = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score",
        json={"score": 90, "talent_id": other.id},
        headers=headers,
    )
    assert mismatch.status_code == 422
    assert "不匹配" in mismatch.json()["message"]


# ------------------------------------------------------------------ 8 权限
async def _role_headers(
    client, db, suffix: str, role_code: str, *, talent_id: int | None = None
) -> dict[str, str]:
    """造一个指定角色的普通用户并登录（用于越权校验）。

    :param talent_id: 绑定的人才档案 ID。员工自助上报依赖这个绑定，
                      不传就是「未绑定」账号（只能读，不能写）。
    """
    username = f"tr_{role_code}_{suffix}"
    user = User(
        username=username,
        password_hash=hash_password(ADMIN_PASSWORD),
        real_name=f"培训测试{role_code}{suffix}",
        is_superuser=False,
        is_active=True,
        talent_id=talent_id,
    )
    db.add(user)
    await db.flush()
    await UserRepository().set_roles(db, user, [role_code])
    await db.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


async def test_只读角色不能写学习进度(client, db):
    """回归：写进度接口曾挂 TRAINING_READ，EMPLOYEE / MANAGER 都能给任意人才改进度。

    读权限必须保留（员工要能看自己的计划），只收写入口。
    """
    admin = await _admin_headers(client, db, "s8")
    talent = await _make_talent(db, "s8")
    await _make_courses(db, "s8")
    plan = await _generate_plan(client, admin, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])

    employee = await _role_headers(client, db, "s8", "employee")
    write = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": talent.id,
            "progress": 40,
            "study_minutes": 200,
        },
        headers=employee,
    )
    assert write.status_code == 403, f"只读角色不得写入进度：{write.text}"

    read = await client.get(f"/api/v1/trainings/plans/{plan['id']}", headers=employee)
    assert read.status_code == 200, f"读权限不应被连带收紧：{read.text}"


# ------------------------------------------------------------------ 9 并发
async def test_并发上报撞唯一键时退化为更新(client, db, monkeypatch):
    """回归：先查后插存在竞态，后到请求撞唯一键会把整单打成 500。

    用 monkeypatch 构造「第一次查询仍查不到」的窗口，精确命中 catch 分支。
    """
    headers = await _admin_headers(client, db, "s9")
    talent = await _make_talent(db, "s9")
    await _make_courses(db, "s9")
    plan = await _generate_plan(client, headers, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])
    # 生成计划时已初始化 progress=0 的记录；结束当前事务，避免快照读不到它
    await db.commit()

    repo = TrainingRepository()
    original = repo.record.get_record
    calls = {"n": 0}

    async def fake_get(session, plan_id, course_id, talent_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 模拟并发窗口：查的时候确实还没有
        return await original(session, plan_id, course_id, talent_id)

    monkeypatch.setattr(repo.record, "get_record", fake_get)

    record = await repo.record.upsert(
        db,
        plan_id=plan["id"],
        course_id=course_id,
        talent_id=talent.id,
        progress=66,
        study_minutes=320,
    )
    assert calls["n"] >= 2, "撞键后必须重查一次"
    assert record.progress == 66, "退化为更新后仍要写入本次进度"
    assert record.plan_id == plan["id"]
    await db.rollback()


# ================================================================== 二轮整改回归
async def test_员工可上报本人进度但不能动他人(client, db):
    """P0-3：此前写接口只挂 TRAINING_MANAGE，员工连自己的进度都上报不了。

    现在拆出 training:report：绑定 talent 后只能操作本人，代他人操作需要 manage。
    """
    admin = await _admin_headers(client, db, "s10")
    talent = await _make_talent(db, "s10")
    other = await _make_talent(db, "s10b")
    await _make_courses(db, "s10")
    plan = await _generate_plan(client, admin, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])

    employee = await _role_headers(client, db, "s10", "employee", talent_id=talent.id)

    ok = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": talent.id,
            "progress": 35,
            "study_minutes": 180,
        },
        headers=employee,
    )
    assert ok.status_code == 200, f"员工必须能上报本人的学习进度：{ok.text}"

    bad = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": other.id,
            "progress": 50,
            "study_minutes": 240,
        },
        headers=employee,
    )
    assert bad.status_code == 403, f"员工不得动别人的人才档案：{bad.text}"


async def test_未绑定人才的员工仍被拒绝(client, db):
    """未绑定 users.talent_id 时不能放行：否则「自助上报」等于「谁都能改」。"""
    admin = await _admin_headers(client, db, "s11")
    talent = await _make_talent(db, "s11")
    await _make_courses(db, "s11")
    plan = await _generate_plan(client, admin, talent.id)
    course_id = next(item["course_id"] for item in plan["items"] if item["course_id"])

    employee = await _role_headers(client, db, "s11", "employee")
    resp = await client.post(
        "/api/v1/trainings/records",
        json={
            "plan_id": plan["id"],
            "course_id": course_id,
            "talent_id": talent.id,
            "progress": 20,
            "study_minutes": 100,
        },
        headers=employee,
    )
    assert resp.status_code == 403
    assert "本人" in resp.json()["message"]


async def test_重复生成方案返回409(client, db):
    """P0-2：接口层必须给出可识别的冲突码，而不是默默建出第二份同名计划。"""
    headers = await _admin_headers(client, db, "s12")
    talent = await _make_talent(db, "s12")
    await _make_courses(db, "s12")
    await _generate_plan(client, headers, talent.id)

    again = await client.post(
        "/api/v1/trainings/plans/generate",
        json={
            "talent_id": talent.id,
            "gaps": GAPS,
            "max_weeks": 4,
            "max_hours_per_week": 8,
            "use_ai": False,
        },
        headers=headers,
    )
    assert again.status_code == 409, f"应提示已有进行中计划：{again.text}"
    assert "已有进行中" in again.json()["message"]


async def test_已结课计划重复打分返回409(client, db):
    """P2-4：防止把已通过的结论覆盖成不通过。"""
    headers = await _admin_headers(client, db, "s13")
    talent = await _make_talent(db, "s13")
    await _make_courses(db, "s13")
    plan = await _generate_plan(client, headers, talent.id)

    first = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score", json={"score": 90}, headers=headers
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score", json={"score": 55}, headers=headers
    )
    assert second.status_code == 409, f"已结课不得重复打分：{second.text}"

    forced = await client.post(
        f"/api/v1/trainings/plans/{plan['id']}/score",
        json={"score": 55, "force": True},
        headers=headers,
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["data"]["passed"] is False


async def test_计划下发站内信可查询并标记已读(client, db):
    """P1-1：「推送给员工」必须能查到，而不是只发了个没人接的事件。"""
    headers = await _admin_headers(client, db, "s14")
    talent = await _make_talent(db, "s14")
    await _make_courses(db, "s14")
    await _generate_plan(client, headers, talent.id)

    listed = await client.get(
        f"/api/v1/trainings/notifications?talent_id={talent.id}", headers=headers
    )
    assert listed.status_code == 200, listed.text
    data = listed.json()["data"]
    assert data["total"] >= 1, "计划下发后必须有一条站内信"
    assert data["unread"] >= 1
    item = data["items"][0]
    assert item["type"] == "training.plan_created"
    assert item["talent_id"] == talent.id

    read = await client.post(
        f"/api/v1/trainings/notifications/{item['id']}/read", headers=headers
    )
    assert read.status_code == 200, read.text
    assert read.json()["data"]["is_read"] is True

    after = await client.get(
        f"/api/v1/trainings/notifications?talent_id={talent.id}&is_read=true", headers=headers
    )
    assert after.json()["data"]["total"] >= 1
