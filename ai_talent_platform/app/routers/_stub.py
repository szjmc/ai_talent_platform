"""
模块：app/routers/_stub.py
职责：未实现模块的占位路由工厂。

标签：[架构组] [P2]（未实现模块的 501 占位路由工厂）

为什么需要：
1. 多人并行开发时，前端需要先看到「接口清单与契约位置」，而不是 404；
2. 占位接口统一返回 501 + 明确的【主责研发组】提示，避免误以为服务挂了；
3. 主责组实现真实路由后，只需在 routers/router.py 替换一行 include 即可。

实现思路：
- placeholder_router(prefix, tag, description) 生成一个只读 GET / 与一个
  GET / 的 APIRouter，并在 OpenAPI 中带「待实现」标签；
- 不参与业务逻辑，也不做权限校验。
"""

from __future__ import annotations

import re

from fastapi import APIRouter

from app.core.response import api_response

_OWNER_RE = re.compile(r"^\[(?P<owner>[^\]]+)\]")


def placeholder_router(prefix: str, tag: str, description: str) -> APIRouter:
    """生成占位路由。"""
    router = APIRouter(prefix=f"/{prefix}", tags=[f"{tag}（待实现）"])
    # 占位说明约定以「[主责研发组] 」开头（如 "[RAG组] 简历上传……"），用于提示对接方。
    matched = _OWNER_RE.match(description.strip())
    owner = matched.group("owner") if matched else "架构组"
    hint = _OWNER_RE.sub("", description.strip()).strip()

    @router.get("", summary=f"{tag}接口占位", include_in_schema=True)
    async def _placeholder():  # noqa: D401
        """该模块尚未实现，返回 501 与实现位置说明。"""
        return api_response(
            code=501,
            message=f"{tag}模块尚未实现，主责研发组：[{owner}]",
            data={"module": tag, "owner": owner, "hint": hint},
            http_status=501,
        )

    return router
