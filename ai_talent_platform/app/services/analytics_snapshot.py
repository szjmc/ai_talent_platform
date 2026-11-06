"""Vanna查询结果快照：复用公共Redis，供页面和PDF/Word使用同一次结果。

标签：[Vanna组] [P0]（结果一致性 + 快照权限隔离）

方案A：仅支持具备analytics:read/export且data_scope=all的用户；
受限范围缺少公共的实际授权部门集合接口，不自行补造该权限能力。
保存JSON结果并绑定用户、角色、组织及分析策略。TTL复用REDIS_DEFAULT_TTL；
Redis不可用时问数降级，无进程内/文件回退，不返回虚假query_id。
读取失败、过期或权限变化时抛公共业务异常，不重新问数，不续期。
Redis读写复用NL2SQL_TIMEOUT_SECONDS作为等待上限，连接卡住也能明确降级。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from app.adapters.nl2sql.sql_validator import ALLOWED_TABLES, SAFE_FUNCTIONS, checked_scope
from app.config.logging_config import get_logger
from app.config.settings import settings
from app.core.exceptions import AIError, NotFoundError, ParamError, PermissionDeniedError
from app.core.permissions import has_permission
from app.infrastructure.cache import redis as cache
from app.schemas.analytics import NL2SQLResponse

logger = get_logger(__name__)
_VERSION = 1
_PREFIX = "analytics:snapshot:"
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _binding(user_scope: dict[str, Any]) -> dict[str, Any] | None:
    """从可信上下文复核当前权限；不是按角色名称猜测是否为管理员。"""
    scope = checked_scope(user_scope)
    if scope["data_scope"] != "all" or not has_permission(scope["roles"], "analytics:export"):
        return None
    policy = {
        "version": _VERSION,
        "tables": sorted(ALLOWED_TABLES), "functions": sorted(SAFE_FUNCTIONS),
        "host": settings.PGSQL_HOST, "port": settings.PGSQL_PORT, "database": settings.PGSQL_DB,
        "schema": settings.PGSQL_SCHEMA, "allowed_schema": settings.NL2SQL_ALLOWED_SCHEMA,
        "max_rows": settings.NL2SQL_MAX_ROWS,
    }
    # 仅保存策略摘要，不在Redis写连接密码、密钥或ORM用户对象。
    policy_hash = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    return {
        "user_id": scope["user_id"], "roles": sorted(set(scope["roles"])),
        "organization_id": scope["organization_id"], "data_scope": "all", "policy": policy_hash,
    }


async def save_snapshot(result: NL2SQLResponse, user_scope: dict[str, Any]) -> NL2SQLResponse:
    """保存规范JSON结果，成功才返回ID；失败仅添加警告，不破坏正常问数。"""
    # 同一序列化结果既返回页面又写Redis，Decimal/日期等不能在两边各自转换。
    response = NL2SQLResponse.model_validate(result.model_dump(mode="json"))
    response.query_id = None
    response.expires_at = None
    principal = _binding(user_scope)
    if principal is None:
        response.warnings.append("当前权限暂不支持查询结果快照导出，本次问数结果仍可查看")
        return response
    ttl = settings.REDIS_DEFAULT_TTL
    if cache.get_redis() is None or type(ttl) is not int or ttl <= 0:
        response.warnings.append("查询快照未保存：Redis不可用或有效期无效，暂不能按页面结果导出")
        return response
    query_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    record = {
        "version": _VERSION, "query_id": query_id, "principal": principal,
        "created_at": now.isoformat(), "expires_at": expires_at.isoformat(),
        "result": response.model_dump(mode="json"),
    }
    try:
        await asyncio.wait_for(
            cache.set_json(_PREFIX + query_id, record, ttl=ttl),
            timeout=settings.NL2SQL_TIMEOUT_SECONDS,
        )
    except Exception:
        # 不记录异常详情，避免Redis连接串或查询数据进入日志。
        logger.warning("Vanna查询快照保存失败，保留本次问数结果")
        response.warnings.append("查询快照保存失败，暂不能按页面结果导出，请稍后重新查询")
        return response
    response.query_id = query_id
    response.expires_at = expires_at
    return response


async def load_snapshot(query_id: str, user_scope: dict[str, Any]) -> NL2SQLResponse:
    """读取本人未过期快照并复核当前权限；异常时禁止回退重新查询。"""
    if not isinstance(query_id, str) or not _ID_PATTERN.fullmatch(query_id):
        raise ParamError("查询快照ID格式不正确")
    principal = _binding(user_scope)
    if principal is None:
        raise PermissionDeniedError("当前权限不支持查询快照导出")
    if cache.get_redis() is None:
        raise AIError("查询快照服务不可用，请在服务恢复后重新查询")
    try:
        record = await asyncio.wait_for(
            cache.get_json(_PREFIX + query_id), timeout=settings.NL2SQL_TIMEOUT_SECONDS,
        )
    except (ValueError, TypeError) as exc:
        raise NotFoundError("查询结果已失效，请重新查询") from exc
    except Exception as exc:
        logger.warning("Vanna查询快照读取失败")
        raise AIError("查询快照服务不可用，请稍后重试") from exc
    if not isinstance(record, dict) or record.get("version") != _VERSION or record.get("query_id") != query_id:
        raise NotFoundError("查询结果已过期或不存在，请重新查询")
    if record.get("principal") != principal:
        raise PermissionDeniedError("无权使用该查询结果或权限已变化，请重新查询")
    try:
        expires_at = datetime.fromisoformat(record["expires_at"])
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            raise NotFoundError("查询结果已过期，请重新查询")
        response = NL2SQLResponse.model_validate(record["result"])
    except (KeyError, ValueError, TypeError, ValidationError) as exc:
        raise NotFoundError("查询结果已失效，请重新查询") from exc
    response.query_id = query_id
    response.expires_at = expires_at
    return response
