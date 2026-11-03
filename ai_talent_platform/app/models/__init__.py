"""
模块：app/models/__init__.py
职责：ORM 模型注册中心。

实现思路：
1. 统一在此导出 Base 及所有业务模型，保证 `Base.metadata` 包含全部表；
2. create_all / Alembic autogenerate 依赖「所有模型模块被 import 一次」，
   因此这里必须显式 import 每个模型文件；
3. 新增模型时：写 models/xxx_model.py → 在此处导入 → 加入 __all__。

注意：模型文件只定义表结构，不写查询逻辑（查询在 repositories/）。
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

# ------------------------------------------------------------------ 业务模型
# 说明：以下模型文件当前为「带完整实现思路注释的骨架」，公共层完成后逐个补全。
# 已实现的模型会在此处被导入并参与建表；未实现的模型保持注释状态，避免启动报错。
from app.models.organization import Organization  # noqa: E402
from app.models.role import Role, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

__all__ = [
    "Base",
    "BaseModel",
    "TimestampMixin",
    "SoftDeleteMixin",
    "OperatorMixin",
    "varchar",
    "optional_varchar",
    "User",
    "Role",
    "UserRole",
    "Organization",
]
