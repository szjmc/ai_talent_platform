"""

标签：[Dify组] [P1]（培训六节点集成测试，真实测试库）
培训模块集成测试：六节点在真实数据库上跑通（依赖 tests/conftest.py 的 MySQL 测试库）。

所属模块：智能培训赋能 Agent（培训模块）

与 tests/test_training_logic.py 的分工：
    test_training_logic.py    纯算法，零依赖，验证「算得对不对」
    本文件                     真库 + Service，验证「串起来对不对」（落库、事件、降级、反作弊）

MySQL 不可用时整个模块 skip（conftest 里 mysql_ready 的既定行为），不会误报失败。

运行：
    pytest tests/test_training_service.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.infrastructure.messaging.event_bus import event_bus
from app.models.talent import Talent
from app.models.training import Course, LearningRecord, PlanCourse, TrainingPlan
from app.schemas.training import (
    CourseCreate,
    CourseUpdate,
    GapItem,
    LearningRecordUpdate,
    ScoreSubmitRequest,
    TrainingPlanGenerateRequest,
)
from app.services.training_service import TrainingService

pytestmark = pytest.mark.usefixtures("mysql_ready")


@pytest.fixture()
async def db():
    """本模块专用会话：NullPool + 每用例独立 engine。

    为什么不用 conftest 的 db_session：
        conftest 的 engine 是模块级单例，连接池里的连接绑定在 mysql_ready 那次
        asyncio.run() 的事件循环上；pytest-asyncio 每个用例又新建事件循环，
        复用旧连接在 Windows + Python 3.13 + aiomysql 下会抛
        AttributeError: 'NoneType' object has no attribute 'send'。
        NullPool 不缓存连接，「连接 vs 事件循环」一一对应，从根上避开该问题。
        （公共 conftest 不在本模块职责范围，故只在本模块内隔离。）
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()

DIMENSION = "需求分析"

GAPS = [
    {"dimension": DIMENSION, "current": 62, "required": 85, "weight": 0.30},
    {"dimension": "数据分析", "current": 55, "required": 80, "weight": 0.25},
    {"dimension": "SQL与数据库", "current": 48, "required": 70, "weight": 0.15},
    {"dimension": "项目管理", "current": 78, "required": 85, "weight": 0.20},
    {"dimension": "沟通表达", "current": 82, "required": 80, "weight": 0.10},
]


# ------------------------------------------------------------------ 夹具
async def _make_talent(db, suffix: str) -> Talent:
    talent = Talent(
        name=f"测试人才{suffix}",
        gender="male",
        mobile=f"1380000{suffix}",
        work_years=5,
        status="active",
        source="manual",
    )
    db.add(talent)
    await db.flush()
    return talent


async def _make_courses(db, *titles: str) -> None:
    """按维度造课：每个维度造 L2/L3 两档，保证 target_level 的贴合区间里有课。"""
    catalog = {
        DIMENSION: [("需求优先级与 MVP 设计", "L3", 8), ("需求分析基础", "L2", 6)],
        "数据分析": [("业务数据分析方法", "L3", 8), ("数据看板与可视化", "L2", 8)],
        "SQL与数据库": [("SQL 查询优化", "L2", 6), ("SQL 入门", "L1", 8)],
        "项目管理": [("敏捷与 Scrum 实战", "L3", 4), ("项目风险与干系人", "L4", 6)],
        "沟通表达": [("结构化表达", "L2", 4)],
    }
    for dimension, rows in catalog.items():
        for title, level, hours in rows:
            if title not in titles and titles:  # 指定 titles 时只造这些（用于空库测试）
                continue
            db.add(
                Course(
                    title=title,
                    category=dimension,
                    level=level,
                    duration_hours=hours,
                    description=f"{dimension}课程",
                    skill_tags=[dimension],
                    is_active=True,
                )
            )
    await db.flush()


async def _cleanup(db, talent_id: int) -> None:
    """清理本用例产生的计划（课程库由 session 级 fixture 管理，不重复删）。"""
    plans = (
        await db.execute(select(TrainingPlan.id).where(TrainingPlan.talent_id == talent_id))
    ).scalars().all()
    for plan_id in plans:
        await db.execute(
            LearningRecord.__table__.delete().where(LearningRecord.plan_id == plan_id)
        )
        await db.execute(PlanCourse.__table__.delete().where(PlanCourse.plan_id == plan_id))
        await db.execute(TrainingPlan.__table__.delete().where(TrainingPlan.id == plan_id))
    await db.flush()


