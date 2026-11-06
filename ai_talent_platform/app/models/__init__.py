"""
模块：app/models/__init__.py
职责：ORM 模型注册中心。

标签：[架构组] [P0]（模型注册中心：create_all / Alembic 依赖全部模型被导入）

实现思路：
1. 统一在此导出 Base 及所有业务模型，保证 `Base.metadata` 包含全部表；
2. create_all / Alembic autogenerate 依赖「所有模型模块被 import 一次」，
   因此这里必须显式 import 每个模型文件；
3. 新增模型时：写 models/xxx_model.py → 在此处导入 → 加入 __all__。

注意：模型文件只定义表结构，不写查询逻辑（查询在 repositories/）。
PgSQL 分析表使用独立的 AnalyticsBase（app/models/analytics.py），
不注册进 Base.metadata，避免 MySQL 建表时混入 PgSQL 表。
"""

from app.models.base import (
    Base,
    BaseModel,
    OperatorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    optional_varchar,
    varchar,
)

# ------------------------------------------------------------------ 业务模型（MySQL）
from app.models.assessment import (  # noqa: E402
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    AssessmentResult,
    InterviewSession,
    InterviewAnswer,
    InterviewRecordSheet,
)
from app.models.audit import AuditLog  # noqa: E402
from app.models.conversation import ChatMessage, Conversation  # noqa: E402
# [合并 lz] Dify 工作流配置表：必须在此注册，否则 init_db 的 create_all 不会建 workflow_info，
# 其 dify_router / dify_service 在全新库上会报表不存在。
# 同 matching_review 一样必须显式 import，否则 Base.metadata 不含该表，create_all 与 Alembic 都会漏表。
from app.models.dify import WorkflowInfo  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.invitation import ExamSession, TalentInvitation  # noqa: E402
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc  # noqa: E402
# [合并 origin/develop] 独立题库表（测评/考试题源，与知识文档向量集合分离）
from app.models.question_bank import QuestionBankQuestion  # noqa: E402
from app.models.knowledge_revision import KnowledgeRevision  # noqa: E402  [合并 lz] 知识库版本管理
# [岗位匹配接入] 加载匹配模型（同模块注册批次快照表）；保留其他模型导入。
from app.models.matching import MatchingRecord  # noqa: E402
# *kwq* 新增（HR 复核）：【新建】matching_reviews / matching_review_events 两张表。
# 必须在此显式 import，否则 Base.metadata 不包含新表，create_all 与 Alembic
# autogenerate 都会漏掉（同事最容易踩的坑）。
from app.models.matching_review import (  # noqa: E402
    MatchingReview,
    MatchingReviewEvent,
    REVIEW_REASON_CODES,
    REVIEW_STATUSES,
    REVIEW_STATUS_LABELS,
    REVIEW_TRANSITIONS,
)
from app.models.organization import Organization  # noqa: E402
from app.models.position import Position, PositionRequirement  # noqa: E402
from app.models.resume import Resume, ResumeExperience  # noqa: E402
# [岗位匹配接入] 注册预警模型与常量，供匹配扩展接口使用。
from app.models.reserve_warning import (  # noqa: E402
    ReserveWarning,
    WARNING_TYPES,
)
from app.models.role import Role, UserRole  # noqa: E402
from app.models.talent import Talent, TalentPortrait  # noqa: E402
from app.models.training import (  # noqa: E402
    Course,
    LearningRecord,
    PlanCourse,
    TrainingNotification,
    TrainingPlan,
)
from app.models.user import User  # noqa: E402

# ------------------------------------------------------------------ 分析模型（PgSQL，独立 metadata）
from app.models.analytics import AnalyticsBase  # noqa: E402

__all__ = [
    # 基类
    "Base",
    "BaseModel",
    "TimestampMixin",
    "SoftDeleteMixin",
    "OperatorMixin",
    "varchar",
    "optional_varchar",
    # 公共层（已实现）
    "User",
    "Role",
    "UserRole",
    "Organization",
    # 岗位
    "Position",
    "PositionRequirement",
    "ReserveWarning",
    "WARNING_TYPES",
    # 人才
    "Talent",
    "TalentPortrait",
    # 简历 / 文件
    "Resume",
    "ResumeExperience",
    "File",
    # 测评
    "Assessment",
    "AssessmentQuestion",
    "AssessmentAnswer",
    "AssessmentResult",
    # 培训
    "Course",
    "TrainingPlan",
    "PlanCourse",
    "LearningRecord",
    "TrainingNotification",
    # 匹配
    "MatchingRecord",
    # 匹配复核（新建表）
    "MatchingReview",
    "MatchingReviewEvent",
    "REVIEW_STATUSES",
    "REVIEW_STATUS_LABELS",
    "REVIEW_TRANSITIONS",
    "REVIEW_REASON_CODES",
    # 知识库
    "KnowledgeDoc",
    "KnowledgeChunk",
    "KnowledgeRevision",
    "QuestionBankQuestion",
    # Dify 工作流配置（合并 origin/lz）
    "WorkflowInfo",
    # AI 会话
    "Conversation",
    "ChatMessage",
    # [合并 lz] Dify 工作流配置
    "WorkflowInfo",
    # 审计
    "AuditLog",
    # PgSQL 分析库（独立 metadata）
    "AnalyticsBase",
]
