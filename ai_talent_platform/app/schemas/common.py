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
4. 响应与分页结构统一在 core/response 定义（PageResult / ApiResponse）；
   本文件的 SuccessResponse 是 ApiResponse 的兼容别名（历史上独立定义过一份、
   合并后多个路由仍按此名称引用），二者是同一个类，不会产生契约漂移；
   COMMON_ERROR_RESPONSES 供各 router 的 responses= 参数统一声明错误响应。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")

from pydantic import BaseModel, ConfigDict, Field

from app.core.response import ApiResponse

# ------------------------------------------------------------------ 兼容层
# 各业务组路由（talent/dify/analytics 等）以 SuccessResponse[...] 声明响应模型。
# 它与 ApiResponse 是同一个类，仅保留历史导入名，勿在此之外再定义新的响应结构。
SuccessResponse = ApiResponse

# 各 router 的 APIRouter(responses=...) 统一声明：让 Swagger 展示所有接口
# 可能返回的错误码。错误体结构见 core/exception_handlers（统一 ApiResponse 包装）。
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "业务错误（code=1xxx）"},
    401: {"description": "未认证（code=2xxx）"},
    403: {"description": "权限不足（code=2004）"},
    404: {"description": "资源不存在（code=3001）"},
    422: {"description": "参数校验失败"},
    500: {"description": "系统错误（code=5000）"},
}


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


# ====================================================================== 合并兼容层
# 说明：合并 origin/develop 时恢复。develop 侧 dd42def 删除了 SuccessResponse 但未迁移完
#      引用它的 router（analytics/dify/talent/system 等十余处仍在 import），
#      该提交在 develop 上本身无法通过 import；此处按 lz_agent 原定义恢复，
#      待全仓库统一迁移 core.response.ApiResponse 后再移除。
class SuccessResponse(BaseModel, Generic[T]):
    """Swagger 响应模型：统一结构 + 泛型 data。"""

    code: int = 200
    message: str = "success"
    data: T | None = None
    trace_id: str = "-"
