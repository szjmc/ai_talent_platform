"""全局 ORM 映射关系测试：不依赖真实数据库服务。

标签：[架构组] [P1]（全局 ORM 映射验收，离线）
"""

from __future__ import annotations

from sqlalchemy.dialects import mysql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config.settings import settings
from app.models import Base
from app.models.analytics import ANALYTICS_TABLE_NAMES, AnalyticsBase
from app.models.mapping import (
    build_orm_mapping,
    render_orm_mapping_markdown,
    table_model_map,
)

EXPECTED_MYSQL_TABLES = {
    "assessment_answers",
    "assessment_questions",
    "assessment_results",
    "assessments",
    # [AI 面试] 数字人面试会话与逐轮问答
    "ai_interview_sessions",
    "ai_interview_turns",
    "audit_logs",
    "chat_messages",
    "conversations",
    "courses",
    "files",
    # [合并 origin/develop] 测评 Agent 上线后新增的三张面试表
    "interview_sessions",
    "interview_answers",
    "interview_record_sheets",
    "talent_invitations",
    "exam_sessions",
    "knowledge_chunks",
    "knowledge_docs",
    "knowledge_revisions",
    "question_bank_questions",
    "learning_records",
    "matching_records",
    "matching_batches",
    "matching_reviews",
    "matching_review_events",
    "organizations",
    "plan_courses",
    "position_requirements",
    "positions",
    "reserve_warnings",
    "resume_experiences",
    "resumes",
    "roles",
    "workflow_info",
    "talent_portraits",
    "talents",
    "training_plans",
    "training_notifications",
    "user_roles",
    "workflow_info",
    "users",
    "workflow_info",
}


# 表数不再硬编码：随合并频繁变动（lz 线 + develop 线 + 测评 Agent 各自加表），
# 由 EXPECTED_MYSQL_TABLES 与 Base.metadata 双向比对，避免「改了表忘了改数字」的假失败。
def test_all_business_tables_are_mapped():
    assert set(Base.metadata.tables) == EXPECTED_MYSQL_TABLES
    mapping = build_orm_mapping()
    assert mapping["mysql"]["table_count"] == len(EXPECTED_MYSQL_TABLES)
    assert mapping["mysql"]["table_count"] == len(table_model_map("mysql"))


def test_model_class_names_follow_convention():
    models = table_model_map("mysql")
    assert models["talents"] == "Talent"
    assert models["user_roles"] == "UserRole"
    assert models["assessment_results"] == "AssessmentResult"
    assert models["knowledge_chunks"] == "KnowledgeChunk"


def test_mysql_ddl_compiles_for_every_table():
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))
    # 约束/索引名不超过 MySQL 的 64 字符限制
    names = [c.name for t in Base.metadata.tables.values() for c in t.constraints if c.name]
    names += [i.name for t in Base.metadata.tables.values() for i in t.indexes if i.name]
    assert max(len(name) for name in names) <= 64


def test_every_foreign_key_points_to_a_mapped_table():
    known = set(Base.metadata.tables) | set(AnalyticsBase.metadata.tables)
    resolved = set()
    for table in list(Base.metadata.tables.values()) + list(AnalyticsBase.metadata.tables.values()):
        for column in table.columns:
            for fk in column.foreign_keys:
                target = fk.target_fullname.split(".")[0]
                resolved.add(target)
                assert target in known, f"{table.name}.{column.name} -> {target} 未映射"
    assert resolved


def test_relationships_are_configurable_and_symmetric():
    configure_mappers()
    for mapper in Base.registry.mappers:
        for rel in mapper.relationships:
            target_table = rel.mapper.local_table.name
            assert target_table in Base.metadata.tables
            if rel.back_populates:
                other = getattr(rel.mapper.class_, rel.back_populates)
                assert other.property.back_populates == rel.key


def test_pgsql_metadata_stays_isolated():
    assert set(AnalyticsBase.metadata.tables) == {
        f"{settings.PGSQL_SCHEMA}.{name}" for name in ANALYTICS_TABLE_NAMES
    }
    assert not set(AnalyticsBase.metadata.tables) & set(Base.metadata.tables)
    mapping = build_orm_mapping()
    assert mapping["pgsql"]["table_count"] == 6
    assert mapping["pgsql"]["partition_parents"] == [
        "fact_assessment_history",
        "fact_behavior_event",
        "fact_talent_snapshot",
        "fact_training_track",
    ]


def test_rendered_markdown_covers_every_table():
    markdown = render_orm_mapping_markdown()
    for name in EXPECTED_MYSQL_TABLES | set(ANALYTICS_TABLE_NAMES):
        assert f"`{name}`" in markdown
    assert settings.DB_NAME in markdown
    assert settings.PGSQL_DB in markdown
