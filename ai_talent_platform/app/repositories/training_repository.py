"""
模块：app/repositories/training_repository.py
职责：培训数据访问层（课程 / 计划 / 计划课程 / 学习记录 + 三个只读 Reader）。

标签：[Dify组] [P1]（培训数据访问层：课程/计划/进度）

============================ 实现思路与代码要点 ============================
本模块采用「一主三门面」结构：
    TrainingRepository          门面，一个对象拿到全部访问能力
      ├─ course                 课程库读写
      ├─ plan                   计划读写
      ├─ plan_course            计划-课程关联（复合主键，单独实现）
      ├─ record                 学习记录读写 + 通过率聚合
      ├─ talent                 [只读] 人才存在性 / 姓名 / 现职位
      ├─ position               [只读] 岗位能力模型（目标分 + 权重）
      └─ assessment             [只读] 测评维度分（gaps 兜底）

- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不在 Python 里循环统计；
- 列表页一律批量聚合，避免 N+1；
- 三个 Reader 只读别人的表，绝不写入（边界要求）。
=========================================================================
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.assessment import Assessment, AssessmentResult
from app.models.position import Position, PositionRequirement
from app.models.talent import Talent
from app.models.training import (
    Course,
    LearningRecord,
    PlanCourse,
    TrainingNotification,
    TrainingPlan,
)
from app.repositories.base import BaseRepository

# 课程未填学时的兜底值（与 training_logic.MAX_EST_HOURS 同量级）
DEFAULT_COURSE_HOURS = 8
# 视为「已学完」的进度阈值
LEARNED_PROGRESS = 100


class CourseRepository(BaseRepository):
    """课程库仓库。"""

    model = Course
    filterable_fields = {"category", "level", "is_active"}
    searchable_fields = {"title", "description"}
    sortable_fields = {"id", "title", "duration_hours", "created_at", "updated_at"}
    default_order = "-id"

    async def list_active(self, db: AsyncSession) -> list[Course]:
        """取全部上架课程（课程库量级有限，直接一次性载入内存做维度匹配）。"""
        stmt = (
            self._base_query()
            .where(Course.is_active.is_(True))
            .order_by(Course.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_by_ids(
        self, db: AsyncSession, course_ids: Sequence[int]
    ) -> list[Course]:
        """按 ID 批量取课程（保持传入顺序由调用方处理）。"""
        ids = [int(cid) for cid in course_ids if cid is not None]
        if not ids:
            return []
        stmt = self._base_query().where(Course.id.in_(ids))
        return list((await db.execute(stmt)).scalars().all())

    async def get_by_title(self, db: AsyncSession, title: str) -> Course | None:
        """按名称查（课程去重 / 幂等导入用）。"""
        stmt = (
            self._base_query()
            .where(Course.title == title)
            .order_by(Course.id.asc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_categories(self, db: AsyncSession) -> set[str]:
        """库内已有的能力维度集合（新建课程时校验维度拼写）。

        节点 3 的匹配规则是 `category` 与缺口维度**一字不差**，
        手输错一个字不会报错、只会静默降级成「库内暂无该维度课程」，
        所以创建/改名时要把不在集合内的维度记一条 warning 提示。
        """
        stmt = (
            select(Course.category)
            .where(Course.category.is_not(None), Course.is_deleted.is_(False))
            .distinct()
        )
        rows = (await db.execute(stmt)).scalars().all()
        return {str(item).strip() for item in rows if item and str(item).strip()}


class TrainingPlanRepository(BaseRepository):
    """培训计划仓库。"""

    model = TrainingPlan
    filterable_fields = {"talent_id", "status", "created_by_ai"}
    searchable_fields = {"name", "goal"}
    sortable_fields = {"id", "start_date", "end_date", "created_at", "updated_at"}
    default_order = "-id"

    async def get_with_courses(self, db: AsyncSession, plan_id: int) -> TrainingPlan | None:
        """计划 + 计划课程 + 课程实体（一次性加载，避免异步懒加载报错）。"""
        stmt = (
            self._base_query()
            .where(TrainingPlan.id == plan_id)
            .options(
                selectinload(TrainingPlan.plan_courses).selectinload(PlanCourse.course)
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_ongoing(self, db: AsyncSession) -> list[TrainingPlan]:
        """取进行中的计划（滞后扫描用）。"""
        stmt = (
            self._base_query()
            .where(TrainingPlan.status.in_(("ongoing", "draft")))
            .order_by(TrainingPlan.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def total_hours(self, db: AsyncSession, plan_id: int) -> float:
        """计划总学时（计划课程学时之和；课程未填学时时按 8h 兜底）。"""
        stmt = (
            select(
                func.coalesce(
                    func.sum(func.coalesce(Course.duration_hours, DEFAULT_COURSE_HOURS)),
                    0,
                )
            )
            .select_from(PlanCourse)
            .join(Course, Course.id == PlanCourse.course_id)
            .where(PlanCourse.plan_id == plan_id)
        )
        value = (await db.execute(stmt)).scalar_one_or_none()
        return float(value or 0)

    async def completion_rate(self, db: AsyncSession, plan_id: int) -> float:
        """计划完成率（**按学时加权**：Σ(进度×课程学时) / (计划总学时×100)）。

        为什么不用「课程进度简单平均」：8 学时课与 4 学时课不该同权。
        「8h 课学完一半 + 4h 课未开始」的实际进度是 33%，简单平均会算成 50%，
        答辩时这个口径一问就露。
        """
        total_hours = await self.total_hours(db, plan_id)
        if total_hours <= 0:
            return 0.0
        stmt = (
            select(
                func.coalesce(
                    func.sum(
                        LearningRecord.progress
                        * func.coalesce(Course.duration_hours, DEFAULT_COURSE_HOURS)
                    ),
                    0,
                )
            )
            .select_from(LearningRecord)
            .join(Course, Course.id == LearningRecord.course_id)
            .where(
                LearningRecord.plan_id == plan_id,
                LearningRecord.is_deleted.is_(False),
            )
        )
        weighted = float((await db.execute(stmt)).scalar_one() or 0)
        return round(weighted / (total_hours * 100), 4)

    async def completion_rates(
        self, db: AsyncSession, plan_ids: Sequence[int]
    ) -> dict[int, float]:
        """批量完成率（学时加权；两条聚合查询，列表页避免 N+1）。"""
        ids = [int(pid) for pid in plan_ids if pid is not None]
        if not ids:
            return {}
        hours_col = func.coalesce(Course.duration_hours, DEFAULT_COURSE_HOURS)

        # 分子：每个计划「进度 × 课程学时」之和
        weighted_stmt = (
            select(
                LearningRecord.plan_id,
                func.coalesce(func.sum(LearningRecord.progress * hours_col), 0),
            )
            .select_from(LearningRecord)
            .join(Course, Course.id == LearningRecord.course_id)
            .where(
                LearningRecord.plan_id.in_(ids),
                LearningRecord.is_deleted.is_(False),
            )
            .group_by(LearningRecord.plan_id)
        )
        weighted = {
            int(pid): float(value or 0)
            for pid, value in (await db.execute(weighted_stmt)).all()
            if pid is not None
        }

        # 分母：每个计划的课表学时之和
        total_stmt = (
            select(
                PlanCourse.plan_id,
                func.coalesce(func.sum(hours_col), 0),
            )
            .select_from(PlanCourse)
            .join(Course, Course.id == PlanCourse.course_id)
            .where(PlanCourse.plan_id.in_(ids))
            .group_by(PlanCourse.plan_id)
        )
        totals = {
            int(pid): float(value or 0)
            for pid, value in (await db.execute(total_stmt)).all()
        }

        result: dict[int, float] = {}
        for pid in ids:
            total = totals.get(pid, 0.0)
            if total <= 0:
                result[pid] = 0.0
            else:
                result[pid] = round(weighted.get(pid, 0.0) / (total * 100), 4)
        return result

    async def has_active_plan(
        self, db: AsyncSession, talent_id: int, *, exclude_plan_id: int | None = None
    ) -> TrainingPlan | None:
        """该人才是否已有进行中 / 草稿状态的计划（生成前的防重检查）。

        没有这道检查时，`POST /plans/generate` 点几次就建几份同名计划：
        计划列表出现重复项、同一门课在两条计划下各算各的完成率，
        本地库已经真实出现过「2 份同名计划」。
        """
        stmt = (
            self._base_query()
            .where(
                TrainingPlan.talent_id == talent_id,
                TrainingPlan.status.in_(("ongoing", "draft")),
                TrainingPlan.is_deleted.is_(False),
            )
            .order_by(TrainingPlan.id.desc())
        )
        if exclude_plan_id is not None:
            stmt = stmt.where(TrainingPlan.id != exclude_plan_id)
        stmt = stmt.limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def cancel(self, db: AsyncSession, plan: TrainingPlan) -> None:
        """取消计划（替换旧计划时用，保留历史但不再参与扫描与推进）。"""
        plan.status = "cancelled"
        await db.flush()

    async def batch_progress(
        self, db: AsyncSession, plan_ids: Sequence[int]
    ) -> dict[int, tuple[float, int]]:
        """批量取「计划总学时 + 累计学习分钟」（滞后扫描用，替代逐计划 3~4 次查询）。

        返回 {plan_id: (total_hours, study_minutes)}。
        """
        ids = [int(pid) for pid in plan_ids if pid is not None]
        if not ids:
            return {}
        hours_col = func.coalesce(Course.duration_hours, DEFAULT_COURSE_HOURS)

        total_stmt = (
            select(PlanCourse.plan_id, func.coalesce(func.sum(hours_col), 0))
            .select_from(PlanCourse)
            .join(Course, Course.id == PlanCourse.course_id)
            .where(PlanCourse.plan_id.in_(ids))
            .group_by(PlanCourse.plan_id)
        )
        totals = {
            int(pid): float(value or 0)
            for pid, value in (await db.execute(total_stmt)).all()
        }

        minutes_stmt = (
            select(
                LearningRecord.plan_id,
                func.coalesce(func.sum(LearningRecord.study_minutes), 0),
            )
            .where(
                LearningRecord.plan_id.in_(ids),
                LearningRecord.is_deleted.is_(False),
            )
            .group_by(LearningRecord.plan_id)
        )
        minutes = {
            int(pid): int(value or 0)
            for pid, value in (await db.execute(minutes_stmt)).all()
            if pid is not None
        }

        return {pid: (totals.get(pid, 0.0), minutes.get(pid, 0)) for pid in ids}


class PlanCourseRepository:
    """培训计划-课程关联仓库（复合主键，单独实现）。"""

    async def list_by_plan(self, db: AsyncSession, plan_id: int) -> list[PlanCourse]:
        stmt = (
            select(PlanCourse)
            .where(PlanCourse.plan_id == plan_id)
            .order_by(PlanCourse.sort.asc(), PlanCourse.course_id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def replace(
        self,
        db: AsyncSession,
        plan_id: int,
        course_ids: Sequence[int],
        *,
        weeks: Sequence[int] | None = None,
        require_pass: bool = False,
    ) -> None:
        """重置计划的课程列表（先删后插，保证顺序与唯一约束不冲突）。

        :param weeks: 与 course_ids 一一对应的排期周次；传了就写进 sort，
                      计划详情才能还原「第几周上什么课」。不传则退化为
                      学习顺序（1..n）。sort 同时承担排序与周次两个语义，
                      是因为表里没有独立的 week 列，而历史数据也按顺序读。
        """
        existing = await self.list_by_plan(db, plan_id)
        for link in existing:
            await db.delete(link)
        await db.flush()

        for index, course_id in enumerate(course_ids, start=1):
            week = None
            if weeks is not None and index - 1 < len(weeks):
                week = int(weeks[index - 1])
            db.add(
                PlanCourse(
                    plan_id=plan_id,
                    course_id=int(course_id),
                    sort=week if week and week > 0 else index,
                    require_pass=require_pass,
                )
            )
        await db.flush()

    async def has_course(self, db: AsyncSession, plan_id: int, course_id: int) -> bool:
        """该课程是否属于此计划（进度上报 / 单课考核的归属校验）。"""
        stmt = (
            select(PlanCourse.course_id)
            .where(
                PlanCourse.plan_id == plan_id,
                PlanCourse.course_id == course_id,
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    async def count(self, db: AsyncSession, plan_id: int) -> int:
        stmt = select(func.count()).select_from(PlanCourse).where(
            PlanCourse.plan_id == plan_id
        )
        return int((await db.execute(stmt)).scalar_one() or 0)


class LearningRecordRepository(BaseRepository):
    """学习记录仓库。"""

    model = LearningRecord
    filterable_fields = {"plan_id", "course_id", "talent_id", "status", "passed"}
    searchable_fields: set[str] = set()
    sortable_fields = {"id", "progress", "score", "last_study_at", "created_at"}
    default_order = "-id"

    async def get_record(
        self,
        db: AsyncSession,
        plan_id: int,
        course_id: int,
        talent_id: int,
    ) -> LearningRecord | None:
        """按「计划 + 课程 + 人才」唯一键取记录。

        用 `.limit(1).first()` 而不是 `scalar_one_or_none()`：历史上 plan_id
        允许 NULL 时唯一索引对 NULL 不生效，库里真出现过同一组 (课程, 人才)
        多条孤儿记录，scalar_one_or_none 会直接抛 MultipleResultsFound（500）。
        外键收紧为 NOT NULL 后理论上不会再有多行，这里保留防御。
        """
        stmt = (
            self._base_query()
            .where(
                LearningRecord.plan_id == plan_id,
                LearningRecord.course_id == course_id,
                LearningRecord.talent_id == talent_id,
            )
            .order_by(LearningRecord.id.asc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalars().first()

    async def list_by_plan(self, db: AsyncSession, plan_id: int) -> list[LearningRecord]:
        stmt = (
            self._base_query()
            .where(LearningRecord.plan_id == plan_id)
            .order_by(LearningRecord.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def sum_minutes(self, db: AsyncSession, plan_id: int) -> int:
        """计划累计学习分钟（聚合）。"""
        stmt = select(
            func.coalesce(func.sum(LearningRecord.study_minutes), 0)
        ).where(
            LearningRecord.plan_id == plan_id,
            LearningRecord.is_deleted.is_(False),
        )
        return int((await db.execute(stmt)).scalar_one() or 0)

    async def learned_course_ids(self, db: AsyncSession, talent_id: int) -> set[int]:
        """已学完的课程 ID（排课去重用：不给他重复排已完成的课）。

        必须限定 plan_id 非空：没有计划归属的历史孤儿记录（曾出现 26 条）
        会让「已学过」集合混入不属于任何计划的课，干扰排课。
        """
        stmt = select(LearningRecord.course_id).where(
            LearningRecord.talent_id == talent_id,
            LearningRecord.plan_id.is_not(None),
            LearningRecord.progress >= LEARNED_PROGRESS,
            LearningRecord.is_deleted.is_(False),
        )
        rows = (await db.execute(stmt)).scalars().all()
        return {int(cid) for cid in rows if cid is not None}

    async def pass_rate_stats(
        self,
        db: AsyncSession,
        *,
        start: Any | None = None,
        end: Any | None = None,
        course_id: int | None = None,
        plan_id: int | None = None,
    ) -> dict[str, Any]:
        """考核通过率聚合（节点 6 的「统计通过率」）。

        口径说明（这个口径会被问，先说清）：
        - 分母只算**已出考核结论**的记录（passed 非空）。没考的绝不进分母，
          否则「还没组织考试」会被算成「不通过」，通过率会永远难看；
        - 平均分只对 score 非空的记录求平均；
        - 时间范围按 `examined_at`（真实考核时间）。历史数据该字段为空时，
          用 created_at 兜底，避免上线早期一次性丢失全部统计。
        """
        conditions = [
            LearningRecord.is_deleted.is_(False),
            LearningRecord.passed.is_not(None),
        ]
        if start is not None:
            conditions.append(
                func.coalesce(LearningRecord.examined_at, LearningRecord.created_at) >= start
            )
        if end is not None:
            conditions.append(
                func.coalesce(LearningRecord.examined_at, LearningRecord.created_at) <= end
            )
        if course_id is not None:
            conditions.append(LearningRecord.course_id == course_id)
        if plan_id is not None:
            conditions.append(LearningRecord.plan_id == plan_id)

        passed_flag = case((LearningRecord.passed.is_(True), 1), else_=0)

        overall_stmt = select(
            func.count(LearningRecord.id),
            func.coalesce(func.sum(passed_flag), 0),
            func.avg(LearningRecord.score),
        ).where(*conditions)
        examined_raw, passed_raw, avg_raw = (await db.execute(overall_stmt)).one()

        examined = int(examined_raw or 0)
        passed = int(passed_raw or 0)
        avg_score = round(float(avg_raw), 2) if avg_raw is not None else None

        by_course: list[dict[str, Any]] = []
        if examined:
            detail_stmt = (
                select(
                    LearningRecord.course_id,
                    Course.title,
                    func.count(LearningRecord.id).label("examined"),
                    func.coalesce(func.sum(passed_flag), 0).label("passed"),
                    func.avg(LearningRecord.score).label("avg_score"),
                )
                .join(Course, Course.id == LearningRecord.course_id, isouter=True)
                .where(*conditions)
                .group_by(LearningRecord.course_id, Course.title)
                .order_by(LearningRecord.course_id.asc())
            )
            for row in (await db.execute(detail_stmt)).all():
                c_examined = int(row.examined or 0)
                c_passed = int(row.passed or 0)
                by_course.append(
                    {
                        "course_id": row.course_id,
                        "title": row.title,
                        "examined": c_examined,
                        "passed": c_passed,
                        "pass_rate": (
                            round(c_passed / c_examined, 4) if c_examined else 0.0
                        ),
                        "avg_score": (
                            round(float(row.avg_score), 2)
                            if row.avg_score is not None
                            else None
                        ),
                    }
                )

        return {
            "total_examined": examined,
            "passed": passed,
            "failed": examined - passed,
            "pass_rate": round(passed / examined, 4) if examined else 0.0,
            "avg_score": avg_score,
            "by_course": by_course,
        }

    async def upsert(
        self,
        db: AsyncSession,
        *,
        plan_id: int,
        course_id: int,
        talent_id: int,
        progress: int | None = None,
        study_minutes: int | None = None,
        suspicious: bool | None = None,
        score: float | None = None,
        passed: bool | None = None,
        examined_at: datetime | None = None,
        touch_study_time: bool = True,
    ) -> LearningRecord:
        """写入 / 更新学习记录（按 计划 + 课程 + 人才 唯一键）。

        只更新传了值的字段，避免「只提交成绩」把已有进度清零。

        `touch_study_time=False` 表示这次写入不是学习行为（比如只登记考核成绩），
        不要刷新 `last_study_at`——该字段语义是「最近学习」，不是「最近变更」。
        曾经只提交成绩也会刷新它，导致「从不学习的记录显示昨天刚学过」。

        并发安全：先查再插存在经典的竞态——两个请求同时查不到、同时插入，
        后到的那个被唯一键 `uq_learning_records_plan_course_talent` 拒绝，
        冒泡成 IntegrityError 500（学员连点两次上报就能复现）。
        这里用 SAVEPOINT 只回滚这一条插入，外层事务（比如 submit_score 的
        批量写回）不受影响，然后按「已存在」重新走更新分支。
        """
        record = await self.get_record(db, plan_id, course_id, talent_id)
        if record is None:
            record = LearningRecord(
                plan_id=plan_id,
                course_id=course_id,
                talent_id=talent_id,
                progress=0,
                study_minutes=0,
                suspicious=False,
            )
            try:
                # add 必须在 SAVEPOINT 之内：回滚时连同 session.new 里的这条一起撤销，
                # 否则失败的 pending 对象会留到下一次 flush 再次撞键。
                async with db.begin_nested():
                    db.add(record)
                    await db.flush()
            except IntegrityError:
                # 竞态落败：另一请求已插入同一 (plan, course, talent)，改为更新它。
                record = await self.get_record(db, plan_id, course_id, talent_id)
                if record is None:
                    raise

        if progress is not None:
            record.progress = int(progress)
            if record.progress >= LEARNED_PROGRESS:
                record.status = "completed"
            elif record.progress > 0:
                record.status = "learning"
            else:
                record.status = "not_started"

        if study_minutes is not None:
            record.study_minutes = int(study_minutes)
        if suspicious is not None:
            record.suspicious = bool(suspicious)
        if score is not None:
            record.score = score
        if passed is not None:
            record.passed = passed
        if examined_at is not None:
            record.examined_at = examined_at

        if touch_study_time:
            record.last_study_at = datetime.now()
        await db.flush()
        return record

    async def list_progress_by_talent(
        self, db: AsyncSession, talent_id: int
    ) -> list[dict[str, Any]]:
        """个人学习进度（记录 + 课程标题 + 计划名）。

        用内连接：学习记录一旦脱离计划就没有业务含义，不能出现在「个人学习
        进度」里（本地库曾混入 26 条 plan_id=NULL 的孤儿记录，页面出现
        「不属于任何计划的课」）。
        """
        stmt = (
            select(
                LearningRecord,
                Course.title,
                TrainingPlan.name,
            )
            .join(Course, Course.id == LearningRecord.course_id)
            .join(TrainingPlan, TrainingPlan.id == LearningRecord.plan_id)
            .where(
                LearningRecord.talent_id == talent_id,
                LearningRecord.plan_id.is_not(None),
                LearningRecord.is_deleted.is_(False),
            )
            .order_by(LearningRecord.id.desc())
        )
        rows = (await db.execute(stmt)).all()
        return [
            {
                "id": record.id,
                "plan_id": record.plan_id,
                "plan_name": plan_name,
                "course_id": record.course_id,
                "course_title": course_title,
                "progress": record.progress,
                "study_minutes": record.study_minutes,
                "status": record.status,
                "score": float(record.score) if record.score is not None else None,
                "passed": record.passed,
                "suspicious": record.suspicious,
                "examined_at": record.examined_at,
                "last_study_at": record.last_study_at,
            }
            for record, course_title, plan_name in rows
        ]


class TrainingNotificationRepository(BaseRepository):
    """培训站内信仓库（事件订阅者的落库出口 + 查询/已读）。"""

    model = TrainingNotification
    filterable_fields = {"talent_id", "plan_id", "type", "level", "is_read"}
    searchable_fields = {"title", "content"}
    sortable_fields = {"id", "notify_date", "created_at"}
    default_order = "-id"

    async def exists_today(
        self, db: AsyncSession, plan_id: int, event_type: str, notify_date: date
    ) -> bool:
        """当天同类通知是否已存在（幂等判重，避免扫描重跑造成重复轰炸）。"""
        stmt = (
            select(TrainingNotification.id)
            .where(
                TrainingNotification.plan_id == plan_id,
                TrainingNotification.type == event_type,
                TrainingNotification.notify_date == notify_date,
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    async def unread_count(self, db: AsyncSession, talent_id: int) -> int:
        stmt = select(func.count()).select_from(TrainingNotification).where(
            TrainingNotification.talent_id == talent_id,
            TrainingNotification.is_read.is_(False),
            TrainingNotification.is_deleted.is_(False),
        )
        return int((await db.execute(stmt)).scalar_one() or 0)


class TalentExistenceReader:
    """只读读取人才表（校验存在性、取姓名与现职位），不写。"""

    async def exists(self, db: AsyncSession, talent_id: int) -> bool:
        stmt = (
            select(Talent.id)
            .where(Talent.id == talent_id, Talent.is_deleted.is_(False))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    async def current_position(self, db: AsyncSession, talent_id: int) -> str | None:
        """读人才的「现职位」文本。

        为什么不用 position_id：talent 表没有岗位外键，只有 current_position 字符串。
        所以这里只能拿到文本，由调用方按 title 去 positions 表模糊匹配，匹配不上就回退默认值。
        """
        stmt = (
            select(Talent.current_position)
            .where(Talent.id == talent_id, Talent.is_deleted.is_(False))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def name(self, db: AsyncSession, talent_id: int) -> str | None:
        """读人才姓名，用于方案名称与推送文案。

        调用方未传 talent_name 时用真实姓名兜底；查不到才退回「人才{id}」，
        否则模型会把 ID 写进方案说明，看起来像没接数据库。
        """
        stmt = (
            select(Talent.name)
            .where(Talent.id == talent_id, Talent.is_deleted.is_(False))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()


class PositionRequirementReader:
    """只读读取岗位能力模型（标准来源），不写。

    标准属于岗位模块，本模块只读。拿不到就由 Service 明确降级为默认值，
    绝不在本层伪造一份「看起来像岗位模型」的数据。
    """

    async def latest(
        self, db: AsyncSession, position_id: int
    ) -> PositionRequirement | None:
        """取该岗位最新版本的能力模型（同岗位 version 递增）。"""
        stmt = (
            select(PositionRequirement)
            .where(
                PositionRequirement.position_id == position_id,
                PositionRequirement.is_deleted.is_(False),
            )
            .order_by(PositionRequirement.version.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def position_id_by_title(self, db: AsyncSession, title: str) -> int | None:
        """按岗位名称精确匹配岗位 ID（用于人才现职位的兜底关联）。"""
        name = (title or "").strip()
        if not name:
            return None
        stmt = (
            select(Position.id)
            .where(Position.title == name, Position.is_deleted.is_(False))
            .order_by(Position.id.asc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def profile(self, db: AsyncSession, position_id: int) -> dict[str, Any] | None:
        """返回可直接喂给 merge_requirement_model 的岗位模型快照。"""
        row = await self.latest(db, position_id)
        if row is None:
            return None
        return {
            "position_id": row.position_id,
            "version": row.version,
            "dimensions": row.dimensions or {},
            "weights": row.weights or {},
            "source": row.source,
        }


class AssessmentGapReader:
    """只读读取测评结果中的维度分（不修改测评模块任何代码/数据）。"""

    async def latest_scored_assessment(
        self, db: AsyncSession, talent_id: int
    ) -> Assessment | None:
        """取该人才最近一次已出分的测评。"""
        stmt = (
            select(Assessment)
            .where(
                Assessment.talent_id == talent_id,
                Assessment.status == "scored",
                Assessment.is_deleted.is_(False),
            )
            .order_by(Assessment.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def dimension_scores(
        self, db: AsyncSession, assessment_id: int
    ) -> dict[str, float]:
        """读取某次测评的维度分（{维度: 分数}）。"""
        stmt = (
            select(AssessmentResult.dimension_scores)
            .where(
                AssessmentResult.assessment_id == assessment_id,
                AssessmentResult.is_deleted.is_(False),
            )
            .limit(1)
        )
        raw = (await db.execute(stmt)).scalar_one_or_none()
        if not isinstance(raw, dict):
            return {}
        result: dict[str, float] = {}
        for key, value in raw.items():
            try:
                if isinstance(value, dict):
                    value = value.get("score", value.get("current"))
                result[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result


class TrainingRepository:
    """培训模块仓库门面：一个对象拿到 5 张表的访问能力。"""

    def __init__(self) -> None:
        self.course = CourseRepository()
        self.plan = TrainingPlanRepository()
        self.plan_course = PlanCourseRepository()
        self.record = LearningRecordRepository()
        self.notification = TrainingNotificationRepository()
        # 以下三个只读别人的表，绝不写入
        self.talent = TalentExistenceReader()
        self.position = PositionRequirementReader()
        self.assessment = AssessmentGapReader()