def _req(
    talent_id: int,
    *,
    weeks: int = 4,
    hours: float = 8,
    use_ai: bool = False,
    gaps=None,
    replace_existing: bool = False,
    draft: bool = False,
):
    return TrainingPlanGenerateRequest(
        talent_id=talent_id,
        gaps=[GapItem(**gap) for gap in (gaps if gaps is not None else GAPS)],
        max_weeks=weeks,
        max_hours_per_week=hours,
        use_ai=use_ai,
        replace_existing=replace_existing,
        draft=draft,
    )


# ------------------------------------------------------------------ 节点 1
async def test_节点1_剔除已达标项并排优先级(db):
    talent = await _make_talent(db, "0001")
    service = TrainingService(db)

    gaps = await service.diagnose(talent.id, gaps=[GapItem(**g) for g in GAPS])

    assert len(gaps) == 4, "沟通表达已达标（82 ≥ 80），必须被剔除"
    assert all(gap["dimension"] != "沟通表达" for gap in gaps)
    # P0 三条缺口都在 20 分以上；项目管理缺口 7 分降为 P2
    assert [gap["priority"] for gap in gaps].count("P0") == 3
    assert gaps[-1]["dimension"] == "项目管理"
    assert gaps[-1]["priority"] == "P2"
    await _cleanup(db, talent.id)


async def test_节点1_全部达标时返回空清单而不是报错(db):
    talent = await _make_talent(db, "0002")
    service = TrainingService(db)
    passed = [
        {"dimension": DIMENSION, "current": 90, "required": 80, "weight": 0.3},
        {"dimension": "数据分析", "current": 85, "required": 80, "weight": 0.2},
    ]
    gaps = await service.diagnose(talent.id, gaps=[GapItem(**p) for p in passed])
    assert gaps == [], "全部达标是最好的结果，不是异常"

    plan = await service.generate_plan(_req(talent.id, gaps=passed))
    assert plan["items"] == []
    assert plan["total_hours"] == 0
    assert "无需" in plan["name"] or plan["summary"], "要给出明确结论，而不是空对象"
    await _cleanup(db, talent.id)


# ------------------------------------------------------------------ 节点 2~4
async def test_节点2至4_生成方案且课程全部来自课程库(db):
    talent = await _make_talent(db, "0003")
    await _make_courses(db)
    service = TrainingService(db)

    plan = await service.generate_plan(_req(talent.id))

    assert plan["id"] > 0
    assert plan["items"], "应当排出课表"
    assert plan["library_rate"] == 1.0, "验收硬指标：课程必须 100% 来自课程库"
    assert plan["external_count"] == 0
    assert all(item["course_id"] for item in plan["items"]), "每项都必须挂真实课程 ID"
    assert plan["total_hours"] <= 4 * 8, "总学时不得超过周期容量"
    assert plan["assessment"]["week"] == 4, "末周留考核"
    await _cleanup(db, talent.id)


async def test_节点3_课程库没有该维度时降级而不编造(db):
    """关键护栏：库里没课 → 明确标注外部建议，绝不编课程名。"""
    talent = await _make_talent(db, "0004")
    # 只造「沟通表达」的课，缺口里的三个维度都无课
    await _make_courses(db, "结构化表达")
    service = TrainingService(db)

    plan = await service.generate_plan(_req(talent.id))

    external = [item for item in plan["items"] if not item["is_platform"]]
    assert external, "应当出现降级项"
    assert all(item["course_id"] is None for item in external)
    assert all(item["title"] is None for item in external), "降级项不得有课程名（防编造）"
    assert all(item["external_suggestion"] for item in external), "要给出可执行的外部建议"
    assert plan["degrade"] is True
    assert plan["library_rate"] < 1.0
    await _cleanup(db, talent.id)


async def test_节点2_容量不足时显式报错而不是偷塞学时(db):
    talent = await _make_talent(db, "0005")
    await _make_courses(db)
    service = TrainingService(db)

    # 28 学时需求 / 4 周 × 2 学时 = 8 学时容量，装不下
    with pytest.raises(BusinessRuleError) as exc:
        await service.generate_plan(_req(talent.id, weeks=4, hours=2))
    assert "容量" in str(exc.value) or "延长" in str(exc.value)
    await _cleanup(db, talent.id)


