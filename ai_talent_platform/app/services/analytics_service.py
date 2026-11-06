"""
模块：app/services/analytics_service.py
职责：分析业务门面：可信范围 -> 受控 Vanna 问数 -> 报告生成 / 列表 / 下载。

标签：[Vanna组] [P0]  依赖：VannaAdapter + PgSQL 分析库 + report_renderer

============================ 实现思路与代码要点 ============================
Service 的定位：把「校验 + 外部能力 + 存储」组织成一次完整业务。
本文件提供以下业务入口：
    0. distribution / assessment_summary / trend / compare：只读ORM统计与显式对比；
    1. nl2sql_ask    ：把登录用户转成可信范围，调用 VannaAdapter.ask；
    2. create_report ：query_id复用结果，或question重新问数；在线程渲染/保存PDF与Word；
    3. list_reports / get_report_file：报告列表与下载（按归属过滤）。
约束：
- 不接受请求体传入角色或 SQL 数据范围，范围只由后端根据角色重算；
- query_id只支持有问数/导出权限的all范围用户，快照失效不会偷偷重新问数；
- 生成报告需要 analytics:export（Router与Service校验），查看/下载需要analytics:read + 归属。
=========================================================================
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from app.adapters.nl2sql.vanna_adapter import VannaAdapter
from app.adapters.nl2sql.sql_validator import checked_scope
from app.ai.context import context_scope
from app.config.settings import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import get_data_scope, has_permission
from app.core.response import PageResult
from app.schemas.analytics import NL2SQLResponse, ReportItem, ReportRequest
from app.schemas.analytics_statistics import AnalyticsFilters, ComparisonRequest, TrendQuery
from app.infrastructure.database.analytics_query import readonly_analytics_session
from app.services import analytics_snapshot, report_store
from app.services.report_renderer import ReportPayload, render_report
from app.services.analytics_statistics_service import AnalyticsStatisticsService


class AnalyticsService:
    """分析问数服务，不接受请求体传入角色或 SQL 数据范围。"""

    def __init__(self, db=None, *, adapter=None) -> None:
        # 保留公共 Service 构造约定；问数本身不使用此 MySQL 会话执行分析。
        self.db = db
        # 默认使用真实适配器，也允许调用方注入替代实现进行隔离验证。
        self.adapter = adapter or VannaAdapter()

    @asynccontextmanager
    async def _statistics(self, user, *, validate=None):
        """从登录上下文重算数据范围，一个接口中的聚合共用只读事务。"""
        roles = self._roles(user)
        with context_scope(user, roles=roles) as context:
            scope = checked_scope({"user_id": context.user_id, "roles": context.roles,
                                   "organization_id": context.organization_id})
            if validate is not None:
                validate(AnalyticsStatisticsService(None, scope))
            async with readonly_analytics_session() as db:
                yield AnalyticsStatisticsService(db, scope)

    async def distribution(self, type_: str, filters: AnalyticsFilters, user):
        """分布统计公开门面，Router/其他模块不得直接调用Repository。"""
        async with self._statistics(user, validate=lambda service: service.validate_distribution(type_, filters)) as service:
            return await service.distribution(type_, filters)

    async def assessment_summary(self, filters: AnalyticsFilters, user):
        """测评统计门面，先校验身份/筛选，再建立专用只读会话。"""
        async with self._statistics(user, validate=lambda service: service.validate_assessment(filters)) as service:
            return await service.assessment_summary(filters)

    async def trend(self, data: TrendQuery, user):
        """授权范围内的月度人才存量及同比环比，不包含流失分析。"""
        async with self._statistics(user, validate=lambda service: service.validate_trend(data)) as service:
            return await service.trend(data)

    async def compare(self, data: ComparisonRequest, user):
        """两个明确快照共用一份只读事务，避免对比期间数据源漂移。"""
        async with self._statistics(user, validate=lambda service: service.validate_comparison(data)) as service:
            return await service.compare(data)

    # ------------------------------------------------------------ 问数
    async def nl2sql_ask(self, question: str, user, *, max_rows: int = 200,
                         with_insight: bool = True, chart_type: str = "auto",
                         chart_options=None) -> NL2SQLResponse:
        """把登录用户转换为可信范围，再交给 VannaAdapter.ask。

        question：自然语言问题；user：由认证依赖取得的用户对象。
        max_rows：请求行数，Adapter 还会应用服务端硬上限。
        with_insight：是否在查询后额外调用模型生成解读。
        chart_type：图表类型，auto 交给适配器按数据推断。
        chart_options：主题 / 尺寸 / 配色，原样透传到图表配置，不参与取数。
        返回 NL2SQLResponse；缺少问数权限时抛 PermissionDeniedError。
        """
        roles = self._roles(user)
        if not has_permission(roles, "analytics:read"):
            raise PermissionDeniedError("缺少权限：analytics:read")
        # 请求期间设置 AI 上下文；退出 with 后恢复原上下文，避免不同用户串号。
        with context_scope(user, roles=roles) as context:
            # 仅传认证用户的身份、角色、部门，禁止从问题文本里推导权限。
            user_scope = {
                "user_id": context.user_id, "roles": context.roles,
                "organization_id": context.organization_id,
            }
            result = await self.adapter.ask(question, user_scope=user_scope,
                max_rows=max_rows, with_insight=with_insight,
                chart_type=chart_type, chart_options=chart_options)
            return await analytics_snapshot.save_snapshot(result, user_scope)

    # ------------------------------------------------------------ 报告
    async def create_report(self, data: ReportRequest, user) -> ReportItem:
        """生成报告：取得受权结果 → 渲染 → 保存，返回带下载地址的元数据。

        query_id模式读取本人快照，不调用模型或重查分析数据；question保留旧入口。
        渲染与保存在线程执行，避免同步绘图/文件 IO 阻塞事件循环；
        渲染失败会抛业务异常，成功后才进入保存流程，不返回缺图报告。
        """
        roles = self._roles(user)
        if not has_permission(roles, "analytics:export"):
            raise PermissionDeniedError("缺少权限：analytics:export")
        if data.query_id is not None:
            with context_scope(user, roles=roles) as context:
                result = await analytics_snapshot.load_snapshot(data.query_id, {
                    "user_id": context.user_id, "roles": context.roles,
                    "organization_id": context.organization_id,
                })
        else:
            result = await self.nl2sql_ask(
                data.question, user, max_rows=data.max_rows,
                with_insight=True, chart_type=data.chart_type,
            )
        title = (data.title or result.question).strip()[:100] or "数据分析报告"
        payload = ReportPayload(
            title=title,
            question=result.question,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
            chart=result.chart if data.include_chart else None,
            insight=result.insight_detail,
            warnings=list(result.warnings),
            author=self._author(user),
            scope=get_data_scope(self._roles(user)),
            generated_at=datetime.now(),
        )
        content = await asyncio.to_thread(render_report, payload, data.format)

        report_id = report_store.new_report_id()
        meta = report_store.build_meta(
            report_id=report_id, title=title, fmt=data.format, question=result.question,
            user=user, scope=payload.scope, author=payload.author,
        )
        if data.query_id is not None:
            meta["source_query_id"] = data.query_id
        saved = await asyncio.to_thread(report_store.save_report, content, data.format, meta)
        return self._to_item(saved)

    async def list_reports(self, page: PageParams, user) -> PageResult:
        """分页列出报告：管理员看全部，其他人只看自己生成的。

        目录扫描是同步 IO，放到线程里执行，避免阻塞事件循环。
        """
        owner_id = None if self._can_see_all(user) else user.id
        metas = await asyncio.to_thread(report_store.list_metas, owner_id)
        start = page.skip
        items = [self._to_item(meta) for meta in metas[start:start + page.limit]]
        return build_page_result(items, len(metas), page)

    async def get_report_file(self, report_id: str, user) -> tuple[Path, str]:
        """取报告文件路径与下载文件名；无权限或文件丢失时抛业务异常。"""
        meta = await asyncio.to_thread(report_store.load_meta, report_id)
        if not self._can_see_all(user) and meta.get("user_id") != user.id:
            # 不暴露“报告存在但不是你的”，统一按无权限处理
            raise PermissionDeniedError("无权下载该报告")
        path = report_store.report_file(meta)
        if not path.exists():
            raise NotFoundError("报告文件已被清理，请重新生成")
        return path, f"{self._safe_name(meta.get('title'))}.{meta.get('format', 'pdf')}"

    # ------------------------------------------------------------ 内部工具
    @staticmethod
    def _roles(user) -> list[str]:
        """取角色码；超级管理员统一规范化为 super_admin。"""
        roles = list(user.role_codes)
        return ["super_admin"] if user.is_superuser else roles

    @staticmethod
    def _can_see_all(user) -> bool:
        """管理员可查看所有人的报告。"""
        roles = set(user.role_codes)
        return bool(user.is_superuser) or bool(roles & {"super_admin", "admin"})

    @staticmethod
    def _author(user) -> str:
        """报告里的“生成人”显示名。"""
        return getattr(user, "real_name", None) or getattr(user, "username", None) or "系统用户"

    @staticmethod
    def _safe_name(title: str | None) -> str:
        """下载文件名清洗：去掉路径分隔符等非法字符。"""
        name = (title or "数据分析报告").strip()
        for char in '\\/:*?"<>|\r\n':
            name = name.replace(char, "_")
        return name[:60] or "数据分析报告"

    @staticmethod
    def _to_item(meta: dict) -> ReportItem:
        """元数据 -> 接口响应结构（补上下载地址）。"""
        return ReportItem(
            report_id=meta["report_id"],
            title=meta.get("title") or "数据分析报告",
            format=meta.get("format") or "pdf",
            size=int(meta.get("size") or 0),
            created_at=datetime.fromisoformat(meta["created_at"]),
            url=f"{settings.API_PREFIX}/analytics/reports/{meta['report_id']}/download",
        )
