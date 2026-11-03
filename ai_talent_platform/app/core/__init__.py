"""
模块：app/core/__init__.py
职责：核心公共层出口。

包含：
    application.py         应用工厂
    lifespan.py            生命周期（启动/关闭）
    dependencies.py        依赖注入（数据库、当前用户、权限）
    security.py            密码哈希 / JWT
    permissions.py         RBAC 权限与数据范围
    exceptions.py          业务异常体系
    exception_handlers.py  全局异常处理
    middleware.py          中间件
    response.py            统一响应
    pagination.py          分页

注意：为避免循环导入，此处只导出最常用的对象，其余按需从子模块导入。
"""

from app.core.exceptions import (
    AuthError,
    BusinessException,
    CredentialsError,
    DuplicateError,
    NotFoundError,
    ParamError,
    PermissionDeniedError,
)
from app.core.pagination import PageParams, build_page_result
from app.core.response import ApiResponse, PageResult, error, success

__all__ = [
    "BusinessException",
    "ParamError",
    "AuthError",
    "CredentialsError",
    "PermissionDeniedError",
    "NotFoundError",
    "DuplicateError",
    "ApiResponse",
    "PageResult",
    "success",
    "error",
    "PageParams",
    "build_page_result",
]
