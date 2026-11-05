"""PgSQL 分析库 ORM 模型。

标签：[Vanna组] [P1]（PgSQL 6 张分析表 ORM，ETL 与问数依赖；独立 AnalyticsBase）

与 MySQL 业务模型使用不同的 DeclarativeBase / MetaData：
- MySQL `Base.metadata.create_all()` 不会误建分析表；
- PgSQL 初始化脚本只创建本文件中的 6 张分析表；
- 4 张 fact 表按时间 RANGE 分区，初始化时创建 DEFAULT 分区兜底。

本模块只定义表结构，不负责 MySQL -> PgSQL 数据同步。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config.settings import settings
from app.models.base import NAMING_CONVENTION


class AnalyticsBase(DeclarativeBase):
    """PgSQL 分析库独立模型基类，不注册到 MySQL Base.metadata。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION, schema=settings.PGSQL_SCHEMA)


ANALYTICS_TABLE_NAMES: tuple[str, ...] = (
    "fact_talent_snapshot",
    "fact_assessment_history",
    "fact_training_track",
    "fact_behavior_event",
    "dim_organization",
    "dim_talent_wide",
)

# 分区父表 -> 默认分区。默认分区保证未预建时间分区时数据仍可写入。
DEFAULT_PARTITIONS: dict[str, str] = {
    "fact_talent_snapshot": "fact_talent_snapshot_default",
    "fact_assessment_history": "fact_assessment_history_default",
    "fact_training_track": "fact_training_track_default",
    "fact_behavior_event": "fact_behavior_event_default",
}

PARTITION_KEYS: dict[str, str] = {
    "fact_talent_snapshot": "snapshot_month",
    "fact_assessment_history": "scored_at",
    "fact_training_track": "event_at",
    "fact_behavior_event": "event_at",
}


class FactTalentSnapshot(AnalyticsBase):
    """人才月度快照：按月份 + 部门 + 学历 + 年龄段聚合。"""

    __tablename__ = "fact_talent_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_month", "organization_id", "education", "age_group",
            name="uq_fact_talent_snapshot_dimensions",
        ),
        CheckConstraint("talent_count >= 0", name="count_non_negative"),
        CheckConstraint("avg_work_years IS NULL OR avg_work_years >= 0", name="work_years_non_negative"),
        Index("ix_fact_talent_snapshot_org_month", "organization_id", "snapshot_month"),
        {"comment": "人才月度快照", "postgresql_partition_by": "RANGE (snapshot_month)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, comment="快照记录 ID")
    snapshot_month: Mapped[date] = mapped_column(Date, primary_key=True, comment="快照月份，取当月 1 日")
    organization_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="部门 ID，0 表示全部门汇总")
    education: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default=text("''"), comment="学历，空字符串表示不限")
    age_group: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default=text("''"), comment="年龄段，空字符串表示不限")
    talent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"), comment="人才数量")
    avg_work_years: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, comment="平均工作年限")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class FactAssessmentHistory(AnalyticsBase):
    """测评历史明细。"""

    __tablename__ = "fact_assessment_history"
    __table_args__ = (
        CheckConstraint("total_score IS NULL OR (total_score >= 0 AND total_score <= 100)", name="score_range"),
        Index("ix_fact_assessment_history_talent_time", "talent_id", "scored_at"),
        Index("ix_fact_assessment_history_assessment", "assessment_id"),
        {"comment": "测评历史明细", "postgresql_partition_by": "RANGE (scored_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, comment="历史记录 ID")
    assessment_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="MySQL 测评任务 ID")
    talent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="人才 ID")
    position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="岗位 ID")
    total_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, comment="总分，0-100")
    level: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="能力等级")
    dimension_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"), comment="维度得分")
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, comment="评分时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="入库时间")


class FactTrainingTrack(AnalyticsBase):
    """培训学习轨迹。"""

    __tablename__ = "fact_training_track"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint("study_minutes >= 0", name="minutes_non_negative"),
        Index("ix_fact_training_track_talent_time", "talent_id", "event_at"),
        Index("ix_fact_training_track_course", "course_id"),
        {"comment": "培训学习轨迹", "postgresql_partition_by": "RANGE (event_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, comment="轨迹记录 ID")
    talent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="人才 ID")
    course_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="课程 ID")
    plan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="培训计划 ID")
    progress: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default=text("0"), comment="学习进度百分比")
    study_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"), comment="累计学习分钟数")
    status: Mapped[str] = mapped_column(String(32), nullable=False, comment="学习状态")
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, comment="轨迹事件时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="入库时间")


class FactBehaviorEvent(AnalyticsBase):
    """用户行为时序事件。"""

    __tablename__ = "fact_behavior_event"
    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        Index("ix_fact_behavior_event_talent_time", "talent_id", "event_at"),
        Index("ix_fact_behavior_event_type_time", "event_type", "event_at"),
        {"comment": "用户行为时序事件", "postgresql_partition_by": "RANGE (event_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, comment="事件 ID")
    talent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="人才 ID")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件类型")
    page: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="页面或资源路径")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="停留时长，毫秒")
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, comment="事件发生时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="入库时间")


class DimOrganization(AnalyticsBase):
    """部门维表，供分析查询避免跨库 JOIN。"""

    __tablename__ = "dim_organization"
    __table_args__ = (
        UniqueConstraint("code", name="uq_dim_organization_code"),
        CheckConstraint("level >= 1", name="level_positive"),
        Index("ix_dim_organization_parent", "parent_id"),
        {"comment": "部门维表"},
    )

    org_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, comment="MySQL 部门 ID")
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="部门编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="部门名称")
    level: Mapped[int] = mapped_column(Integer, nullable=False, comment="部门层级")
    path: Mapped[str] = mapped_column(String(512), nullable=False, comment="祖先路径")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="上级部门 ID")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="最近同步时间")


class DimTalentWide(AnalyticsBase):
    """人才分析宽表，供统计与 NL2SQL 查询。"""

    __tablename__ = "dim_talent_wide"
    __table_args__ = (
        CheckConstraint("work_years IS NULL OR work_years >= 0", name="work_years_non_negative"),
        CheckConstraint("match_avg_score IS NULL OR (match_avg_score >= 0 AND match_avg_score <= 100)", name="match_score_range"),
        Index("ix_dim_talent_wide_education", "education"),
        Index("ix_dim_talent_wide_potential", "potential_level"),
        {"comment": "人才分析宽表"},
    )

    talent_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, comment="MySQL 人才 ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="人才姓名")
    org_path: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="所属部门路径")
    education: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="最高学历")
    work_years: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, comment="工作年限")
    skill_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]"), comment="技能标签")
    potential_level: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="潜力等级")
    match_avg_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, comment="平均匹配分")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="最近同步时间")


__all__ = [
    "AnalyticsBase",
    "ANALYTICS_TABLE_NAMES",
    "DEFAULT_PARTITIONS",
    "PARTITION_KEYS",
    "FactTalentSnapshot",
    "FactAssessmentHistory",
    "FactTrainingTrack",
    "FactBehaviorEvent",
    "DimOrganization",
    "DimTalentWide",
]