async def test_方案必须挂在真实人才上(db):
    """传错人才 ID 要给 404 业务提示，而不是数据库外键 500。"""
    service = TrainingService(db)
    with pytest.raises(NotFoundError):
        await service.generate_plan(_req(999_999_999))


# ------------------------------------------------------------------ 节点 5
async def test_节点5_进度回退被拒且刷课被标记(db):
    talent = await _make_talent(db, "0006")
    await _make_courses(db)
    service = TrainingService(db)

    plan = await service.generate_plan(_req(talent.id))
    item = next(i for i in plan["items"] if i["course_id"])

    saved = await service.update_progress(
        LearningRecordUpdate(
            plan_id=plan["id"],
            course_id=item["course_id"],
            talent_id=talent.id,
            progress=80,
            study_minutes=int(item["hours"] * 60 * 0.8),
        )
    )
    assert saved["progress"] == 80
    assert saved["suspicious"] is False

    # 进度回退必须拦住
    with pytest.raises(ParamError):
        await service.update_progress(
            LearningRecordUpdate(
                plan_id=plan["id"],
                course_id=item["course_id"],
                talent_id=talent.id,
                progress=50,
                study_minutes=100,
            )
        )

    # 100% 进度只学 1 分钟 → 疑似刷课（标记但不阻断）
    cheat = await service.update_progress(
        LearningRecordUpdate(
            plan_id=plan["id"],
            course_id=item["course_id"],
            talent_id=talent.id,
            progress=100,
            study_minutes=1,
        )
    )
    assert cheat["suspicious"] is True
    await _cleanup(db, talent.id)


async def test_节点5_落后超阈值发出HR预警事件(db):
    talent = await _make_talent(db, "0007")
    await _make_courses(db)
    service = TrainingService(db)

    plan = await service.generate_plan(_req(talent.id))
    item = next(i for i in plan["items"] if i["course_id"])
    await service.update_progress(
        LearningRecordUpdate(
            plan_id=plan["id"],
            course_id=item["course_id"],
            talent_id=talent.id,
            progress=10,
            study_minutes=int(item["hours"] * 60 * 0.1),
        )
    )

    # 把周期改成「已过 90%」，制造落后
    from datetime import date, timedelta

    plan_row = (
        await db.execute(select(TrainingPlan).where(TrainingPlan.id == plan["id"]))
    ).scalar_one()
    plan_row.start_date = date.today() - timedelta(days=25)
    plan_row.end_date = date.today() + timedelta(days=3)
    await db.flush()

    captured: list[dict] = []
    event_bus.subscribe("training.lag.alert", captured.append)
    try:
        # 显式 publish=True：查询侧默认不推（见下一条用例），
        # 推送由定时扫描 scan_lag 负责；这里验的是事件内容本身。
        progress = await service.get_progress(plan["id"], publish=True)
    finally:
        event_bus.unsubscribe("training.lag.alert", captured.append)

    assert progress["alert"] in ("HR预警", "提醒")
    await event_bus.drain(timeout=1)
    assert captured, "滞后时必须发出事件，供 HR 看板 / 通知消费"
    await _cleanup(db, talent.id)


# ------------------------------------------------------------------ 节点 6
async def test_节点6_达标发复测事件且开关可关闭(db):
    talent = await _make_talent(db, "0008")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    captured: list[dict] = []
    event_bus.subscribe("training.retest.required", captured.append)
    try:
        result = await service.submit_score(
            ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=88)
        )
    finally:
        event_bus.unsubscribe("training.retest.required", captured.append)

    assert result["passed"] is True
    assert result["plan_status"] == "finished"
    await event_bus.drain(timeout=1)
    assert len(captured) == 1, "达标后应回流一次复测事件"

    # HR 关掉开关就不发
    plan2 = await service.generate_plan(_req(talent.id))
    captured.clear()
    event_bus.subscribe("training.retest.required", captured.append)
    try:
        result2 = await service.submit_score(
            ScoreSubmitRequest(
                plan_id=plan2["id"], talent_id=talent.id, score=92, auto_retest=False
            )
        )
    finally:
        event_bus.unsubscribe("training.retest.required", captured.append)
    await event_bus.drain(timeout=1)

    assert result2["passed"] is True
    assert captured == [], "auto_retest=False 时不得发送复测事件"
    await _cleanup(db, talent.id)


async def test_节点6_不及格时计划回到进行中(db):
    talent = await _make_talent(db, "0009")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    result = await service.submit_score(
        ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=55)
    )

    assert result["passed"] is False
    assert result["plan_status"] == "ongoing", "未通过应继续培训，而不是草率结束"
    assert result["events"] == [] or all(
        e["type"] != "training.retest.required" for e in result["events"]
    )
    await _cleanup(db, talent.id)


