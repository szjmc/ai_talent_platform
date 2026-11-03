"""
模块：app/models/base.py
职责：ORM Base 与所有业务表通用的 Mixin。

实现思路：
1. DeclarativeBase 子类 Base，统一 metadata 命名规范（索引/约束名可预测，便于 Alembic 迁移）；
2. 通用 Mixin：
     - TimestampMixin：created_at / updated_at（数据库与 ORM 双重默认值）
     - SoftDeleteMixin：is_deleted / deleted_at + 逻辑删除方法
     - OperatorMixin：created_by / updated_by（审计追踪）
     - BaseModel（推荐基类）：以上三者组合 + to_dict() 序列化 + 乐观锁 version
3. 约定：业务表一律继承 BaseModel；纯关联表可只继承 Base + TimestampMixin；
4. 逻辑删除优先：默认查询都要过滤 is_deleted=False（Repository 里统一处理）。

注意：不要在此文件中 import 任何具体业务模型，避免循环依赖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, MetaData, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# 统一的约束命名规则：迁移脚本更可读，也避免 MySQL 索引名冲突
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的根基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """未显式声明表名时，类名转蛇形复数（Talent -> talents）。"""
        name = cls.__name__
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")
        return snake if snake.endswith("s") else f"{snake}s"

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """转为普通 dict（不含内部 SQLAlchemy 状态字段）。"""
        exclude = exclude or set()
        result: dict[str, Any] = {}
        for column in self.__table__.columns:
            if column.key in exclude:
                continue
            result[column.key] = getattr(self, column.key)
        return result

    def __repr__(self) -> str:  # pragma: no cover - 调试输出
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


class TimestampMixin:
    """创建 / 更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        # 用 CURRENT_TIMESTAMP 而不是 now()：MySQL 5.6.5+ 与 8.0 都支持，
        # 避免低版本 MySQL 对「表达式默认值」的限制
        server_default=text("CURRENT_TIMESTAMP"),
        default=datetime.now,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=datetime.now,
        onupdate=datetime.now,
        comment="更新时间",
    )


class SoftDeleteMixin:
    """逻辑删除标记。"""

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
        index=True,
        comment="是否已删除（逻辑删除）",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None, comment="删除时间"
    )

    def soft_delete(self) -> None:
        """标记为已删除（实际删除动作由 Repository 触发 flush/commit）。"""
        self.is_deleted = True
        self.deleted_at = datetime.now()

    def restore(self) -> None:
        """恢复已删除记录。"""
        self.is_deleted = False
        self.deleted_at = None


class OperatorMixin:
    """数据归属与操作人（用于数据范围控制和审计）。"""

    created_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="创建人用户 ID"
    )
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="最后修改人用户 ID"
    )


class BaseModel(Base, TimestampMixin, SoftDeleteMixin, OperatorMixin):
    """业务模型推荐基类：主键 + 时间戳 + 逻辑删除 + 操作人 + 乐观锁。

    子类必须声明 `__tablename__`，并在此之上补充业务字段。
    """

    __abstract__ = True

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键 ID（BIGINT，主键索引由 PK 提供）",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="乐观锁版本号，每次更新 +1",
    )


# 便于业务模型复用的公共字段类型
def varchar(length: int = 255, comment: str | None = None):
    """VARCHAR 快捷声明。"""
    return mapped_column(String(length), nullable=False, comment=comment)


def optional_varchar(length: int = 255, comment: str | None = None):
    """可空 VARCHAR 快捷声明。"""
    return mapped_column(String(length), nullable=True, comment=comment)
