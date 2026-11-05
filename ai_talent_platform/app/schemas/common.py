"""
模块：app/schemas/common.py
职责：通用数据结构（ID、状态、排序、批量操作等）。

标签：[架构组] [P1]（跨业务通用 Schema：ID / 排序 / 批量）

实现思路：
1. 这里只放「跨业务复用」的 Schema，具体业务 Schema 放各自文件；
2. 所有 Schema 统一 model_config = ConfigDict(from_attributes=True, populate_by_name=True)，
   便于直接由 ORM 对象构造响应；
3. 常用基类：
     ORMModel     读模型基类（from_attributes）
     BaseSchema   写模型基类（禁止额外字段，保证入参严格）
4. 响应与分页结构不在此重复定义（历史上的 PageData / PageQuerySchema / SuccessResponse
   已删除）：分页统一 core/pagination.PageParams + core/response.PageResult，
   Swagger 响应包装统一 core/response.ApiResponse，避免多套契约漂移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """响应模型基类：支持从 ORM 对象直接构造。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class BaseSchema(BaseModel):
    """请求模型基类：默认拒绝未知字段（防止前端多传字段被静默忽略）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IDSchema(BaseModel):
    """通用 ID 参数。"""

    id: int = Field(..., gt=0, description="主键 ID")


class IDsSchema(BaseModel):
    """批量操作 ID 列表。"""

    ids: list[int] = Field(..., min_length=1, description="主键 ID 列表")


class StatusUpdateSchema(BaseSchema):
    """通用启用/禁用请求。"""

    is_active: bool = Field(..., description="是否启用")


class SortSchema(BaseSchema):
    """通用排序参数。"""

    order_by: str | None = Field(None, description="排序字段")
    order: str = Field("desc", pattern="^(asc|desc)$", description="排序方向")


class TimestampSchema(ORMModel):
    """含时间戳的响应字段（可被业务响应模型继承）。"""

    created_at: datetime | None = Field(None, description="创建时间")
    updated_at: datetime | None = Field(None, description="更新时间")


class OperationResult(ORMModel):
    """通用操作结果。"""

    success: bool = True
    message: str = "操作成功"
    affected: int = 0


class OptionItem(ORMModel):
    """下拉选项。"""

    label: str
    value: Any
    extra: dict[str, Any] | None = None


# 常见响应声明，Router 中使用 responses={...} 提升文档质量
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "业务错误（code=1xxx）"},
    401: {"description": "未认证（code=2xxx）"},
    403: {"description": "权限不足（code=2004）"},
    404: {"description": "资源不存在（code=3001）"},
    422: {"description": "参数校验失败"},
    500: {"description": "系统错误（code=5000）"},
}
