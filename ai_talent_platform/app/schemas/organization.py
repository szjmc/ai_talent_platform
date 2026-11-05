"""
模块：app/schemas/organization.py
职责：组织架构相关请求 / 响应结构。

标签：[架构组] [P1]

实现要点：
- 树形响应使用递归模型，需要在文件末尾 model_rebuild()；
- 移动部门时只允许指定 new_parent_id，level/path 由 Service 重算。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema, ORMModel


class OrgCreate(BaseSchema):
    """创建部门。"""

    parent_id: int | None = Field(None, gt=0, description="上级部门 ID，为空表示根部门")
    code: str = Field(..., min_length=2, max_length=64, description="部门编码（唯一）")
    name: str = Field(..., min_length=2, max_length=128, description="部门名称")
    leader_id: int | None = Field(None, gt=0, description="负责人用户 ID")
    sort: int = Field(0, ge=0, description="排序号，越小越靠前")
    is_active: bool = True
    remark: str | None = Field(None, max_length=255)


class OrgUpdate(BaseSchema):
    """更新部门（code 不允许修改）。"""

    name: str | None = Field(None, min_length=2, max_length=128)
    leader_id: int | None = Field(None, gt=0)
    sort: int | None = Field(None, ge=0)
    is_active: bool | None = None
    remark: str | None = Field(None, max_length=255)


class OrgMoveRequest(BaseSchema):
    """移动部门到新的父级。"""

    new_parent_id: int | None = Field(None, description="新的上级部门 ID，为空表示移动到根")


class OrgResponse(ORMModel):
    """部门响应（列表 / 详情）。"""

    id: int
    parent_id: int | None = None
    code: str
    name: str
    level: int = 1
    path: str = "/"
    leader_id: int | None = None
    leader_name: str | None = Field(None, description="负责人姓名（Service 补充）")
    sort: int = 0
    is_active: bool = True
    remark: str | None = None
    user_count: int = Field(0, description="部门在职人数（Service 统计）")
    created_at: datetime | None = None


class OrgTreeNode(ORMModel):
    """组织树节点（递归）。"""

    id: int
    parent_id: int | None = None
    code: str
    name: str
    leader_id: int | None = None
    sort: int = 0
    is_active: bool = True
    user_count: int = 0
    children: list["OrgTreeNode"] = Field(default_factory=list)


OrgTreeNode.model_rebuild()
