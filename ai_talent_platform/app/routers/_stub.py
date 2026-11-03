"""
模块：app/routers/_stub.py
职责：未实现模块的占位路由工厂。

为什么需要：
1. 多人并行开发时，前端需要先看到「接口清单与契约位置」，而不是 404；
2. 占位接口统一返回 501 + 明确的负责人提示，避免误以为服务挂了；
3. 模块负责人实现真实路由后，只需在 routers/router.py 替换一行 include 即可。

实现思路：
- placeholder_router(prefix, tag, description) 生成一个只读 GET / 与一个
  GET /_todo 的 APIRouter，并在 OpenAPI 中带「待实现」标签；
- 不参与业务逻辑，也不做权限校验。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.response import api_response


def placeholder_router(prefix: str, tag: str, description: str) -> APIRouter:
    """生成占位路由。"""
    router = APIRouter(prefix=f"/{prefix}", tags=[f"{tag}（待实现）"])

    @router.get("", summary=f"{tag}接口占位", include_in_schema=True)
    async def _placeholder():  # noqa: D401
        """该模块尚未实现，返回 501 与实现位置说明。"""
        return api_response(
            code=501,
            message=f"{tag}模块尚未实现，请对接负责人",
            data={"module": tag, "hint": description},
            http_status=501,
        )

    return router
