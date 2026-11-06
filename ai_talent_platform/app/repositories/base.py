"""
模块：app/repositories/base.py
职责：Repository 通用 CRUD 基类（全平台数据访问的唯一底座）。

标签：[架构组] [P0]（BaseRepository 通用 CRUD，全平台数据访问唯一底座）

设计原则：
1. Repository 只负责「数据库怎么读写」，不做业务判断（业务在 Service）；
2. 所有方法接收 `db: AsyncSession` 作为第一个参数（由 Service 从依赖注入传入），
   不自己 commit，事务边界统一交给 get_db() / transaction()；
3. 统一支持逻辑删除（is_deleted=False），可通过 include_deleted=True 关闭过滤；
4. 提供分页、条件筛选、批量、计数、存在性判断等高频能力，避免业务 Repository 重复代码；
5. 子类只需要：定义 model、filterable_fields（可筛选字段）、searchable_fields（模糊搜索字段）、
   sortable_fields（可排序字段白名单），即可获得完整的列表查询能力。

子类示例：
    class TalentRepository(BaseRepository[Talent]):
        model = Talent
        filterable_fields = {"education", "status", "organization_id"}
        searchable_fields = {"name", "phone"}
        sortable_fields = {"created_at", "name"}

使用示例（Service）：
    repo = TalentRepository()
    talent = await repo.get_by_id(db, 1)
    page = await repo.paginate(db, page_params, filters={"status": "active"}, keyword="张")
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Generic, Sequence, TypeVar

from sqlalchemy import Select, and_, func, inspect, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DataConflictError, DuplicateError, NotFoundError
from app.core.pagination import PageParams, build_page_result
from app.core.response import PageResult
from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


@asynccontextmanager
async def unique_write_guard(db: AsyncSession, action: str) -> AsyncIterator[None]:
    """唯一键写操作守卫：SAVEPOINT 内执行写入，冲突转为 DuplicateError。

    为什么是 SAVEPOINT 而不是 db.rollback()：请求级事务（get_db）里可能已完成
    多次读写，直接 rollback 会把整个请求的成果一并丢弃（历史 bug）。begin_nested()
    只回滚到 SAVEPOINT，外层事务、已读数据与此前写入全部保留，会话继续可用。

    契约：进入守卫时会话必须没有「未 flush 的写操作」—— begin_nested 建立 SAVEPOINT
    前会先 flush 快照，脏状态会在 SAVEPOINT 之外失败而失去保护。本仓库的写方法
    都遵循「进入即干净」（每次操作立即 flush）；若契约被破坏（is_active=False），
    守卫退化为整体回滚以恢复会话可用，并仍然抛出 DuplicateError。

    用法：
        async with unique_write_guard(db, "Talent 数据已存在或违反唯一约束"):
            db.add(obj)
            await db.flush()
    """
    try:
        async with db.begin_nested():
            yield
    except IntegrityError as exc:
        txn = db.get_transaction()
        if txn is not None and not txn.is_active:
            await db.rollback()
        detail = (getattr(exc.orig, "args", None) or [exc])[0]
        raise DuplicateError(f"{action}：{detail}") from exc


class BaseRepository(Generic[ModelT]):
    """通用 CRUD 仓库。"""

    model: ClassVar[type[Base]]                    # 子类必须指定
    filterable_fields: ClassVar[set[str]] = set()  # 允许精确筛选的字段
    searchable_fields: ClassVar[set[str]] = set()  # 允许关键字模糊搜索的字段
    sortable_fields: ClassVar[set[str]] = {"id", "created_at", "updated_at"}
    default_order: ClassVar[str] = "-id"           # 默认排序，"-" 前缀表示倒序

    # ------------------------------------------------------------ 内部工具
    @classmethod
    def _model(cls) -> type[Base]:
        if getattr(cls, "model", None) is None:
            raise NotImplementedError(f"{cls.__name__} 必须声明 model 属性")
        return cls.model

    @classmethod
    def _has_column(cls, name: str) -> bool:
        return name in cls._model().__table__.columns

    @classmethod
    def _supports_soft_delete(cls) -> bool:
        return cls._has_column("is_deleted")

    def _base_query(self, *, include_deleted: bool = False) -> Select:
        """构造基础查询，默认排除逻辑删除数据。"""
        stmt = select(self._model())
        if not include_deleted and self._supports_soft_delete():
            stmt = stmt.where(self._model().is_deleted.is_(False))
        return stmt

    def _apply_filters(self, stmt: Select, filters: dict[str, Any] | None) -> Select:
        """应用精确筛选条件（只接受白名单字段，None 值忽略）。"""
        if not filters:
            return stmt
        conditions = []
        for field, value in filters.items():
            if value is None or value == "":
                continue
            if field not in self.filterable_fields or not self._has_column(field):
                continue
            column = getattr(self._model(), field)
            if isinstance(value, (list, tuple, set)):
                conditions.append(column.in_(list(value)))
            else:
                conditions.append(column == value)
        return stmt.where(and_(*conditions)) if conditions else stmt

    def _apply_keyword(self, stmt: Select, keyword: str | None) -> Select:
        """应用关键字模糊搜索（多个字段之间 OR）。"""
        if not keyword:
            return stmt
        fields = [f for f in self.searchable_fields if self._has_column(f)]
        if not fields:
            return stmt
        pattern = f"%{keyword.strip()}%"
        return stmt.where(or_(*[getattr(self._model(), f).like(pattern) for f in fields]))

    def _apply_order(self, stmt: Select, order_by: str | None, order: str = "desc") -> Select:
        """应用排序（白名单校验；非法字段回退默认排序，防止注入）。"""
        spec = order_by or self.default_order
        if order_by and order_by not in self.sortable_fields:
            spec = self.default_order
            desc_flag = True
        else:
            desc_flag = order == "desc"

        if spec and spec.startswith("-"):
            field = spec[1:]
            desc_flag = True
        elif spec and spec.startswith("+"):
            field = spec[1:]
            desc_flag = False
        else:
            field = spec

        if not field or not self._has_column(field):
            return stmt

        column = getattr(self._model(), field)
        # 排序附加主键，保证分页结果稳定
        order_clause = column.desc() if desc_flag else column.asc()
        if field != "id":
            order_clause = order_clause, getattr(self._model(), "id").desc()
            return stmt.order_by(*order_clause)
        return stmt.order_by(order_clause)

    # ------------------------------------------------------------ 查询
    async def get_by_id(
        self,
        db: AsyncSession,
        obj_id: int,
        *,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """按主键查询，不存在返回 None。"""
        stmt = self._base_query(include_deleted=include_deleted).where(
            self._model().id == obj_id
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_id_or_404(
        self,
        db: AsyncSession,
        obj_id: int,
        *,
        include_deleted: bool = False,
        message: str | None = None,
    ) -> ModelT:
        """按主键查询，不存在抛 NotFoundError（Service 层最常用）。"""
        obj = await self.get_by_id(db, obj_id, include_deleted=include_deleted)
        if obj is None:
            raise NotFoundError(message or f"{self._model().__name__} 不存在（id={obj_id}）")
        return obj

    async def get_one(
        self,
        db: AsyncSession,
        filters: dict[str, Any],
        *,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """按条件查询单条（条件必须走白名单）。"""
        stmt = self._apply_filters(
            self._base_query(include_deleted=include_deleted), filters
        ).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_field(
        self,
        db: AsyncSession,
        field: str,
        value: Any,
        *,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """按单个字段查询（如 username、code）。字段必须在白名单内。"""
        return await self.get_one(db, {field: value}, include_deleted=include_deleted)

    async def list(
        self,
        db: AsyncSession,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        order_by: str | None = None,
        order: str = "desc",
        limit: int | None = None,
        offset: int | None = None,
        include_deleted: bool = False,
        extra_where: list[Any] | None = None,
    ) -> Sequence[ModelT]:
        """条件查询列表（不含总数）。"""
        stmt = self._base_query(include_deleted=include_deleted)
        stmt = self._apply_filters(stmt, filters)
        stmt = self._apply_keyword(stmt, keyword)
        if extra_where:
            stmt = stmt.where(and_(*extra_where))
        stmt = self._apply_order(stmt, order_by, order)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        return (await db.execute(stmt)).scalars().all()

    async def list_all(
        self,
        db: AsyncSession,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        include_deleted: bool = False,
    ) -> Sequence[ModelT]:
        """查询全部（慎用，仅用于下拉选项等小数据量场景）。"""
        return await self.list(
            db, filters=filters, order_by=order_by, include_deleted=include_deleted
        )

    async def paginate(
        self,
        db: AsyncSession,
        page_params: PageParams,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
        extra_where: list[Any] | None = None,
    ) -> PageResult[Any]:
        """分页查询，返回统一 PageResult。"""
        total = await self.count(
            db,
            filters=filters,
            keyword=keyword,
            include_deleted=include_deleted,
            extra_where=extra_where,
        )
        if total == 0:
            return build_page_result([], 0, page_params)
        items = await self.list(
            db,
            filters=filters,
            keyword=keyword,
            order_by=page_params.order_by,
            order=page_params.order,
            limit=page_params.limit,
            offset=page_params.skip,
            include_deleted=include_deleted,
            extra_where=extra_where,
        )
        return build_page_result(list(items), total, page_params)

    async def count(
        self,
        db: AsyncSession,
        *,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
        extra_where: list[Any] | None = None,
    ) -> int:
        """统计记录总数。"""
        stmt = select(func.count()).select_from(self._model())
        if not include_deleted and self._supports_soft_delete():
            stmt = stmt.where(self._model().is_deleted.is_(False))
        stmt = self._apply_filters(stmt, filters)
        stmt = self._apply_keyword(stmt, keyword)
        if extra_where:
            stmt = stmt.where(and_(*extra_where))
        return int((await db.execute(stmt)).scalar() or 0)

    async def exists(
        self,
        db: AsyncSession,
        filters: dict[str, Any],
        *,
        include_deleted: bool = False,
    ) -> bool:
        """判断是否已存在符合条件的记录（唯一性校验用）。"""
        return await self.count(db, filters=filters, include_deleted=include_deleted) > 0

    # ------------------------------------------------------------ 写入
    async def create(
        self,
        db: AsyncSession,
        obj_in: ModelT | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ModelT:
        """新增一条记录。

        用法：
            repo.create(db, Talent(name="张三"))         # 传对象
            repo.create(db, {"name": "张三"})            # 传字典
            repo.create(db, name="张三")                 # 传关键字参数
        """
        if isinstance(obj_in, dict):
            obj = self._model()(**obj_in)
        elif obj_in is None:
            obj = self._model()(**kwargs)
        else:
            obj = obj_in

        async with unique_write_guard(
            db, f"{self._model().__name__} 数据已存在或违反唯一约束"
        ):
            db.add(obj)
            await db.flush()
        return obj

    async def create_many(
        self,
        db: AsyncSession,
        objs: Sequence[ModelT] | Sequence[dict[str, Any]],
    ) -> list[ModelT]:
        """批量新增（一次 flush，唯一键冲突只回滚到 SAVEPOINT，失败抛 DuplicateError）。"""
        instances: list[ModelT] = []
        for item in objs:
            instances.append(self._model()(**item) if isinstance(item, dict) else item)
        if not instances:
            return []
        async with unique_write_guard(db, "批量写入失败，存在唯一键冲突"):
            db.add_all(instances)
            await db.flush()
        return instances

    async def update(
        self,
        db: AsyncSession,
        db_obj: ModelT,
        obj_in: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ModelT:
        """更新记录（字段级增量更新，自动 +1 乐观锁版本号，SAVEPOINT 保护唯一键）。"""
        data = {**(obj_in or {}), **kwargs}
        # 修改与 flush 都放进 SAVEPOINT：冲突时内存状态随快照一起还原，外层事务保留
        async with unique_write_guard(
            db, f"{self._model().__name__} 数据已存在或违反唯一约束"
        ):
            for field, value in data.items():
                if field in {"id", "created_at", "created_by"}:
                    continue
                if hasattr(db_obj, field):
                    setattr(db_obj, field, value)
            if hasattr(db_obj, "version"):
                db_obj.version = (db_obj.version or 1) + 1
            await db.flush()
        return db_obj

    async def update_by_id(
        self,
        db: AsyncSession,
        obj_id: int,
        obj_in: dict[str, Any],
    ) -> ModelT:
        """按主键更新，不存在抛 NotFoundError。"""
        db_obj = await self.get_by_id_or_404(db, obj_id)
        return await self.update(db, db_obj, obj_in)

    async def bulk_update(
        self,
        db: AsyncSession,
        filters: dict[str, Any],
        values: dict[str, Any],
    ) -> int:
        """按条件批量更新，返回受影响行数（注意：绕过 ORM 事件，谨慎使用）。"""
        stmt = (
            update(self._model())
            .where(and_(*[getattr(self._model(), k) == v for k, v in filters.items() if self._has_column(k)]))
            .values(**values)
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)

    # ------------------------------------------------------------ 删除
    async def delete(self, db: AsyncSession, db_obj: ModelT, *, soft: bool | None = None) -> bool:
        """删除记录：支持逻辑删除（默认按模型是否支持 is_deleted 决定）。"""
        use_soft = self._supports_soft_delete() if soft is None else soft
        if use_soft:
            db_obj.soft_delete()
            await db.flush()
        else:
            await db.delete(db_obj)
            await db.flush()
        return True

    async def delete_by_id(
        self, db: AsyncSession, obj_id: int, *, soft: bool | None = None
    ) -> bool:
        """按主键删除，不存在抛 NotFoundError（保证幂等调用可感知）。"""
        db_obj = await self.get_by_id_or_404(db, obj_id)
        return await self.delete(db, db_obj, soft=soft)

    async def hard_delete(self, db: AsyncSession, db_obj: ModelT) -> bool:
        """物理删除（仅数据治理 / 测试清理使用）。"""
        await db.delete(db_obj)
        await db.flush()
        return True

    # ------------------------------------------------------------ 内部
    def check_version(self, db_obj: ModelT, expected_version: int | None) -> None:
        """乐观锁校验（可在 Service 更新前调用）。"""
        if expected_version is not None and getattr(db_obj, "version", None) != expected_version:
            raise DataConflictError()

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """返回仓库元信息（调试 / 文档用）。"""
        return {
            "repository": cls.__name__,
            "model": cls._model().__name__,
            "table": cls._model().__tablename__,
            "columns": [c.key for c in cls._model().__table__.columns],
            "filterable_fields": sorted(cls.filterable_fields),
            "searchable_fields": sorted(cls.searchable_fields),
        }
