"""
模块：app/core/pagination.py
职责：统一分页参数与分页结果计算。

实现思路：
1. PageParams：作为 FastAPI 依赖使用（Depends(PageParams)），自动解析 page / page_size，
   并从 settings 读取默认值与上限，越界自动收敛而不是报错；
2. 提供 skip / limit 属性供 Repository 直接使用；
3. build_page_result()：把「查询结果 + 总数」组装成 PageResult，供 Service 返回；
4. 支持排序参数（order_by + order），Service 传给 Repository 时做白名单校验。

使用示例（Router）：
    @router.get("")
    async def list_users(page: PageParams = Depends()):
        items, total = await service.page_list(page)
        return success(build_page_result(items, total, page))
"""

from __future__ import annotations

import math
from typing import Any, Generic, Sequence, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.core.response import PageResult

T = TypeVar("T")

ORDER_ASC = "asc"
ORDER_DESC = "desc"


class PageParams:
    """分页 + 排序查询参数（FastAPI 依赖）。"""

    def __init__(
        self,
        page: int = Query(
            settings.DEFAULT_PAGE, ge=1, description="页码，从 1 开始"
        ),
        page_size: int = Query(
            settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE, description="每页条数"
        ),
        order_by: str | None = Query(None, description="排序字段（由各模块白名单校验）"),
        order: str = Query(ORDER_DESC, pattern="^(asc|desc)$", description="排序方向 asc/desc"),
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.order_by = order_by
        self.order = order

    # ---------------- 派生属性
    @property
    def skip(self) -> int:
        """SQL OFFSET。"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """SQL LIMIT。"""
        return self.page_size

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"PageParams(page={self.page}, page_size={self.page_size}, order_by={self.order_by}, order={self.order})"


class PageMeta(BaseModel):
    """分页元信息（可作为独立 schema 复用）。"""

    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页")
    page_size: int = Field(20, description="每页条数")
    pages: int = Field(0, description="总页数")

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def calc_pages(total: int, page_size: int) -> int:
    """计算总页数，page_size<=0 时返回 0，避免除零。"""
    if page_size <= 0:
        return 0
    return math.ceil(total / page_size)


def build_page_result(
    items: Sequence[T] | list[Any],
    total: int,
    page_params: PageParams | None = None,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> PageResult[Any]:
    """组装分页结果（PageResult 可直接塞进 success(data=...)）。"""
    p = page if page is not None else (page_params.page if page_params else settings.DEFAULT_PAGE)
    ps = (
        page_size
        if page_size is not None
        else (page_params.page_size if page_params else settings.DEFAULT_PAGE_SIZE)
    )
    return PageResult[Any](
        items=list(items),
        total=total,
        page=p,
        page_size=ps,
        pages=calc_pages(total, ps),
    )


class PageQuery(BaseModel, Generic[T]):
    """可选的泛型分页响应体：{items, total, page, page_size, pages}。"""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
