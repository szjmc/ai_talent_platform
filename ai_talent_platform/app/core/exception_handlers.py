"""
模块：app/core/exception_handlers.py
职责：全局异常处理，把各类异常统一转换为 ApiResponse 结构。

处理矩阵：
    BusinessException          -> 使用异常自带的 code / status_code / message
    RequestValidationError     -> 422，把 Pydantic 校验错误整理成可读字段列表
    StarletteHTTPException     -> 401/404/405... 等框架异常，统一包装
    Exception（兜底）           -> 500，日志记录完整堆栈，对外隐藏内部细节

实现思路：
1. 统一在应用创建时 register_exception_handlers(app) 注册；
2. 所有 handler 都返回 core.response.api_response，保证结构一致；
3. 兜底 handler 中使用 logger.exception 打印调用栈，返回 trace_id 便于定位；
4. 业务异常按 warning 级别记录，不打印堆栈，避免日志噪音。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.logging_config import get_logger
from app.core.exceptions import BusinessException, NotFoundError
from app.core.response import CODE_SERVER_ERROR, api_response

logger = get_logger(__name__)


def _format_validation_errors(errors: list[dict]) -> list[dict]:
    """把 Pydantic 的校验错误整理为 [{field, message, type}]，方便前端直接展示。"""
    formatted: list[dict] = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", []) if p != "body"]
        formatted.append(
            {
                "field": ".".join(loc) or "body",
                "message": err.get("msg", "参数错误"),
                "type": err.get("type", ""),
            }
        )
    return formatted


def register_exception_handlers(app: FastAPI) -> None:
    """注册全部全局异常处理器（应用创建时调用一次）。"""

    @app.exception_handler(BusinessException)
    async def _business_exception_handler(request: Request, exc: BusinessException):
        # 业务异常属于预期内错误：warning 级别，不打堆栈
        logger.warning(
            "业务异常 %s %s -> code=%s msg=%s",
            request.method,
            request.url.path,
            exc.code,
            exc.message,
        )
        return api_response(
            code=exc.code,
            message=exc.message,
            data=exc.data,
            http_status=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("参数校验失败 %s %s -> %s", request.method, request.url.path, exc.errors())
        return api_response(
            code=422,
            message="请求参数校验失败",
            data={"errors": _format_validation_errors(exc.errors())},
            http_status=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        # 404/405 等框架抛出异常时，也保持 {code,message,data} 结构
        message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
        if exc.status_code == 404:
            message = message if message != "Not Found" else "接口不存在"
        return api_response(code=exc.status_code, message=message, http_status=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        # 兜底：记录完整堆栈，对外只返回通用提示
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        return api_response(
            code=CODE_SERVER_ERROR,
            message="系统繁忙，请稍后重试",
            http_status=500,
        )

    logger.info("全局异常处理器注册完成")


__all__ = ["register_exception_handlers", "NotFoundError"]
