"""
模块：app/services/analytics_service.py
职责：数据分析业务服务层（概览 / 分布 / 趋势 / NL2SQL 问数 / 报告导出）。

标签：[Vanna组] [P0]  依赖：VannaAdapter + PgSQL 分析库（问数 / 看板 / 导出）

统计口径（与 docs/api_document.md 十六节一致，避免各接口口径漂移）：
- 人才总数        在职+储备且未逻辑删除；PgSQL dim_talent_wide 优先，空则回退 MySQL
- 在招岗位        positions.status='open'（MySQL）
- 匹配率          近 90 天匹配记录中 score>=60 的占比（0-1，MySQL）
- 培训完成率      fact_training_track completed/总数（0-1），回退 learning_records
- 平均测评分      fact_assessment_history avg(total_score)，回退已提交 assessments
- 月度趋势(概览)  近 6 个月人才总数（fact_talent_snapshot 口径；回退 MySQL 新增口径并注记）
- 分布(教育/技能) dim_talent_wide 按人计数；分布(年龄) fact_talent_snapshot 年龄段；
  分布(部门)      fact_talent_snapshot 最新月按部门（回退 MySQL 直查）
- 趋势(talent_new/turnover/match) MySQL 直查小聚合（离职以状态更新月近似，
                  模型无 left_at 字段）；受限身份自动附加行级部门过滤

数据通道：PgSQL 由 scripts/sync_analytics.py 幂等同步（6 张分析表）；
PgSQL 未启用/未同步时自动降级 MySQL 并在 data_notes/note 中说明（见 settings 注释）。

已完成：
    - [x] 首页概览 / 分布统计 / 趋势分析 / NL2SQL 问数 / 报告生成（+ 任务列表）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.nl2sql.sql_validator import inject_data_scope
from app.adapters.nl2sql.vanna_adapter import (
    VannaAdapter,
    build_scope_clause,
    is_scope_unrestricted,
)
from app.config.settings import settings
from app.core.exceptions import ParamError
from app.infrastructure.database.postgres import pg_session
from app.models.user import User
from app.schemas.analytics import (
    DashboardOverview,
    DistributionItem,
    DistributionResponse,
    MonthlyTrendPoint,
    NL2SQLRequest,
    NL2SQLResponse,
    ReportRequest,
    ReportTask,
    TrendPoint,
    TrendResponse,
)

logger = logging.getLogger(__name__)

_DIST_TYPES = ("education", "age", "skill", "org")
_TREND_METRICS = ("talent_new", "turnover", "match")

# 报告任务注册表（进程内；文件落 UPLOAD_DIR/reports，文件中心就绪后转为下载地址）
_REPORT_TASKS: "OrderedDict[str, ReportTask]" = OrderedDict()
_REPORT_HANDLES: dict[str, asyncio.Task] = {}
_MAX_REPORT_TASKS = 100


def scope_for_user(user: User | None) -> dict[str, Any]:
    """User -> 数据范围字典（与 sql_query 技能的 user_scope 同形）。"""
    if user is None:
        return {}
    return {
        "user_id": user.id,
        "roles": list(user.role_codes),
        "organization_id": user.organization_id,
    }


def reset_report_tasks() -> None:
    """清空报告任务注册表（测试 / 定期清理用）。"""
    for handle in list(_REPORT_HANDLES.values()):
        handle.cancel()
    _REPORT_HANDLES.clear()
    _REPORT_TASKS.clear()


class AnalyticsService:
    """数据分析业务服务（看板统计走 PgSQL 分析库，业务小聚合走 MySQL）。"""

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db  # MySQL 会话（在招岗位 / 匹配率 / 降级口径 / 趋势直查）

    # ================================================================ 取数基础设施
    async def _pg_rows(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """PgSQL 查询；未启用返回 []（调用方降级）。"""
        async with pg_session() as pg:
            if pg is None:
                return []
            result = await pg.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result.all()]

    async def _mysql_rows(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        result = await self.db.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result.all()]

    async def _mysql_scalar(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> float | None:
        rows = await self._mysql_rows(sql, params)
        if not rows:
            return None
        value = next(iter(rows[0].values()), None)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def _pg_scalar(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> float | None:
        rows = await self._pg_rows(sql, params)
        if not rows:
            return None
        value = next(iter(rows[0].values()), None)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _pg_exec_sql(
        self, sql: str, scope: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        """在 SQL 上注入数据范围（受限身份 fail-closed），返回可执行 SQL。"""
        clause = build_scope_clause(sql, scope)
        if not clause:
            return sql, {}
        return inject_data_scope(sql, clause), {}

    def _org_filter(
        self, scope: dict[str, Any] | None, column: str = "organization_id"
    ) -> tuple[str, dict[str, Any]]:
        """MySQL 侧行级过滤片段（受限身份 -> AND column = :scope_org）。"""
        if is_scope_unrestricted(scope) or not scope:
            return "", {}
        org_id = (scope or {}).get("organization_id")
        if not isinstance(org_id, int) or org_id <= 0:
            raise ParamError("数据范围受限：当前身份缺少部门信息，无法执行分析查询")
        return f" AND {column} = :scope_org", {"scope_org": org_id}

    # ================================================================ 概览
    async def overview(self, scope: dict[str, Any] | None = None) -> DashboardOverview:
        """首页驾驶舱概览（PgSQL 优先，逐指标降级并注记）。"""
        scope = scope or {}
        notes: list[str] = []

        # 1) 人才总数：dim_talent_wide 优先（ETL 后=在职+储备）；空/未启用回退 MySQL
        pg_sql, _ = self._pg_exec_sql("SELECT COUNT(*) AS c FROM dim_talent_wide", scope)
        total = await self._pg_scalar(pg_sql)
        if not total:
            org_sql, org_params = self._org_filter(scope)
            mysql_total = await self._mysql_scalar(
                "SELECT COUNT(*) AS c FROM talents "
                "WHERE is_deleted = 0 AND status IN ('active', 'reserve')" + org_sql,
                org_params,
            )
            if mysql_total is not None:
                total = mysql_total
                notes.append("分析库未同步：人才总数回退 MySQL 口径")
            else:
                total = total or 0.0

        # 2) 在招岗位（MySQL）
        org_sql, org_params = self._org_filter(scope)
        active_positions = int(
            await self._mysql_scalar(
                "SELECT COUNT(*) AS c FROM positions "
                "WHERE is_deleted = 0 AND status = 'open'" + org_sql,
                org_params,
            )
            or 0
        )

        # 3) 匹配率：近 90 天 score>=60 占比（受限 -> 岗位子查询过滤）
        if is_scope_unrestricted(scope) or not scope:
            match_sql = (
                "SELECT AVG(CASE WHEN score >= 60 THEN 1.0 ELSE 0.0 END) AS c "
                "FROM matching_records "
                "WHERE COALESCE(matched_at, created_at) >= "
                "DATE_SUB(NOW(), INTERVAL 90 DAY)"
            )
            match_params: dict[str, Any] = {}
        else:
            match_sql = (
                "SELECT AVG(CASE WHEN score >= 60 THEN 1.0 ELSE 0.0 END) AS c "
                "FROM matching_records "
                "WHERE COALESCE(matched_at, created_at) >= "
                "DATE_SUB(NOW(), INTERVAL 90 DAY) "
                "AND position_id IN (SELECT id FROM positions "
                "WHERE organization_id = :scope_org)"
            )
            match_params = {"scope_org": scope.get("organization_id")}
        match_rate = await self._mysql_scalar(match_sql, match_params)
        if match_rate is None:
            match_rate = 0.0
            notes.append("近 90 天无匹配记录，匹配率记 0")

        # 4) 培训完成率：PgSQL fact_training_track -> MySQL learning_records
        pg_sql, _ = self._pg_exec_sql(
            "SELECT COALESCE(AVG(CASE WHEN status = 'completed' THEN 1.0 ELSE 0.0 END), 0) AS c "
            "FROM fact_training_track",
            scope,
        )
        training = await self._pg_scalar(pg_sql)
        if not training:
            if is_scope_unrestricted(scope) or not scope:
                training_sql = (
                    "SELECT AVG(CASE WHEN status = 'completed' THEN 1.0 ELSE 0.0 END) AS c "
                    "FROM learning_records"
                )
                training_params: dict[str, Any] = {}
            else:
                training_sql = (
                    "SELECT AVG(CASE WHEN status = 'completed' THEN 1.0 ELSE 0.0 END) AS c "
                    "FROM learning_records WHERE talent_id IN "
                    "(SELECT id FROM talents WHERE is_deleted = 0 AND organization_id = :scope_org)"
                )
                training_params = {"scope_org": scope.get("organization_id")}
            training = await self._mysql_scalar(training_sql, training_params)
            if training is None:
                training = 0.0
                notes.append("无培训数据，培训完成率记 0")

        # 5) 平均测评分：PgSQL fact_assessment_history -> MySQL assessments
        pg_sql, _ = self._pg_exec_sql(
            "SELECT AVG(total_score) AS c FROM fact_assessment_history", scope
        )
        score = await self._pg_scalar(pg_sql)
        if score is None:
            if is_scope_unrestricted(scope) or not scope:
                score_sql = (
                    "SELECT AVG(total_score) AS c FROM assessments "
                    "WHERE is_deleted = 0 AND submitted_at IS NOT NULL"
                )
                score_params: dict[str, Any] = {}
            else:
                score_sql = (
                    "SELECT AVG(total_score) AS c FROM assessments "
                    "WHERE is_deleted = 0 AND submitted_at IS NOT NULL "
                    "AND talent_id IN (SELECT id FROM talents "
                    "WHERE is_deleted = 0 AND organization_id = :scope_org)"
                )
                score_params = {"scope_org": scope.get("organization_id")}
            score = await self._mysql_scalar(score_sql, score_params)

        # 6) 概览小趋势：近 6 个月人才总数（PgSQL 快照；回退 MySQL 新增口径）
        trend_rows = await self._pg_rows(
            "SELECT TO_CHAR(snapshot_month, 'YYYY-MM') AS m, SUM(talent_count) AS c "
            "FROM fact_talent_snapshot "
            "WHERE education = '' AND age_group = '' AND organization_id = 0 "
            "AND snapshot_month >= (CURRENT_DATE - INTERVAL '6 months') "
            "GROUP BY 1 ORDER BY 1"
        )
        monthly = [
            MonthlyTrendPoint(month=str(row["m"]), value=float(row["c"] or 0))
            for row in trend_rows
        ]
        if not monthly:
            org_sql, org_params = self._org_filter(scope)
            new_rows = await self._mysql_rows(
                "SELECT DATE_FORMAT(created_at, '%Y-%m') AS m, COUNT(*) AS c "
                "FROM talents WHERE is_deleted = 0 AND created_at >= "
                "DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 MONTH), '%Y-%m-01')" + org_sql
                + " GROUP BY m ORDER BY m",
                org_params,
            )
            monthly = [
                MonthlyTrendPoint(month=str(row["m"]), value=float(row["c"] or 0))
                for row in new_rows
            ]
            if monthly:
                notes.append("分析库未同步：月度趋势为 MySQL 新增口径（非存量）")

        return DashboardOverview(
            total_talents=int(total or 0),
            active_positions=active_positions,
            match_rate=round(float(match_rate or 0.0), 4),
            training_completion=round(float(training or 0.0), 4),
            assessment_avg_score=None if score is None else round(float(score), 2),
            monthly_trend=monthly,
            data_notes=notes,
        )

    # ================================================================ 分布
    async def distribution(
        self, dist_type: str, scope: dict[str, Any] | None = None
    ) -> DistributionResponse:
        """多维分布：type in education/age/skill/org。"""
        if dist_type not in _DIST_TYPES:
            raise ParamError(f"不支持的分布类型：{dist_type}（可选 {', '.join(_DIST_TYPES)}）")
        scope = scope or {}
        handler = {
            "education": self._dist_education,
            "age": self._dist_age,
            "skill": self._dist_skill,
            "org": self._dist_org,
        }[dist_type]
        return await handler(scope)

    async def _dist_education(self, scope: dict[str, Any]) -> DistributionResponse:
        sql, _ = self._pg_exec_sql(
            "SELECT COALESCE(NULLIF(education, ''), '未填写') AS label, "
            "COUNT(*) AS value FROM dim_talent_wide GROUP BY 1 ORDER BY 2 DESC",
            scope,
        )
        rows = await self._pg_rows(sql)
        note = ""
        if not rows:
            org_sql, params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT COALESCE(NULLIF(education, ''), '未填写') AS label, "
                "COUNT(*) AS value FROM talents "
                "WHERE is_deleted = 0 AND status <> 'left'" + org_sql
                + " GROUP BY 1 ORDER BY 2 DESC",
                params,
            )
            note = "分析库未同步，回退 MySQL 口径"
        return DistributionResponse(
            type="education",
            items=[DistributionItem(label=str(r["label"]), value=float(r["value"] or 0)) for r in rows],
            note=note,
        )

    async def _dist_age(self, scope: dict[str, Any]) -> DistributionResponse:
        # 年龄段仅存在于快照（宽表无出生日期）：org0=全部门 / org=本部门（ETL 双口径）
        org_value = 0 if is_scope_unrestricted(scope) else scope.get("organization_id")
        rows = await self._pg_rows(
            "SELECT age_group AS label, SUM(talent_count) AS value "
            "FROM fact_talent_snapshot "
            "WHERE organization_id = :org AND education = '' AND age_group <> '' "
            "GROUP BY 1 ORDER BY 2 DESC",
            {"org": org_value},
        )
        note = ""
        if not rows:
            org_sql, params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT CASE "
                "WHEN birth_date IS NULL THEN '未知' "
                "WHEN TIMESTAMPDIFF(YEAR, birth_date) < 25 THEN '<25' "
                "WHEN TIMESTAMPDIFF(YEAR, birth_date) < 30 THEN '25-30' "
                "WHEN TIMESTAMPDIFF(YEAR, birth_date) < 35 THEN '30-35' "
                "WHEN TIMESTAMPDIFF(YEAR, birth_date) < 40 THEN '35-40' "
                "ELSE '40+' END AS label, COUNT(*) AS value "
                "FROM talents WHERE is_deleted = 0 AND status <> 'left'" + org_sql
                + " GROUP BY 1 ORDER BY 2 DESC",
                params,
            )
            note = "分析库未同步，回退 MySQL 口径"
        return DistributionResponse(
            type="age",
            items=[DistributionItem(label=str(r["label"]), value=float(r["value"] or 0)) for r in rows],
            note=note,
        )

    async def _dist_skill(self, scope: dict[str, Any]) -> DistributionResponse:
        sql, _ = self._pg_exec_sql(
            "SELECT s.skill AS label, COUNT(*) AS value "
            "FROM dim_talent_wide w, unnest(w.skill_tags) AS s(skill) "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 50",
            scope,
        )
        rows = await self._pg_rows(sql)
        note = ""
        if not rows:
            org_sql, params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT jt.tag AS label, COUNT(*) AS value "
                "FROM talents t, JSON_TABLE(t.skill_tags, '$[*]' "
                "COLUMNS (tag VARCHAR(64) PATH '$')) jt "
                "WHERE t.is_deleted = 0 AND t.status <> 'left' "
                "AND t.skill_tags IS NOT NULL" + org_sql
                + " GROUP BY 1 ORDER BY 2 DESC LIMIT 50",
                params,
            )
            note = "分析库未同步，回退 MySQL JSON_TABLE 口径"
        return DistributionResponse(
            type="skill",
            items=[DistributionItem(label=str(r["label"]), value=float(r["value"] or 0)) for r in rows],
            note=note,
        )

    async def _dist_org(self, scope: dict[str, Any]) -> DistributionResponse:
        params: dict[str, Any] = {}
        org_clause = ""
        if not is_scope_unrestricted(scope):
            org_value = scope.get("organization_id")
            if not isinstance(org_value, int) or org_value <= 0:
                raise ParamError("数据范围受限：当前身份缺少部门信息，无法执行分析查询")
            org_clause = " AND f.organization_id = :scope_org"
            params["scope_org"] = org_value
        rows = await self._pg_rows(
            "SELECT o.name AS label, SUM(f.talent_count) AS value "
            "FROM fact_talent_snapshot f "
            "JOIN dim_organization o ON o.org_id = f.organization_id "
            "WHERE f.snapshot_month = (SELECT MAX(snapshot_month) FROM fact_talent_snapshot) "
            "AND f.education = '' AND f.age_group = '' AND f.organization_id > 0"
            + org_clause
            + " GROUP BY o.name ORDER BY 2 DESC",
            params,
        )
        note = ""
        if not rows:
            org_sql, mysql_params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT COALESCE(o.name, '未分配') AS label, COUNT(*) AS value "
                "FROM talents t LEFT JOIN organizations o ON o.id = t.organization_id "
                "WHERE t.is_deleted = 0 AND t.status <> 'left'" + org_sql
                + " GROUP BY 1 ORDER BY 2 DESC",
                mysql_params,
            )
            note = "分析库未同步，回退 MySQL 口径"
        return DistributionResponse(
            type="org",
            items=[DistributionItem(label=str(r["label"]), value=float(r["value"] or 0)) for r in rows],
            note=note,
        )

    # ================================================================ 趋势
    async def trend(
        self,
        metric: str,
        scope: dict[str, Any] | None = None,
        granularity: str = "month",
    ) -> TrendResponse:
        """月度趋势：talent_new 新增 / turnover 离职 / match 平均匹配分（MySQL 直查）。"""
        if metric not in _TREND_METRICS:
            raise ParamError(f"不支持的趋势指标：{metric}（可选 {', '.join(_TREND_METRICS)}）")
        if granularity != "month":
            raise ParamError("暂仅支持 granularity=month")
        scope = scope or {}
        scope_org = "" if is_scope_unrestricted(scope) else scope.get("organization_id")
        if not is_scope_unrestricted(scope) and not isinstance(scope_org, int):
            raise ParamError("数据范围受限：当前身份缺少部门信息，无法执行分析查询")

        note = ""
        if metric == "talent_new":
            cond, params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT DATE_FORMAT(created_at, '%Y-%m') AS m, COUNT(*) AS c "
                "FROM talents WHERE is_deleted = 0 AND created_at >= "
                "DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 12 MONTH), '%Y-%m-01')"
                + cond
                + " GROUP BY m ORDER BY m",
                params,
            )
            unit = "count"
        elif metric == "turnover":
            cond, params = self._org_filter(scope)
            rows = await self._mysql_rows(
                "SELECT DATE_FORMAT(updated_at, '%Y-%m') AS m, COUNT(*) AS c "
                "FROM talents WHERE is_deleted = 0 AND status = 'left' AND updated_at >= "
                "DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 12 MONTH), '%Y-%m-01')"
                + cond
                + " GROUP BY m ORDER BY m",
                params,
            )
            unit = "count"
            note = "口径：以状态更新月近似离职月（talents 无 left_at 字段）"
        else:  # match
            if is_scope_unrestricted(scope) or not scope:
                match_sql = (
                    "SELECT DATE_FORMAT(COALESCE(matched_at, created_at), '%Y-%m') AS m, "
                    "AVG(score) AS c FROM matching_records "
                    "WHERE COALESCE(matched_at, created_at) >= "
                    "DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 12 MONTH), '%Y-%m-01') "
                    "GROUP BY m ORDER BY m"
                )
                params = {}
            else:
                match_sql = (
                    "SELECT DATE_FORMAT(COALESCE(matched_at, created_at), '%Y-%m') AS m, "
                    "AVG(score) AS c FROM matching_records "
                    "WHERE COALESCE(matched_at, created_at) >= "
                    "DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 12 MONTH), '%Y-%m-01') "
                    "AND position_id IN (SELECT id FROM positions "
                    "WHERE organization_id = :scope_org) GROUP BY m ORDER BY m"
                )
                params = {"scope_org": scope_org}
            rows = await self._mysql_rows(match_sql, params)
            unit = "score"

        return TrendResponse(
            metric=metric,  # type: ignore[arg-type]
            granularity="month",
            unit=unit,
            points=[TrendPoint(month=str(r["m"]), value=float(r["c"] or 0)) for r in rows],
            note=note,
        )

    # ================================================================ NL2SQL
    async def nl2sql(
        self, request: NL2SQLRequest, scope: dict[str, Any] | None = None
    ) -> NL2SQLResponse:
        """自然语言问数（Vanna -> 校验 -> 数据范围 -> 只读执行 -> 图表/解读）。"""
        adapter = VannaAdapter()
        result = await adapter.ask(
            request.question,
            user_scope=scope or {},
            max_rows=request.max_rows,
        )
        return NL2SQLResponse(**result)

    # ================================================================ 报告
    async def generate_report(
        self,
        request: ReportRequest,
        scope: dict[str, Any] | None = None,
    ) -> ReportTask:
        """异步生成分析报告：立即返回任务号，后台聚合指标并落盘。"""
        if request.report_type == "custom" and not request.date_range:
            raise ParamError("report_type=custom 时必须提供 date_range")
        task_id = uuid.uuid4().hex[:16]
        task = ReportTask(
            task_id=task_id,
            status="pending",
            report_type=request.report_type,
            requested_format=request.format,
            created_at=datetime.now(timezone.utc),
        )
        _REPORT_TASKS[task_id] = task
        while len(_REPORT_TASKS) > _MAX_REPORT_TASKS:
            oldest, _ = _REPORT_TASKS.popitem(last=False)
            _REPORT_HANDLES.pop(oldest, None)
        handle = asyncio.create_task(self._run_report(task_id, request, scope or {}))
        _REPORT_HANDLES[task_id] = handle
        return task

    def reports(self) -> list[ReportTask]:
        """报告任务列表（新任务在前）。"""
        return list(reversed(_REPORT_TASKS.values()))

    async def wait_for_report(self, task_id: str, timeout: float = 10.0) -> ReportTask:
        """等待报告任务结束（脚本 / 测试用）。"""
        handle = _REPORT_HANDLES.get(task_id)
        if handle is not None:
            try:
                await asyncio.wait_for(asyncio.shield(handle), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        task = _REPORT_TASKS.get(task_id)
        if task is None:
            raise ParamError(f"报告任务不存在：{task_id}")
        return task

    async def _collect_report_pieces(
        self, request: ReportRequest, scope: dict[str, Any]
    ) -> dict[str, Any]:
        """聚合报告素材（单独方法便于测试注入）。"""
        pieces: dict[str, Any] = {}
        try:
            pieces["overview"] = await self.overview(scope)
        except Exception as exc:  # noqa: BLE001 - 报告尽力而为
            logger.warning("报告聚合 overview 失败：%s", exc)
            pieces["overview_error"] = str(exc)
        for dist_type in ("education", "org"):
            try:
                pieces[f"distribution_{dist_type}"] = await self.distribution(dist_type, scope)
            except Exception as exc:  # noqa: BLE001
                logger.warning("报告聚合 distribution(%s) 失败：%s", dist_type, exc)
        try:
            pieces["trend_turnover"] = await self.trend("turnover", scope)
        except Exception as exc:  # noqa: BLE001
            logger.warning("报告聚合 trend 失败：%s", exc)
        return pieces

    async def _run_report(
        self, task_id: str, request: ReportRequest, scope: dict[str, Any]
    ) -> None:
        task = _REPORT_TASKS[task_id]
        task.status = "running"
        try:
            pieces = await self._collect_report_pieces(request, scope)
            markdown = self._render_report_markdown(request, pieces)
            url, actual_format = self._write_report_file(task_id, request, markdown)
            task.status = "succeeded"
            task.url = url
            task.actual_format = actual_format
            task.finished_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001 - 任务失败也要可见，不打断主流程
            logger.exception("报告生成失败 task_id=%s", task_id)
            task.status = "failed"
            task.error = str(exc)
            task.finished_at = datetime.now(timezone.utc)

    @staticmethod
    def _render_report_markdown(
        request: ReportRequest, pieces: dict[str, Any]
    ) -> str:
        """渲染报告正文（Markdown；docx/pdf 依赖存在时再转格式）。"""
        lines = [
            f"# 人才数据分析报告（{request.report_type}）",
            "",
            f"生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        ]
        if request.date_range:
            lines.append(f"统计区间：{request.date_range[0]} ~ {request.date_range[1]}")
        overview = pieces.get("overview")
        if isinstance(overview, DashboardOverview):
            lines += [
                "",
                "## 一、核心指标",
                "",
                f"- 人才总数：{overview.total_talents}",
                f"- 在招岗位：{overview.active_positions}",
                f"- 匹配率：{overview.match_rate:.0%}",
                f"- 培训完成率：{overview.training_completion:.0%}",
                f"- 平均测评分：{overview.assessment_avg_score}",
            ]
            for note in overview.data_notes:
                lines.append(f"- 说明：{note}")
        for key, title in (
            ("distribution_education", "学历分布"),
            ("distribution_org", "部门分布"),
            ("trend_turnover", "离职趋势"),
        ):
            payload = pieces.get(key)
            if payload is None:
                continue
            lines += ["", f"## {title}", ""]
            if isinstance(payload, DistributionResponse):
                for item in payload.items:
                    lines.append(f"- {item.label}：{item.value:g}")
                if payload.note:
                    lines.append(f"- 说明：{payload.note}")
            elif isinstance(payload, TrendResponse):
                for point in payload.points:
                    lines.append(f"- {point.month}：{point.value:g}")
                if payload.note:
                    lines.append(f"- 说明：{payload.note}")
        if pieces.get("overview_error"):
            lines += ["", f"> 指标聚合部分失败：{pieces['overview_error']}"]
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _write_report_file(
        task_id: str, request: ReportRequest, markdown: str
    ) -> tuple[str, str]:
        """落盘报告：优先 docx（缺 python-docx 时降级 md，actual_format 如实记录）。"""
        now = datetime.now()
        rel_dir = Path("reports") / f"{now:%Y}" / f"{now:%m}"
        target_dir = Path(settings.UPLOAD_DIR) / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        if request.format == "docx":
            try:
                import docx  # type: ignore[import-not-found]  # python-docx（可选依赖）

                document = docx.Document()
                for line in markdown.splitlines():
                    if line.startswith("# "):
                        document.add_heading(line[2:], level=1)
                    elif line.startswith("## "):
                        document.add_heading(line[3:], level=2)
                    elif line.strip():
                        document.add_paragraph(line)
                file_path = target_dir / f"{task_id}.docx"
                document.save(str(file_path))
                return (str(rel_dir / file_path.name), "docx")
            except ImportError:
                pass
        # PDF 渲染依赖（reportlab 等）未安装 / 格式降级：以 Markdown 承载内容
        file_path = target_dir / f"{task_id}.md"
        file_path.write_text(markdown, encoding="utf-8")
        return (str(rel_dir / file_path.name), "md")


__all__ = [
    "AnalyticsService",
    "reset_report_tasks",
    "scope_for_user",
]