# ------------------------------------------------------------------ 课程管理
async def test_课程创建与维度一致性提醒(db):
    """课程维度写错（多两个字）会导致该维度静默降级——这里用测试锁定行为。"""
    service = TrainingService(db)
    course = await service.create_course(
        CourseCreate(
            title="维度写错的课",
            category="需求分析能力",  # 与 gaps 里的「需求分析」不一致
            level="L3",
            duration_hours=8,
        )
    )
    assert course.id > 0

    talent = await _make_talent(db, "0010")
    await _make_courses(db)  # 只造正确维度的课
    plan = await service.generate_plan(_req(talent.id))
    # 多两个字的那门课不会命中，方案仍应 100% 来自正确维度的课
    assert all(
        item["title"] != "维度写错的课" for item in plan["items"]
    ), "维度名不一致的课程不得被匹配到"

    await db.delete(course)
    await _cleanup(db, talent.id)


# ------------------------------------------------------------------ Skills 可编排层
@pytest.fixture()
def nullpool_scope():
    """给技能用的会话工厂：与 db 夹具同样的 NullPool 策略。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session:
                yield session
                await session.commit()
        finally:
            await engine.dispose()

    return _scope


async def test_技能_生成方案能被编排层直接调用(db, monkeypatch, nullpool_scope):
    """Agent / Workflow 通过技能调培训模块，返回结构必须可序列化、字段齐。"""
    from app.ai.skills.training_skill import TrainingPlanGenerateSkill

    talent = await _make_talent(db, "0011")
    await _make_courses(db)
    await db.commit()  # 技能另开会话，需要数据已提交

    monkeypatch.setattr(
        "app.ai.skills.training_skill._load_session_scope", lambda: nullpool_scope
    )
    data = await TrainingPlanGenerateSkill().execute(
        talent_id=talent.id, gaps=GAPS, max_weeks=4, max_hours_per_week=8
    )

    assert data["plan_id"] > 0
    assert data["library_rate"] == 1.0
    assert data["items"], "技能必须返回可渲染的课表"
    required = {"week", "dimension", "hours", "course_id", "title", "is_platform", "reason"}
    assert required <= set(data["items"][0]), f"缺少字段：{required - set(data['items'][0])}"
    assert "assessment" in data and data["assessment"]["week"] == 4

    await _cleanup(db, talent.id)


async def test_技能_空短板清单直接拒绝(db, monkeypatch, nullpool_scope):
    """空 gaps 不静默返回空方案，而是明确报参数错误。"""
    from app.ai.skills.base import SkillParamError
    from app.ai.skills.training_skill import TrainingPlanGenerateSkill

    monkeypatch.setattr(
        "app.ai.skills.training_skill._load_session_scope", lambda: nullpool_scope
    )
    with pytest.raises(SkillParamError):
        await TrainingPlanGenerateSkill().execute(talent_id=1, gaps=[])


def test_技能清单里能查到培训技能():
    """Agent 编排前的自查：两个技能必须已注册且参数 Schema 合法。"""
    from app.ai.skills import EXPECTED_SKILLS, bootstrap_skills, list_tools

    registered = bootstrap_skills()
    assert {"training_plan_generate", "training_lag_scan"} <= set(registered)
    assert {"training_plan_generate", "training_lag_scan"} <= set(EXPECTED_SKILLS)

    tools = {tool["function"]["name"]: tool["function"] for tool in list_tools()}
    generate = tools["training_plan_generate"]
    assert generate["parameters"]["type"] == "object"
    assert set(generate["parameters"]["required"]) == {"talent_id", "gaps"}
    assert tools["training_lag_scan"]["parameters"]["required"] == []


# ================================================================== 二轮整改回归
# 以下用例对应《培训模块遗留问题清单》里的条目，每条守一个「接口返回码正常、
# 但业务结果错了」的缺陷——这类问题第一轮的契约测试抓不到，只能靠断言业务值。

async def test_考核后累计学时不被清零(db):
    """P0-1：submit_score 曾传 study_minutes=0，把整份计划的学时归零。

    后果链：进度页永远「完成 0h / 落后 100%」→ 未通过时计划仍 ongoing
    → 下一次 lag-scan 把刚学完 4 周的学员判成 HR 预警。
    """
    talent = await _make_talent(db, "0031")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    item = next(i for i in plan["items"] if i["course_id"])
    await service.update_progress(
        LearningRecordUpdate(
            plan_id=plan["id"],
            course_id=item["course_id"],
            talent_id=talent.id,
            progress=60,
            study_minutes=int(item["hours"] * 60 * 0.6),
        )
    )
    before = await service.repo.record.sum_minutes(db, plan["id"])
    assert before > 0, "前置条件：上报后应有累计学时"

    # 考核不通过（72 < 80）
    await service.submit_score(
        ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=72)
    )

    after = await service.repo.record.sum_minutes(db, plan["id"])
    assert after == before, f"考核不得改变累计学时：{before} -> {after}"

    progress = await service.get_progress(plan["id"])
    assert progress["completed_h"] > 0, "复核：进度判定不能因为考了一次试就归零"
    await _cleanup(db, talent.id)


async def test_查询进度默认不产生副作用事件(db):
    """P1-3：GET /progress 曾经每调一次就发一次预警事件（前端轮询 = 重复轰炸）。

    查询侧只算不推，推送交给 POST /lag-scan。
    """
    from datetime import date, timedelta

    talent = await _make_talent(db, "0032")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    row = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == plan["id"]))).scalar_one()
    row.start_date = date.today() - timedelta(days=25)
    row.end_date = date.today() + timedelta(days=3)
    await db.flush()

    captured: list[dict] = []
    event_bus.subscribe("training.lag.alert", captured.append)
    event_bus.subscribe("training.lag.remind", captured.append)
    try:
        result = await service.get_progress(plan["id"])
    finally:
        event_bus.unsubscribe("training.lag.alert", captured.append)
        event_bus.unsubscribe("training.lag.remind", captured.append)
    await event_bus.drain(timeout=1)

    assert result["alert"] in ("HR预警", "提醒"), "前置条件：确实落后"
    assert captured == [], "查询接口不得触发推送事件"
    await _cleanup(db, talent.id)


async def test_重复生成计划被拒绝且可显式替换(db):
    """P0-2：连点「生成方案」会为同一人才堆出多份同名计划（本地库已出现 2 份）。

    默认 409 拒绝；replace_existing=True 时旧计划置 cancelled 后再建。
    """
    from app.core.exceptions import DataConflictError

    talent = await _make_talent(db, "0033")
    await _make_courses(db)
    service = TrainingService(db)

    first = await service.generate_plan(_req(talent.id))
    assert first["id"]

    with pytest.raises(DataConflictError) as exc:
        await service.generate_plan(_req(talent.id))
    assert "已有进行中" in str(exc.value)

    replaced = await service.generate_plan(_req(talent.id, replace_existing=True))
    assert replaced["id"] != first["id"], "替换应生成新计划"

    old = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == first["id"]))).scalar_one()
    assert old.status == "cancelled", "旧计划必须置为 cancelled，不能继续参与扫描"
    await _cleanup(db, talent.id)


async def test_已结课计划拒绝重复打分但可强制改判(db):
    """P2-4：重复提交会把「已通过」覆盖成「不通过」，连带改掉已归档的通过率。"""
    from app.core.exceptions import DataConflictError

    talent = await _make_talent(db, "0034")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    first = await service.submit_score(
        ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=88)
    )
    assert first["plan_status"] == "finished"

    with pytest.raises(DataConflictError):
        await service.submit_score(
            ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=50)
        )

    forced = await service.submit_score(
        ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=50, force=True)
    )
    assert forced["passed"] is False, "force=True 时允许改判"
    await _cleanup(db, talent.id)


async def test_完成率按学时加权而不是课程数平均(db):
    """P2-3：8h 课与 4h 课不该同权。

    构造：8h 课 100%、4h 课 0% → 加权完成率 66.7%，简单平均会算成 50%。
    """
    talent = await _make_talent(db, "0035")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    links = await db.execute(
        select(PlanCourse, Course)
        .join(Course, Course.id == PlanCourse.course_id)
        .where(PlanCourse.plan_id == plan["id"])
    )
    rows = [(link.course_id, float(course.duration_hours or 8)) for link, course in links]
    assert len(rows) >= 2, "前置条件：至少两门课"
    rows.sort(key=lambda pair: -pair[1])
    big, small = rows[0], rows[1]

    await service.update_progress(
        LearningRecordUpdate(
            plan_id=plan["id"], course_id=big[0], talent_id=talent.id,
            progress=100, study_minutes=int(big[1] * 60),
        )
    )
    rate = await service.repo.plan.completion_rate(db, plan["id"])

    total = await service.repo.plan.total_hours(db, plan["id"])
    expected = round(big[1] / total, 4)
    simple = round(1 / len(rows), 4)  # 只有一门课 100% 时的「课程数简单平均」
    assert abs(rate - expected) < 0.02, f"加权完成率应约 {expected}，实际 {rate}"
    if abs(expected - simple) > 0.005:
        # 学时分布不均时，加权与简单平均必然不同；这正是本用例要守的口径
        assert abs(rate - simple) > 0.005, (
            f"完成率必须是学时加权（{expected}），不能是课程数简单平均（{simple}），实际 {rate}"
        )
    await _cleanup(db, talent.id)


async def test_计划下发写入站内信且当日幂等(db):
    """P1-1：事件此前没有任何订阅者，推送只是一句空话。

    现在落成可查询的站内信；同计划同类型同日只保留一条。
    """
    from app.models.training import TrainingNotification
    from app.services.notification_service import TrainingNotificationService

    talent = await _make_talent(db, "0036")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))

    rows = (
        await db.execute(
            select(TrainingNotification).where(TrainingNotification.plan_id == plan["id"])
        )
    ).scalars().all()
    assert rows, "计划下发必须落一条站内信，否则「推送给员工」无从验证"
    assert rows[0].talent_id == talent.id
    assert rows[0].type == "training.plan_created"
    assert rows[0].is_read is False

    # 幂等：同一事件再写一次不会重复
    notifier = TrainingNotificationService(db)
    again = await notifier.record(
        {
            "type": "training.plan_created",
            "plan_id": plan["id"],
            "talent_id": talent.id,
            "title": "重复",
            "content": "重复",
        }
    )
    assert again is None, "同日同类型通知必须幂等，避免扫描重跑造成重复轰炸"
    await _cleanup(db, talent.id)


async def test_全维度达标返回空id而不是0(db):
    """P2-1：曾返回 id=0，前端拿 0 查详情直接 404。"""
    talent = await _make_talent(db, "0037")
    service = TrainingService(db)
    passed = [
        {"dimension": DIMENSION, "current": 90, "required": 80, "weight": 0.3},
        {"dimension": "数据分析", "current": 85, "required": 80, "weight": 0.2},
    ]
    plan = await service.generate_plan(_req(talent.id, gaps=passed))
    assert plan["id"] is None, "未生成计划时不能给 id=0 冒充真实主键"
    assert plan["no_training_needed"] is True
    await _cleanup(db, talent.id)


async def test_更新课程不能改成重名(db):
    """P1-4：create 校验重名、update 曾经不校验，可把 B 改成与 A 同名。"""
    talent = await _make_talent(db, "0038")
    service = TrainingService(db)
    first = await service.create_course(CourseCreate(title="重名校验A", category=DIMENSION, level="L2", duration_hours=4))
    await service.create_course(CourseCreate(title="重名校验B", category=DIMENSION, level="L2", duration_hours=4))

    with pytest.raises(BusinessRuleError):
        await service.update_course(first.id, CourseUpdate(title="重名校验B"))

    # 改成自己的名字不算重名
    same = await service.update_course(first.id, CourseUpdate(title="重名校验A"))
    assert same.title == "重名校验A"
    await _cleanup(db, talent.id)


async def test_单课考核不会让整份计划结课(db):
    """新发现缺陷：指定 course_id 的考核曾把 plan.status 置为 finished。

    后果：4 门课里考过 1 门，计划就退出滞后扫描，剩下的课等于自动豁免。
    只有整计划结课考核（不传 course_id）才应该改变计划状态。
    """
    talent = await _make_talent(db, "0039")
    await _make_courses(db)
    service = TrainingService(db)
    plan = await service.generate_plan(_req(talent.id))
    item = next(i for i in plan["items"] if i["course_id"])

    result = await service.submit_score(
        ScoreSubmitRequest(
            plan_id=plan["id"],
            talent_id=talent.id,
            course_id=item["course_id"],
            score=92,
        )
    )
    assert result["passed"] is True
    assert result["plan_status"] == "ongoing", "单课通过不得让整份计划结课"

    row = (await db.execute(select(TrainingPlan).where(TrainingPlan.id == plan["id"]))).scalar_one()
    assert row.status == "ongoing"
    await _cleanup(db, talent.id)
