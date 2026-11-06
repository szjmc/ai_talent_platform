"""

标签：[Dify组] [P1]（岗位能力接入与通过率口径）
培训模块补充集成测试：岗位能力模型接入 + 考核通过率统计。

补这两块的背景（对应需求文档条款）：
    需求 46 条：培训要「基于人才能力短板、**岗位能力模型**、员工学习数据」——
        原实现只读了测评分数，目标分/权重用默认值兜底，等于「千人一面」。
    需求 101 条：要「自动组织课后考核、**统计通过率**」——
        原实现只有完成率（过程），没有通过率（结果）。

本文件只验证这两处新增能力，六节点主流程见 tests/test_training_service.py。

运行：
    pytest tests/test_training_position_and_passrate.py -q
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.models.organization import Organization
from app.models.position import Position, PositionRequirement
from app.models.talent import Talent
from app.models.training import Course, LearningRecord
from app.schemas.training import GapItem, TrainingPlanGenerateRequest
from app.services.training_service import TrainingService

pytestmark = pytest.mark.usefixtures("mysql_ready")


@pytest.fixture()
async def db():
    """NullPool + 每用例独立 engine（理由同 test_training_service.py 的 db 夹具）。"""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


def _code(prefix: str) -> str:
    """唯一编码：测试库是共享的，重复跑不能撞唯一约束。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ------------------------------------------------------------------ 造数
async def _make_org(db) -> Organization:
    org = Organization(
        code=_code("ORG"),
        name=f"测试部门{uuid.uuid4().hex[:4]}",
        level=1,
        path="/",
        sort=0,
        is_active=True,
    )
    db.add(org)
    await db.flush()
    return org


async def _make_talent(db, *, position_title: str | None = None) -> Talent:
    talent = Talent(
        name=f"岗位测试人才{uuid.uuid4().hex[:6]}",
        gender="male",
        mobile=_code("139"),
        work_years=5,
        status="active",
        source="manual",
        current_position=position_title,
    )
    db.add(talent)
    await db.flush()
    return talent


async def _make_position(
    db, org: Organization, *, dimensions=None, weights=None, with_model: bool = True
) -> Position:
    position = Position(
        title=f"岗位{uuid.uuid4().hex[:6]}",
        code=_code("POS"),
        organization_id=org.id,
        level="middle",
        status="open",
    )
    db.add(position)
    await db.flush()
    if with_model:
        db.add(
            PositionRequirement(
                position_id=position.id,
                version=1,
                dimensions=dimensions or {},
                weights=weights or {},
                source="manual",
            )
        )
        await db.flush()
    return position


def _req(talent_id: int, *, gaps, position_id: int | None = None):
    return TrainingPlanGenerateRequest(
        talent_id=talent_id,
        position_id=position_id,
        gaps=[GapItem(**gap) for gap in gaps],
        max_weeks=4,
        max_hours_per_week=8,
        use_ai=False,
    )


# ============================================================== 岗位能力模型
async def test_只给当前分时自动采用岗位模型的目标分与权重(db):
    """测评 Agent 只传「考了多少分」，岗位标准由岗位模型补——这才是千人千面。"""
    org = await _make_org(db)
    position = await _make_position(
        db,
        org,
        dimensions={"需求分析": {"required": 85}, "数据分析": {"required": 88}},
        weights={"需求分析": 0.30, "数据分析": 0.25},
    )
    talent = await _make_talent(db)
    service = TrainingService(db)

    gaps = await service.diagnose(
        talent.id,
        gaps=[{"dimension": "需求分析", "current": 60}, {"dimension": "数据分析", "current": 55}],
        position_id=position.id,
    )

    by_dim = {gap["dimension"]: gap for gap in gaps}
    assert by_dim["需求分析"]["required"] == 85.0
    assert by_dim["需求分析"]["weight"] == 0.30
    assert by_dim["数据分析"]["required"] == 88.0
    assert service._requirement_source == "position_model"
    assert service._requirement_position_id == position.id
    assert service._requirement_version == 1


