"""
模块：app/models/training.py
职责：课程、培训计划、计划-课程关联、学习记录。

标签：[Dify组] [P1]（培训表：课程 / 计划 / 进度）

============================ 实现思路与代码要点 ============================
表设计：
1. courses         课程：title、category、level、duration_hours、resource_url、
                   cover、description、skill_tags(JSON)、is_active
2. training_plans  培训计划：name、talent_id、goal、start_date、end_date、
                   status、created_by_ai、source_assessment_id
3. plan_courses    计划-课程关联：plan_id、course_id、sort、require_pass
4. learning_records 学习记录：plan_id、course_id、talent_id、progress(0-100)、
                   study_minutes、last_study_at、status、score、passed、suspicious
关键思路：
- 「测评-诊断-培训-提升-复测」闭环：培训计划由能力缺口（测评结果）驱动生成；
- 学习记录做反作弊：study_minutes 与 progress 比例异常时标记 suspicious；
- 完成率统计在 Repository 用聚合查询实现（勿在 Python 里 for 循环算）。
已完成：
- [x] 模型字段与索引（talent_id + status）
待实现清单：
- [ ] TrainingRepository：completion_rate、list_progress_by_talent
- [ ] TrainingService：AI 生成计划、进度更新、滞后预警
=========================================================================
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel, TimestampMixin


class Course(BaseModel):
    """课程。"""

    __tablename__ = "courses"
    __table_args__ = {"comment": "课程表"}

    title: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="课程名称")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="分类")
    level: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True, comment="适用层级")
    duration_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True, comment="学时"
    )
    resource_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="资源地址")
    cover: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="封面")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="简介")
    skill_tags: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="对应技能标签")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1"), comment="是否上架"
    )

    # ------------------------------------------------------------------ 关系
    plan_courses: Mapped[list["PlanCourse"]] = relationship(
        "PlanCourse", back_populates="course", cascade="all, delete-orphan", lazy="select"
    )
    learning_records: Mapped[list["LearningRecord"]] = relationship(
        "LearningRecord", back_populates="course", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Course id={self.id} title={self.title}>"


class TrainingPlan(BaseModel):
    """培训计划（由测评能力缺口驱动生成）。"""

    __tablename__ = "training_plans"
    __table_args__ = (
        Index("ix_training_plans_talent_id_status", "talent_id", "status"),
        {"comment": "培训计划表"},
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="计划名称")
    talent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="CASCADE"),
        nullable=False,
        comment="人才 ID（FK -> talents.id，CASCADE）",
    )
    goal: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="培养目标")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="开始日期")
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="结束日期")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
        comment="状态：draft/ongoing/finished/cancelled",
    )
    created_by_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"), comment="是否 AI 生成"
    )
    source_assessment_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="能力缺口来源测评 ID（逻辑外键 -> assessments.id）"
    )

    talent: Mapped["Talent"] = relationship("Talent", back_populates="training_plans")  # noqa: F821
    plan_courses: Mapped[list["PlanCourse"]] = relationship(
        "PlanCourse", back_populates="plan", cascade="all, delete-orphan", lazy="select"
    )
    learning_records: Mapped[list["LearningRecord"]] = relationship(
        "LearningRecord", back_populates="plan", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TrainingPlan id={self.id} name={self.name} status={self.status}>"


class PlanCourse(Base, TimestampMixin):
    """培训计划与课程的关联表（纯关联，无独立业务主键）。"""

    __tablename__ = "plan_courses"
    __table_args__ = (
        UniqueConstraint("plan_id", "course_id", name="uq_plan_courses_plan_course"),
        {"comment": "培训计划-课程关联表"},
    )

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        primary_key=True,
        comment="计划 ID（FK -> training_plans.id，CASCADE）",
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        comment="课程 ID（FK -> courses.id，CASCADE）",
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="学习顺序"
    )
    require_pass: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"), comment="是否必须通过考核"
    )

    plan: Mapped["TrainingPlan"] = relationship("TrainingPlan", back_populates="plan_courses")
    course: Mapped["Course"] = relationship("Course", back_populates="plan_courses")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlanCourse plan_id={self.plan_id} course_id={self.course_id}>"


class LearningRecord(BaseModel):
    """学习记录（进度、学时、考核、反作弊标记）。

    plan_id / course_id 为什么必须是 NOT NULL + CASCADE（曾经是 SET NULL）：
    MySQL 唯一索引对 NULL 不生效（NULL != NULL）。原设计里计划或课程一旦被删，
    SET NULL 会把记录变成孤儿，此时 `uq_learning_records_plan_course_talent`
    完全失效，可以无限插入重复行；`get_record` 用 scalar_one_or_none 取多行
    还会抛 MultipleResultsFound（500）。学习记录脱离计划本就没有业务含义，
    所以计划删了就一起删（CASCADE），课程软删不影响（软删不触发外键）。
    """

    __tablename__ = "learning_records"
    __table_args__ = (
        UniqueConstraint("plan_id", "course_id", "talent_id", name="uq_learning_records_plan_course_talent"),
        Index("ix_learning_records_talent_id_status", "talent_id", "status"),
        {"comment": "学习记录表"},
    )

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        nullable=False,
        comment="计划 ID（FK -> training_plans.id，CASCADE）",
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        comment="课程 ID（FK -> courses.id，CASCADE）",
    )
    talent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="CASCADE"),
        nullable=False,
        comment="人才 ID（FK -> talents.id，CASCADE）",
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="进度 0-100（单调递增）"
    )
    study_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="累计学习分钟"
    )
    last_study_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近学习时间"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_started",
        server_default=text("'not_started'"),
        comment="状态：not_started/learning/completed",
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True, comment="考核分数")
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否通过")
    examined_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="最近一次考核时间（通过率按此字段做时间筛选；为空表示从未考核）",
    )
    suspicious: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
        comment="反作弊标记（学时与进度比例异常）",
    )

    plan: Mapped["TrainingPlan | None"] = relationship("TrainingPlan", back_populates="learning_records")
    course: Mapped["Course | None"] = relationship("Course", back_populates="learning_records")
    talent: Mapped["Talent"] = relationship("Talent", back_populates="learning_records")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LearningRecord id={self.id} talent_id={self.talent_id} progress={self.progress}>"


class TrainingNotification(BaseModel):
    """培训站内信（节点 4/5/6 事件的实际落地点）。

    为什么要有这张表：事件总线在「无订阅者」时静默丢弃（只打一条 debug 日志），
    于是「把计划推送给员工」「落后提醒 HR」这些承诺此前都只是发了个没人接的事件。
    新增订阅者把事件落成站内信之后，推送才是可查、可已读、可追溯的。

    幂等键 `(plan_id, type, notify_date)`：同一计划同一天同类通知只落一条。
    没有它的话，滞后扫描重跑、页面轮询、手工补扫都会重复轰炸收件人。
    """

    __tablename__ = "training_notifications"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "type", "notify_date", name="uq_training_notifications_dedup"
        ),
        Index("ix_training_notifications_talent_type", "talent_id", "type"),
        {"comment": "培训站内信表"},
    )

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        nullable=False,
        comment="计划 ID（FK -> training_plans.id，CASCADE）",
    )
    talent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="接收人才 ID（收件人）"
    )
    type: Mapped[str] = mapped_column(
        String(48), nullable=False, comment="事件类型（training.*）"
    )
    level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="正常",
        server_default=text("'正常'"),
        comment="级别：正常 / 提醒 / HR预警",
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="标题")
    content: Mapped[str] = mapped_column(String(512), nullable=False, comment="正文")
    notify_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="通知日期（与 type 组成幂等键，避免重复推送）"
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"), comment="是否已读"
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="已读时间"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TrainingNotification id={self.id} type={self.type} plan_id={self.plan_id}>"
