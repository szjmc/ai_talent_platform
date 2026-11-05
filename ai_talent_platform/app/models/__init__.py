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
)
from app.models.audit import AuditLog  # noqa: E402
from app.models.conversation import ChatMessage, Conversation  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc  # noqa: E402
from app.models.matching import MatchingRecord  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.position import Position, PositionRequirement  # noqa: E402
from app.models.resume import Resume, ResumeExperience  # noqa: E402
from app.models.role import Role, UserRole  # noqa: E402
from app.models.talent import Talent, TalentPortrait  # noqa: E402
from app.models.training import (  # noqa: E402
    Course,
    LearningRecord,
    PlanCourse,
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
    # 匹配
    "MatchingRecord",
    # 知识库
    "KnowledgeDoc",
    "KnowledgeChunk",
    # AI 会话
    "Conversation",
    "ChatMessage",
    # 审计
    "AuditLog",
    # PgSQL 分析库（独立 metadata）
    "AnalyticsBase",
]