async def test_调用方给了完整标准时不被岗位模型改写(db):
    """测评 Agent 明确给了目标分，就尊重它的口径，岗位模型不得覆盖。"""
    org = await _make_org(db)
    position = await _make_position(
        db, org, dimensions={"需求分析": {"required": 85}}, weights={"需求分析": 0.30}
    )
    talent = await _make_talent(db)
    service = TrainingService(db)

    gaps = await service.diagnose(
        talent.id,
        gaps=[{"dimension": "需求分析", "current": 60, "required": 95, "weight": 0.10}],
        position_id=position.id,
    )

    assert gaps[0]["required"] == 95.0
    assert gaps[0]["weight"] == 0.10
    assert service._requirement_source == "explicit"


async def test_岗位没有维护能力模型时回退默认值而不是编造(db):
    org = await _make_org(db)
    position = await _make_position(db, org, with_model=False)
    talent = await _make_talent(db)
    service = TrainingService(db)

    gaps = await service.diagnose(
        talent.id, gaps=[{"dimension": "需求分析", "current": 60}], position_id=position.id
    )

    assert gaps[0]["required"] == 80.0, "回退默认目标分"
    assert gaps[0]["weight"] == 0.2, "回退默认权重"
    assert service._requirement_source == "default"


async def test_不传岗位ID时用现职位名自动匹配岗位模型(db):
    """talent 表没有岗位外键，所以用 current_position 文本兜底匹配。"""
    org = await _make_org(db)
    position = await _make_position(
        db, org, dimensions={"SQL与数据库": {"required": 90}}, weights={"SQL与数据库": 0.3}
    )
    talent = await _make_talent(db, position_title=position.title)
    service = TrainingService(db)

    gaps = await service.diagnose(talent.id, gaps=[{"dimension": "SQL与数据库", "current": 50}])

    assert gaps[0]["required"] == 90.0
    assert service._requirement_source == "position_model"
    assert service._requirement_position_id == position.id


async def test_现职位匹配不到岗位时回退默认值(db):
    org = await _make_org(db)
    await _make_position(db, org, dimensions={"需求分析": {"required": 85}})
    talent = await _make_talent(db, position_title="一个库里不存在的岗位名")
    service = TrainingService(db)

    gaps = await service.diagnose(talent.id, gaps=[{"dimension": "需求分析", "current": 60}])

    assert gaps[0]["required"] == 80.0
    assert service._requirement_source == "default"
    assert service._requirement_position_id is None


# ============================================================== 考核通过率
async def _make_course(db, title: str) -> Course:
    course = Course(
        title=title,
        category="需求分析",
        level="L2",
        duration_hours=6,
        description="通过率测试课程",
        skill_tags=["需求分析"],
        is_active=True,
    )
    db.add(course)
    await db.flush()
    return course


async def _make_record(db, *, talent: Talent, course: Course, passed, score) -> LearningRecord:
    """造一条学习记录。

    每条记录挂一个独立计划：`(plan_id, course_id, talent_id)` 是唯一键，
    而 plan_id 收紧为 NOT NULL 后不能再靠 NULL 绕过唯一约束
    （那正是「26 条孤儿记录 + 唯一键失效」的事故根源）。
    """
    from app.models.training import TrainingPlan

    plan = TrainingPlan(
        name=f"考核计划{uuid.uuid4().hex[:8]}",
        talent_id=talent.id,
        status="finished" if passed is not None else "ongoing",
    )
    db.add(plan)
    await db.flush()
    record = LearningRecord(
        plan_id=plan.id,
        talent_id=talent.id,
        course_id=course.id,
        progress=100,
        study_minutes=360,
        status="completed",
        score=score,
        passed=passed,
    )
    db.add(record)
    await db.flush()
    return record


