"""
模块：app/core/token_revocation.py
职责：JWT 吊销存储（jti 黑名单 + 用户级强制下线），Redis 优先、进程内兜底。

标签：[架构组] [P0]（登出 / 改密后旧令牌立即失效，安全红线）

实现思路：
1. jti 级吊销：revoke_jti(jti, exp) 写入黑名单直到令牌自然过期（登出场景）；
2. 用户级吊销：revoke_user_tokens(user_id) 记录吊销时刻 cutoff，
   iat < cutoff 的存量令牌一律拒绝（改密全端下线 / 管理员踢人）；
3. 存储：REDIS_ENABLED 且客户端可用时走 Redis（多实例共享黑名单），
   否则退化为进程内 dict（单实例兜底，多 worker 部署请开启 Redis）；
4. 校验点：core/dependencies.get_current_user 与 AuthService.refresh / logout；
   decode_token 保持纯函数（只验签与过期），职责不混放。

注意：iat 使用秒级浮点时间戳，保证「吊销后立刻重新登录」的新令牌不会被误伤。
"""

from __future__ import annotations

import time
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings

logger = get_logger(__name__)

_JTI_KEY = "auth:revoked:jti:{jti}"
_USER_KEY = "auth:revoked:user:{user_id}"

# 进程内兜底存储：jti -> 过期时刻；user_id -> 吊销时刻（cutoff）
_mem_jti: dict[str, float] = {}
_mem_user: dict[int, float] = {}
_redis_warned = False


def _redis_client() -> Any | None:
    """延迟导入 Redis 客户端（未启用时返回 None）。"""
    from app.infrastructure.cache.redis import get_redis

    return get_redis()


def _prune_memory() -> None:
    """清理过期的 jti 与超出 refresh 有效期的用户 cutoff，避免内存无限增长。"""
    now = time.time()
    for key in [k for k, exp in _mem_jti.items() if exp <= now]:
        _mem_jti.pop(key, None)
    stale_before = now - settings.JWT_REFRESH_EXPIRE_MINUTES * 60
    for uid in [u for u, cutoff in _mem_user.items() if cutoff < stale_before]:
        _mem_user.pop(uid, None)


async def revoke_jti(jti: str | None, exp: float | int | None = None) -> None:
    """吊销单个令牌（按 jti），有效期对齐令牌自身的 exp。"""
    if not jti:
        return
    now = time.time()
    ttl = max(int(float(exp or 0) - now), 60) if exp else 60
    _mem_jti[str(jti)] = now + ttl
    _prune_memory()
    client = _redis_client()
    if client is not None:
        try:
            await client.set(_JTI_KEY.format(jti=jti), "1", ex=ttl)
            return
        except Exception:  # noqa: BLE001 - Redis 故障时进程内兜底仍然生效
            _warn_redis_once("jti 吊销")
    # Redis 不可用：进程内黑名单对当前 worker 生效


async def revoke_user_tokens(user_id: int | None) -> None:
    """用户级强制下线：iat 早于当前时刻的存量令牌全部失效（改密 / 踢人）。"""
    if not user_id:
        return
    cutoff = time.time()
    _mem_user[int(user_id)] = cutoff
    _prune_memory()
    client = _redis_client()
    if client is not None:
        try:
            ttl = settings.JWT_REFRESH_EXPIRE_MINUTES * 60 + 60
            await client.set(_USER_KEY.format(user_id=int(user_id)), repr(cutoff), ex=ttl)
        except Exception:  # noqa: BLE001
            _warn_redis_once("用户级吊销")


async def is_token_revoked(payload: dict[str, Any]) -> bool:
    """校验令牌是否已被吊销（jti 黑名单 or 用户级 cutoff）。"""
    jti = payload.get("jti")
    if jti and await _is_jti_revoked(str(jti)):
        return True

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return False
    cutoff = await _user_cutoff(user_id)
    if cutoff is None:
        return False
    try:
        iat = float(payload.get("iat") or 0.0)
    except (TypeError, ValueError):
        return True  # iat 缺失或非法时按已吊销处理（fail-closed）
    return iat < cutoff


async def _is_jti_revoked(jti: str) -> bool:
    if jti in _mem_jti:
        if _mem_jti[jti] > time.time():
            return True
        _mem_jti.pop(jti, None)
    client = _redis_client()
    if client is not None:
        try:
            return bool(await client.exists(_JTI_KEY.format(jti=jti)))
        except Exception:  # noqa: BLE001
            _warn_redis_once("jti 校验")
    return False


async def _user_cutoff(user_id: int) -> float | None:
    cutoff = _mem_user.get(user_id)
    client = _redis_client()
    if client is not None:
        try:
            raw = await client.get(_USER_KEY.format(user_id=user_id))
            if raw is not None:
                remote = float(raw)
                cutoff = max(cutoff or 0.0, remote) if cutoff else remote
        except Exception:  # noqa: BLE001
            _warn_redis_once("用户级校验")
    return cutoff


def _warn_redis_once(feature: str) -> None:
    global _redis_warned
    if not _redis_warned:
        logger.warning("Redis 不可用，%s 已降级为进程内黑名单（多 worker 部署请启用 Redis）", feature)
        _redis_warned = True


def reset_revocation_store() -> None:
    """清空进程内吊销记录（测试 / 定期清理用；Redis 记录按 TTL 自动过期）。"""
    _mem_jti.clear()
    _mem_user.clear()


__all__ = [
    "is_token_revoked",
    "reset_revocation_store",
    "revoke_jti",
    "revoke_user_tokens",
]
