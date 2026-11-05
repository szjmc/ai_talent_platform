"""
模块：app/core/exceptions.py
职责：定义全平台业务异常体系。

标签：[架构组] [P0]（业务异常体系与错误码契约，被所有组依赖）

设计思路：
1. 所有可预期错误都抛 BusinessException 子类，由 exception_handlers 统一转换为标准响应；
2. 每个异常携带 code（业务码）、message（提示语）、data（附加信息）三个要素；
3. 业务码规则：
     1xxx 通用参数/业务错误（400）
     2xxx 认证与权限（401/403）
     3xxx 资源类（404/409）
     5xxx 系统/第三方能力错误（500/502）
   同时 HTTP 状态码由 status_code 字段控制，前端既可看 HTTP 也能看 code；
4. Service 层只需 raise NotFoundError("人才不存在")，无需关心如何返回。

扩展方式：新增异常时继承 BusinessException，设置 code / status_code / 默认 message。
"""

from __future__ import annotations

from typing import Any


class BusinessException(Exception):
    """业务异常基类。"""

    code: int = 400        # 业务码
    status_code: int = 400  # HTTP 状态码
    message: str = "业务处理失败"

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        status_code: int | None = None,
        data: Any = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.code = code if code is not None else self.__class__.code
        self.status_code = status_code if status_code is not None else self.__class__.status_code
        self.data = data
        super().__init__(self.message)


# ---------------------------------------------------------------- 1xxx 参数/通用
class ParamError(BusinessException):
    """请求参数不合法（业务层二次校验失败时使用）。"""

    code = 1001
    status_code = 422
    message = "请求参数不合法"


class BusinessRuleError(BusinessException):
    """违反业务规则，例如「该人才已入职」「测评已结束」。"""

    code = 1002
    status_code = 400
    message = "业务规则校验未通过"


# ---------------------------------------------------------------- 2xxx 认证/权限
class AuthError(BusinessException):
    """认证失败（未登录、Token 无效、密码错误）。"""

    code = 2001
    status_code = 401
    message = "未认证或登录已过期"


class TokenExpiredError(AuthError):
    code = 2002
    message = "登录已过期，请重新登录"


class CredentialsError(AuthError):
    code = 2003
    message = "用户名或密码错误"


class PermissionDeniedError(BusinessException):
    """已登录但权限不足。"""

    code = 2004
    status_code = 403
    message = "权限不足，无法访问该资源"


class AccountDisabledError(AuthError):
    code = 2005
    message = "账号已被禁用，请联系管理员"


class AccountLockedError(AuthError):
    code = 2006
    message = "登录失败次数过多，账号已临时锁定"


# ---------------------------------------------------------------- 3xxx 资源
class NotFoundError(BusinessException):
    """资源不存在。"""

    code = 3001
    status_code = 404
    message = "资源不存在"


class DuplicateError(BusinessException):
    """资源重复（用户名/手机号/名称唯一约束冲突）。"""

    code = 3002
    status_code = 409
    message = "资源已存在"


class DataConflictError(BusinessException):
    """并发修改冲突（乐观锁版本号不一致）。"""

    code = 3003
    status_code = 409
    message = "数据已被其他人修改，请刷新后重试"


# ---------------------------------------------------------------- 5xxx 系统/AI
class SystemError(BusinessException):
    """系统内部错误。"""

    code = 5000
    status_code = 500
    message = "系统内部错误"


class ExternalServiceError(BusinessException):
    """外部服务（大模型、Dify、Milvus 等）调用失败。"""

    code = 5001
    status_code = 502
    message = "外部服务调用失败"


class AIError(ExternalServiceError):
    """AI 能力异常（解析失败、输出不可解析、超出配额）。"""

    code = 5002
    message = "AI 能力调用失败"


class FileError(BusinessException):
    """文件处理异常（类型不支持、超出大小、写入失败）。"""

    code = 5003
    status_code = 400
    message = "文件处理失败"