async def test_通过率只统计已出结论的记录(db):
    """没考的不进分母——否则「还没组织考试」会被算成「不通过」。"""
    talent = await _make_talent(db)
    course = await _make_course(db, f"通过率课程{uuid.uuid4().hex[:6]}")

    await _make_record(db, talent=talent, course=course, passed=True, score=90)
    await _make_record(db, talent=talent, course=course, passed=True, score=85)
    await _make_record(db, talent=talent, course=course, passed=False, score=60)
    # 一条「学完了但还没考」的记录：passed/score 都是空，必须被排除
    await _make_record(db, talent=talent, course=course, passed=None, score=None)

    service = TrainingService(db)
    stats = await service.pass_rate(course_id=course.id)

    assert stats["total_examined"] == 3, "分母只算 3 条已出结论的记录"
    assert stats["passed"] == 2
    assert stats["failed"] == 1
    assert stats["pass_rate"] == pytest.approx(0.6667, abs=1e-3)
    assert stats["avg_score"] == pytest.approx(78.3, abs=0.1)


async def test_通过率按课程分组(db):
    """同一批考核要能拆到课程维度，否则 HR 不知道「哪门课没人过」。"""
    talent = await _make_talent(db)
    easy = await _make_course(db, f"高通过课{uuid.uuid4().hex[:6]}")
    hard = await _make_course(db, f"低通过课{uuid.uuid4().hex[:6]}")

    await _make_record(db, talent=talent, course=easy, passed=True, score=92)
    await _make_record(db, talent=talent, course=easy, passed=True, score=88)
    await _make_record(db, talent=talent, course=hard, passed=False, score=55)

    service = TrainingService(db)
    easy_stats = await service.pass_rate(course_id=easy.id)
    hard_stats = await service.pass_rate(course_id=hard.id)

    assert easy_stats["pass_rate"] == 1.0
    assert hard_stats["pass_rate"] == 0.0
    assert easy_stats["by_course"][0]["examined"] == 2
    assert hard_stats["by_course"][0]["title"] == hard.title


async def test_没有考核记录时通过率为0而不是除零(db):
    service = TrainingService(db)
    stats = await service.pass_rate(course_id=999_999_999)

    assert stats["total_examined"] == 0
    assert stats["pass_rate"] == 0.0
    assert stats["by_course"] == []


async def test_整计划结课考核会落进学习记录并能被统计(db):
    """回归防线：不传 course_id 的结课考核，分数必须落到每门课的记录上。

    早期实现只在 plan.status 上留结论、不写 learning_records，
    导致「通过率」分母恒为 0 —— 接口能调通，但统计永远是 0，属于静默失效。
    """
    from app.models.training import PlanCourse, TrainingPlan
    from app.schemas.training import ScoreSubmitRequest

    talent = await _make_talent(db)
    course_a = await _make_course(db, f"结课课程A{uuid.uuid4().hex[:6]}")
    course_b = await _make_course(db, f"结课课程B{uuid.uuid4().hex[:6]}")

    plan = TrainingPlan(name=f"结课考核计划{uuid.uuid4().hex[:4]}", talent_id=talent.id, status="ongoing")
    db.add(plan)
    await db.flush()
    db.add(PlanCourse(plan_id=plan.id, course_id=course_a.id, sort=1))
    db.add(PlanCourse(plan_id=plan.id, course_id=course_b.id, sort=2))
    await db.flush()

    service = TrainingService(db)
    result = await service.submit_score(
        ScoreSubmitRequest(
            plan_id=plan.id, talent_id=talent.id, score=88, auto_retest=False
        )
    )
    assert result["passed"] is True

    stats = await service.pass_rate(plan_id=plan.id)
    assert stats["total_examined"] == 2, "计划下 2 门课各应有一条结课考核记录"
    assert stats["passed"] == 2
    assert stats["pass_rate"] == 1.0
