"""
模块：app/core/response.py
职责：统一响应格式（全平台接口对外结构唯一来源）。

标签：[架构组] [P0]（统一响应结构契约，被所有 Router 依赖）

结构：
    {
      "code": 200,            # 业务状态码，与 HTTP 状态码同源，0/200 表示成功
      "message": "success",   # 面向前端/用户的提示语
      "data": {...},          # 业务数据，可为 null / 对象 / 数组
      "trace_id": "..."       # 请求链路 ID，便于日志排查
    }

实现思路：
1. ApiResponse 继承 Pydantic BaseModel 且支持泛型，Swagger 能正确展示 data 结构；
2. success() / error() 两个工厂方法覆盖 90% 场景，Router 直接 return success(data=...)；
3. PageResult 作为分页 data 的标准结构，配合 core/pagination.py 使用；
4. 为了保证「响应一定带 trace_id」，由 middleware 在 Response 上写入请求头，此处从 contextvar 读取。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config.logging_config import trace_id_ctx

T = TypeVar("T")

# 业务状态码（与 HTTP 状态码保持一致，便于前端统一处理；业务细分码在 exceptions 中扩展）
CODE_SUCCESS = 200
CODE_BAD_REQUEST = 400
CODE_UNAUTHORIZED = 401
CODE_FORBIDDEN = 403
CODE_NOT_FOUND = 404
CODE_CONFLICT = 409
CODE_UNPROCESSABLE = 422
CODE_SERVER_ERROR = 500


class PageResult(BaseModel, Generic[T]):
    """分页数据结构。"""

    items: list[T] = Field(default_factory=list, description="当前页数据")
    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页码，从 1 开始")
    page_size: int = Field(20, description="每页条数")
    pages: int = Field(0, description="总页数")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应模型。"""

    code: int = Field(CODE_SUCCESS, description="业务状态码，200 表示成功")
    message: str = Field("success", description="提示信息")
    data: T | None = Field(None, description="业务数据")
    trace_id: str = Field("-", description="请求链路 ID")

    @classmethod
    def success(
        cls,
        data: Any = None,
        message: str = "success",
        code: int = CODE_SUCCESS,
    ) -> "ApiResponse[Any]":
        """成功响应工厂。"""
        return ApiResponse[Any](code=code, message=message, data=data, trace_id=trace_id_ctx.get())

    @classmethod
    def error(
        cls,
        message: str = "error",
        code: int = CODE_BAD_REQUEST,
        data: Any = None,
    ) -> "ApiResponse[Any]":
        """失败响应工厂。"""
        return ApiResponse[Any](code=code, message=message, data=data, trace_id=trace_id_ctx.get())


def success(data: Any = None, message: str = "success", code: int = CODE_SUCCESS) -> ApiResponse[Any]:
    """函数式快捷方法：`return success(data=obj, message="创建成功")`。"""
    return ApiResponse.success(data=data, message=message, code=code)


def error(message: str = "error", code: int = CODE_BAD_REQUEST, data: Any = None) -> ApiResponse[Any]:
    """函数式快捷方法：`return error("参数错误", code=400)`。"""
    return ApiResponse.error(message=message, code=code, data=data)


def api_response(
    *,
    code: int = CODE_SUCCESS,
    message: str = "success",
    data: Any = None,
    http_status: int | None = None,
) -> JSONResponse:
    """直接构造 JSONResponse（异常处理器等非建模场景使用）。

    http_status 默认取 code，保证 HTTP 层与业务码一致；如需「HTTP 200 + 业务错误码」可显式传入。
    """
    payload = ApiResponse[Any](
        code=code, message=message, data=data, trace_id=trace_id_ctx.get()
    ).model_dump()
    return JSONResponse(status_code=http_status or code, content=payload)
