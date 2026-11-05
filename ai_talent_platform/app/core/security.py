"""
模块：app/core/security.py
职责：密码加密与 JWT 签发/解析（纯工具，不依赖数据库）。

标签：[架构组] [P0]（bcrypt / JWT，安全红线）

实现思路：
1. 密码：使用 bcrypt 单向哈希（自带盐），提供 hash_password / verify_password；
   - bcrypt 明文上限 72 字节，超长先做 sha256 预哈希，避免异常；
   - 兼容历史数据：verify 时捕获格式异常返回 False，不让脏数据拖垮登录接口；
2. JWT：使用 PyJWT，access_token 携带 sub(user_id)、username、roles、type、exp、iat、jti；
   - create_access_token / create_refresh_token 分开签发；
   - decode_token 校验签名与过期，失败抛 AuthError / TokenExpiredError；
3. 所有密钥、算法、有效期来自 settings，禁止硬编码。

注意：本模块只做「密码与令牌」的纯函数能力；
     「当前登录用户」的解析放在 core/dependencies.py（需要访问数据库）。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.config.settings import settings
from app.core.exceptions import AuthError, TokenExpiredError

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


# ============================================================== 密码
def _prehash(password: str) -> bytes:
    """bcrypt 只处理 72 字节以内的密码，超长部分先 sha256 压缩为 32 字节。"""
    raw = password.encode("utf-8")
    if len(raw) > 72:
        return hashlib.sha256(raw).hexdigest().encode("utf-8")  # 64 字节，仍在限制内
    return raw


def hash_password(password: str) -> str:
    """生成密码哈希（bcrypt + 随机盐）。"""
    if not password:
        raise ValueError("密码不能为空")
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prehash(password), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与数据库哈希是否匹配。"""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_prehash(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        # 哈希格式损坏时视为校验失败（避免登录接口 500）
        return False


# ============================================================== JWT
def _create_token(
    subject: str | int,
    token_type: Literal["access", "refresh"],
    expires_minutes: int,
    extra: dict[str, Any] | None = None,
) -> str:
    """统一令牌签发逻辑（每个令牌都带唯一 jti，供吊销存储使用）。"""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        # 秒级浮点：用户级吊销（revoke_user_tokens）按 iat < cutoff 比较，
        # 浮点精度保证「吊销后立刻重新登录」的新令牌不会被误伤。
        "iat": now.timestamp(),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    user_id: int | str,
    *,
    username: str | None = None,
    roles: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """签发访问令牌（expires_minutes 用于 remember_me 自定义有效期）。"""
    payload_extra: dict[str, Any] = {"roles": roles or []}
    if username:
        payload_extra["username"] = username
    if extra:
        payload_extra.update(extra)
    return _create_token(
        user_id,
        TOKEN_TYPE_ACCESS,
        expires_minutes if expires_minutes is not None else settings.JWT_EXPIRE_MINUTES,
        payload_extra,
    )


def create_refresh_token(user_id: int | str, *, extra: dict[str, Any] | None = None) -> str:
    """签发刷新令牌（只用于换取新的 access_token）。"""
    return _create_token(user_id, TOKEN_TYPE_REFRESH, settings.JWT_REFRESH_EXPIRE_MINUTES, extra)


def decode_token(token: str, *, expected_type: str | None = TOKEN_TYPE_ACCESS) -> dict[str, Any]:
    """解析并校验令牌。

    :param token: 原始 JWT 字符串
    :param expected_type: 期望的令牌类型（None 表示不校验类型）
    :raises TokenExpiredError: 令牌过期
    :raises AuthError: 签名错误、结构非法、类型不匹配
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.PyJWTError as exc:
        raise AuthError("登录凭证无效，请重新登录") from exc

    if expected_type and payload.get("type") != expected_type:
        raise AuthError("令牌类型不匹配")
    return payload


def get_user_id_from_token(token: str, *, expected_type: str | None = TOKEN_TYPE_ACCESS) -> int:
    """从令牌中取出用户 ID（sub 为字符串，这里安全转换为 int）。"""
    payload = decode_token(token, expected_type=expected_type)
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("登录凭证无效") from exc


# ============================================================== 其它安全工具
def generate_random_token(length: int = 32) -> str:
    """生成随机字符串（用于 API Key、一次性验证码等场景）。"""
    return secrets.token_urlsafe(length)


def mask_sensitive(value: str | None, keep_left: int = 3, keep_right: int = 4) -> str:
    """敏感信息脱敏（手机号、身份证、邮箱等日志输出防泄漏）。"""
    if not value:
        return ""
    if len(value) <= keep_left + keep_right:
        return value[0] + "*" * (len(value) - 1)
    return value[:keep_left] + "*" * (len(value) - keep_left - keep_right) + value[-keep_right:]
