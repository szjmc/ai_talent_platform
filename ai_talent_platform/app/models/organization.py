"""
模块：app/models/organization.py
职责：组织架构（部门）表。

标签：[架构组] [P0]（组织架构表，数据权限范围的基础）

============================ 实现思路与代码要点 ============================
表设计：organizations
- id / parent_id（自关联，构建树）
- code          部门编码（唯一）
- name          部门名称（同一父级下唯一）
- level         层级（1 为根，由 Service 计算）
- path          祖级路径（如 /1/3/7/，用于「本部门及下级」范围查询，避免递归 SQL）
- leader_id     负责人（users.id）
- sort          排序号
- is_active     是否启用
- 继承 BaseModel
关键思路：
1. 树形查询优先用 path 前缀匹配（WHERE path LIKE '/1/3/%'），比递归 CTE 更简单高效；
2. 删除部门前必须校验：无子部门 + 无在职用户（Service 层规则）；
3. 组织树变更时清理缓存键 `org:tree`（infrastructure/cache）；
4. path 维护规则：新增时 path = parent.path + id + "/"；移动部门时必须级联更新子孙 path。
=========================================================================
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Organization(BaseModel):
    """组织架构（部门）。"""

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_organizations_parent_name"),
        {"comment": "组织架构表"},
    )

    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="上级部门 ID，NULL 表示根节点",
    )
    code: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="部门编码"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="部门名称")
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="层级，根为 1"
    )
    path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="/",
        server_default=text("'/'"),
        index=True,
        comment="祖级路径，如 /1/3/7/",
    )
    # 注意：这里刻意不加数据库外键，避免 organizations.leader_id 与 users.organization_id
    # 形成循环外键依赖（会导致 create_all / 迁移建表顺序问题）。业务上由 Service 校验用户存在。
    leader_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="部门负责人用户 ID（逻辑外键 -> users.id）"
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序号，越小越靠前"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1"), comment="是否启用"
    )
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")

    # ------------------------------------------------------------------ 关系
    positions: Mapped[list["Position"]] = relationship(  # noqa: F821
        "Position",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="select",
    )
    talents: Mapped[list["Talent"]] = relationship(  # noqa: F821
        "Talent", back_populates="organization", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Organization id={self.id} name={self.name}>"
