"""
模块：app/ai/skills/training_skill.py
职责：培训模块对外暴露的 Skills 可编排组件（Agent / LLM 的工具层）。

标签：[Agent组] [P1]

============================ 实现思路与代码要点 ============================
一个 Skill = 一个可被 LLM function-calling 调用的「工具」。本模块只暴露两个，
刻意不做「课程增删改」这类管理动作——那是 HR 在管理台做的事，不该让模型代劳。

技能 1 training_plan_generate（生成培训方案）
    入参：人才 ID + 能力短板清单（来自测评 Agent 的 gaps 契约）+ 周期/周学时上限
    行为：复用 TrainingService 的节点 1~4（诊断 → 排期 → 匹配真实课程 → 落库推送）
    返回：可直接给前端渲染的 scheme 摘要（周次 / 维度 / 学时 / 课程名 / 是否来自库）

技能 2 training_lag_scan（滞后扫描）
    入参：可选 talent_id（不传则扫描全部进行中计划）
    行为：复用节点 5，按「时间进度 vs 完成学时」判级并发事件
    返回：预警清单，供 HR 看板或定时任务消费

安全与边界（与其他技能保持一致）：
- 身份与权限只从 AI 上下文取（load_skill_context），LLM 传入的身份参数一律忽略；
- 业务逻辑一律走 Service，技能内不写 SQL、不拼业务规则；
- 课程名只能来自课程库：Service 层若查不到课会降级为「外部建议」，
  技能不提供任何「补一个课程名」的入参，从接口层面堵住编造；
- 失败抛业务异常，由 BaseSkill.run() 转成结构化失败结果，不返回 None。
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.skills.base import BaseSkill, SkillParamError, import_dependency
from app.core.permissions import PermissionEnum
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

# 能力短板的 JSON Schema 片段（供两个技能的 parameters 复用）
_GAP_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dimension": {"type": "string", "description": "能力维度名称，必须与课程库分类一致", "maxLength": 32},
        "current": {"type": "number", "description": "当前分数 0~100", "minimum": 0, "maximum": 100},
        "required": {"type": "number", "description": "岗位要求分数 0~100", "minimum": 0, "maximum": 100},
        "weight": {"type": "number", "description": "岗位权重 0~1", "minimum": 0, "maximum": 1},
    },
    "required": ["dimension", "current", "required"],
}


def _load_training_service() -> Any:
    """延迟导入 Service，避免技能模块 import 期就把 DB 依赖拉起来。"""
    return import_dependency(
        "app.services.training_service",
        "TrainingService",
        hint="培训模块 Service 未就绪（app/services/training_service.py）",
    )


def _load_session_scope() -> Any:
    return import_dependency(
        "app.infrastructure.database.session",
        "session_scope",
        hint="数据库会话管理未就绪（app/infrastructure/database/session.py）",
    )


def _schema_factory() -> Any:
    return import_dependency(
        "app.schemas.training",
        "TrainingPlanGenerateRequest",
        hint="培训模块 Schema 未就绪（app/schemas/training.py）",
    )


# ------------------------------------------------------------------ 技能 1：生成方案
@skill_registry.register("training_plan_generate")
class TrainingPlanGenerateSkill(BaseSkill):
    """按能力短板生成个性化培训方案（课程全部来自平台课程库）。"""

    name = "training_plan_generate"
    description = (
        "根据人才能力短板生成培训方案：自动排优先级、排 4 周课表、从课程库挂真实课程，"
        "并返回每周学什么、多少学时、哪门课。不会编造课程，库里没有就明确标注为外部建议。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "talent_id": {"type": "integer", "description": "人才 ID", "minimum": 1},
            "gaps": {
                "type": "array",
                "description": "能力短板清单（来自测评结果；维度名需与课程库分类一致）",
                "items": _GAP_ITEM_SCHEMA,
                "minItems": 1,
                "maxItems": 20,
            },
            "max_weeks": {
                "type": "integer",
                "description": "培训周期（周），默认 4",
                "default": 4,
                "minimum": 1,
                "maximum": 12,
            },
            "max_hours_per_week": {
                "type": "number",
                "description": "每周学时上限，默认 8",
                "default": 8,
                "minimum": 1,
                "maximum": 40,
            },
            "pass_score": {
                "type": "number",
                "description": "结课考核及格线，默认 80",
                "default": 80,
                "minimum": 0,
                "maximum": 100,
            },
        },
        "required": ["talent_id", "gaps"],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.TRAINING_MANAGE)
    requires = (
        "app.services.training_service.TrainingService.generate_plan",
        "app.repositories.training_repository.TrainingRepository",
    )
    timeout_seconds = 60.0

    async def execute(
        self,
        talent_id: int,
        gaps: list[dict[str, Any]],
        max_weeks: int = 4,
        max_hours_per_week: float = 8,
        pass_score: float = 80,
        **_: Any,
    ) -> dict[str, Any]:
        if not gaps:
            raise SkillParamError("能力短板清单不能为空：请先调用测评能力获取 gaps")

        service_cls = _load_training_service()
        request_cls = _schema_factory()
        session_scope = _load_session_scope()

        try:
            request = request_cls(
                talent_id=talent_id,
                gaps=gaps,
                max_weeks=max_weeks,
                max_hours_per_week=max_hours_per_week,
                pass_score=pass_score,
            )
        except Exception as exc:  # noqa: BLE001 - Pydantic 校验失败统一转参数错误
            raise SkillParamError(f"培训方案入参不合法：{exc}") from exc

        async with session_scope() as db:
            plan = await service_cls(db).generate_plan(request)

        logger.info(
            "技能 training_plan_generate：talent=%s plan=%s 周=%s 学时=%s 来自库=%s",
            talent_id,
            plan.get("id"),
            plan.get("total_weeks"),
            plan.get("total_hours"),
            plan.get("library_rate"),
        )
        return {
            "plan_id": plan.get("id"),
            "plan_name": plan.get("name"),
            "talent_id": plan.get("talent_id"),
            "total_weeks": plan.get("total_weeks"),
            "total_hours": plan.get("total_hours"),
            "library_rate": plan.get("library_rate"),
            "external_count": plan.get("external_count"),
            "degrade": plan.get("degrade"),
            "generated_by": plan.get("generated_by"),
            "summary": plan.get("summary"),
            "assessment": plan.get("assessment"),
            "items": [
                {
                    "week": item.get("week"),
                    "dimension": item.get("dimension"),
                    "hours": item.get("hours"),
                    "course_id": item.get("course_id"),
                    "title": item.get("title"),
                    "is_platform": item.get("is_platform"),
                    "reason": item.get("reason"),
                }
                for item in plan.get("items", [])
            ],
        }


# ------------------------------------------------------------------ 技能 2：滞后扫描
@skill_registry.register("training_lag_scan")
class TrainingLagScanSkill(BaseSkill):
    """扫描进行中的培训计划，产出滞后预警清单。"""

    name = "training_lag_scan"
    description = (
        "扫描进行中的培训计划，按「时间进度 vs 实际完成学时」判定正常/提醒/HR预警，"
        "返回需要跟进的计划清单。可指定人才 ID 只看某个人。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "talent_id": {
                "type": "integer",
                "description": "只看该人才的计划；不传则扫描全部进行中计划",
                "minimum": 1,
            },
            "min_lag": {
                "type": "number",
                "description": "返回结果的最小落后比例（0~1），默认 0 表示全部返回",
                "default": 0,
                "minimum": 0,
                "maximum": 1,
            },
        },
        "required": [],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.TRAINING_READ)
    requires = ("app.services.training_service.TrainingService.scan_lag",)
    timeout_seconds = 45.0

    async def execute(
        self,
        talent_id: int | None = None,
        min_lag: float = 0,
        **_: Any,
    ) -> dict[str, Any]:
        service_cls = _load_training_service()
        session_scope = _load_session_scope()

        async with session_scope() as db:
            result = await service_cls(db).scan_lag()

        alerts = [
            alert
            for alert in result.get("alerts", [])
            if float(alert.get("lag_ratio") or 0) >= float(min_lag)
            and (talent_id is None or int(alert.get("talent_id") or 0) == int(talent_id))
        ]
        logger.info(
            "技能 training_lag_scan：扫描 %s 个计划，命中 %s 条",
            result.get("scanned"),
            len(alerts),
        )
        return {
            "scanned": result.get("scanned", 0),
            "warned": result.get("warned", 0),
            "reminded": result.get("reminded", 0),
            "alerts": alerts,
        }


# ------------------------------------------------------------------ 函数式入口
async def generate_training_plan(
    talent_id: int,
    gaps: list[dict[str, Any]],
    max_weeks: int = 4,
    max_hours_per_week: float = 8,
    pass_score: float = 80,
) -> dict[str, Any]:
    """生成培训方案（供 Workflow / 其他 Agent 直接 await 调用）。"""
    return await TrainingPlanGenerateSkill().execute(
        talent_id=talent_id,
        gaps=gaps,
        max_weeks=max_weeks,
        max_hours_per_week=max_hours_per_week,
        pass_score=pass_score,
    )


async def scan_training_lag(talent_id: int | None = None, min_lag: float = 0) -> dict[str, Any]:
    """扫描培训滞后计划（供定时任务 / HR 看板调用）。"""
    return await TrainingLagScanSkill().execute(talent_id=talent_id, min_lag=min_lag)


__all__ = [
    "TrainingLagScanSkill",
    "TrainingPlanGenerateSkill",
    "generate_training_plan",
    "scan_training_lag",
]
