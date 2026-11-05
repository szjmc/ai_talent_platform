"""
模块：app/ai/skills/matching_skill.py
职责：人岗匹配技能（供 Agent 调用的 3 个工具：打分 / 搜岗位 / 列候选人）。

标签：[架构组] [P0]  依赖：[Agent组] [P0] MatchingService / PositionRepository

============================ 实现思路与代码要点 ============================
技能方法：
    calculate_matching_score(talent_id, position_id) -> dict    人岗匹配打分
    search_positions(keyword) -> list[dict]                     岗位检索
    list_candidates(position_id, top_k) -> list[dict]           候选人排序
要点：
- 打分逻辑复用 MatchingService，避免两套算法；本文件同时提供**纯函数版**
  `compute_match_score(talent, position)`（只吃 dict，不碰库），
  这样 Service 未实现时，Agent 传入的人才/岗位数据也能直接打分；
- 返回结构精简（只给 LLM 必要字段），控制上下文长度；
- 权重沿用设计文档：技能 0.4 / 经验 0.3 / 学历 0.2 / 素质 0.1。
已完成：
    - [x] 三个技能 + 工具描述（JSON Schema）+ 纯函数打分算法 + 权限校验
待实现（依赖方）：
    - [ ] MatchingService.calculate / list_candidates（Agent 组）
    - [ ] PositionRepository.search_by_title（Agent 组）
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.skills.base import (
    BaseSkill,
    import_dependency,
    load_skill_context,
    require_dependency,
)
from app.core.exceptions import ParamError
from app.core.permissions import PermissionEnum
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 打分常量
EDUCATION_LEVELS: dict[str, int] = {
    "高中及以下": 1,
    "中专": 1,
    "大专": 2,
    "本科": 3,
    "硕士": 4,
    "博士": 5,
}
POTENTIAL_SCORES: dict[str, float] = {"A": 95.0, "B": 80.0, "C": 65.0, "D": 50.0}
DEFAULT_WEIGHTS: dict[str, float] = {"skill": 0.4, "experience": 0.3, "education": 0.2, "quality": 0.1}


def normalize_skills(values: Any) -> set[str]:
    """技能标签归一化（去空、转小写、去空格），用于集合匹配。"""
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(item).strip().lower() for item in values if str(item).strip()}


def education_level(value: Any) -> int:
    """学历转等级（未知学历按 0 处理，匹配时视为最低）。"""
    if value is None:
        return 0
    return EDUCATION_LEVELS.get(str(value).strip(), 0)


def min_education_from_position(position: dict[str, Any]) -> str | None:
    """从岗位结构化要求里取最低学历（requirements.min_education / min_education）。"""
    requirements = position.get("requirements") or {}
    if isinstance(requirements, dict):
        return requirements.get("min_education") or requirements.get("education")
    return position.get("min_education")


def min_work_years_from_position(position: dict[str, Any]) -> float:
    """从岗位结构化要求里取最低工作年限。"""
    requirements = position.get("requirements") or {}
    value = None
    if isinstance(requirements, dict):
        value = requirements.get("min_work_years") or requirements.get("experience_years")
    if value is None:
        value = position.get("min_work_years")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_match_score(
    talent: dict[str, Any],
    position: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """纯函数版人岗匹配打分（不访问数据库，便于单测与复现）。

    维度：
      - skill      技能重合度 = |T∩P| / |P|（岗位无技能要求时按 80 分计）
      - experience 工作年限达标度（达到要求 100，每少 1 年 -15，下限 0）
      - education  学历达标度（达标 100，每差一级 -25，下限 0）
      - quality    素质分（潜力等级 A/B/C/D -> 95/80/65/50，缺省 70）
    """
    weights = weights or DEFAULT_WEIGHTS
    talent_skills = normalize_skills(talent.get("skill_tags"))
    position_skills = normalize_skills(position.get("skill_tags"))

    if position_skills:
        matched = talent_skills & position_skills
        skill_score = 100.0 * len(matched) / len(position_skills)
    else:
        matched = set()
        skill_score = 80.0  # 岗位未声明技能要求，不因缺失技能扣分

    required_years = min_work_years_from_position(position)
    work_years = float(talent.get("work_years") or 0)
    if required_years <= 0:
        experience_score = min(100.0, 60.0 + work_years * 8)
    elif work_years >= required_years:
        experience_score = 100.0
    else:
        experience_score = max(0.0, 100.0 - (required_years - work_years) * 15)

    required_education = min_education_from_position(position)
    talent_level = education_level(talent.get("education"))
    if not required_education:
        education_score = 80.0
    else:
        gap = education_level(required_education) - talent_level
        education_score = 100.0 if gap <= 0 else max(0.0, 100.0 + gap * -25)

    quality_score = POTENTIAL_SCORES.get(str(talent.get("potential_level") or "").upper())
    if quality_score is None:
        dimensions = talent.get("dimensions") or {}
        quality_score = float(dimensions.get("quality") or 70)

    dimensions = {
        "skill": round(min(100.0, skill_score), 2),
        "experience": round(min(100.0, experience_score), 2),
        "education": round(min(100.0, education_score), 2),
        "quality": round(min(100.0, quality_score), 2),
    }
    total = sum(dimensions[key] * weights.get(key, 0.0) for key in dimensions)
    return {
        "total_score": round(total, 2),
        "dimensions": dimensions,
        "weights": dict(weights),
        "highlights": sorted(matched)[:10],
        "gaps": sorted(position_skills - talent_skills)[:10],
    }


# ------------------------------------------------------------------ 技能 1：打分
@skill_registry.register("matching_score")
class MatchingScoreSkill(BaseSkill):
    """人岗匹配打分（传入 ID 走 Service，传入数据走纯函数算法）。"""

    name = "matching_score"
    description = "计算人才与岗位的匹配得分（0-100）及技能/经验/学历/素质四维分数"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "talent_id": {"type": "integer", "description": "人才 ID"},
            "position_id": {"type": "integer", "description": "岗位 ID"},
            "talent": {
                "type": "object",
                "description": "人才数据（skill_tags/work_years/education/potential_level），传了可不查库",
            },
            "position": {
                "type": "object",
                "description": "岗位数据（skill_tags/requirements），传了可不查库",
            },
        },
        "required": [],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.MATCHING_READ)
    requires = ("app.services.matching_service.MatchingService（按 ID 打分时）",)
    timeout_seconds = 30.0

    async def execute(
        self,
        talent_id: int | None = None,
        position_id: int | None = None,
        talent: dict[str, Any] | None = None,
        position: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        context = load_skill_context()

        if talent is not None and position is not None:
            # 纯函数路径：Agent 已持有数据时直接算（无库依赖，保证技能始终可用）
            result = compute_match_score(talent, position)
            result.update(
                {
                    "talent_id": talent.get("id") or talent_id,
                    "position_id": position.get("id") or position_id,
                    "source": "skill_pure_function",
                }
            )
            return result

        if talent_id is None or position_id is None:
            raise ParamError("请同时提供 talent_id 与 position_id，或直接传 talent / position 数据")

        service = self._load_service()
        calculator = getattr(service, "calculate", None) or getattr(service, "match", None)
        require_dependency(
            calculator is not None,
            "MatchingService.calculate",
            hint="Agent 组未实现，可改为直接传 talent/position 数据调用本技能",
        )
        result = calculator(talent_id=talent_id, position_id=position_id, context=context)
        if hasattr(result, "__await__"):
            result = await result
        return self._shape(result, talent_id, position_id, "matching_service")

    def _load_service(self) -> Any:
        service_cls = import_dependency(
            "app.services.matching_service",
            "MatchingService",
            hint="Agent 组未实现 MatchingService",
        )
        return service_cls()

    @staticmethod
    def _shape(result: Any, talent_id: int, position_id: int, source: str) -> dict[str, Any]:
        """把 Service 返回值收敛成统一结构（dict / ORM 都兼容）。"""
        if isinstance(result, dict):
            data = dict(result)
        else:
            data = {
                "total_score": getattr(result, "score", None) or getattr(result, "total_score", 0),
                "dimensions": getattr(result, "dimension_scores", None) or {},
                "highlights": getattr(result, "highlights", None) or [],
                "gaps": getattr(result, "gaps", None) or [],
            }
        try:
            data["total_score"] = round(float(data.get("total_score") or 0), 2)
        except (TypeError, ValueError):
            data["total_score"] = 0.0
        data.update({"talent_id": talent_id, "position_id": position_id, "source": source})
        return data


# ------------------------------------------------------------------ 技能 2：搜岗位
@skill_registry.register("position_search")
class PositionSearchSkill(BaseSkill):
    """按关键词检索在招岗位。"""

    name = "position_search"
    description = "按关键词/技能检索在招岗位，返回岗位 ID、名称、层级与技能要求"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "岗位名称或技能关键词", "maxLength": 100},
            "status": {
                "type": "string",
                "description": "岗位状态",
                "enum": ["open", "paused", "closed"],
                "default": "open",
            },
            "top_k": {"type": "integer", "description": "返回条数", "default": 10, "minimum": 1, "maximum": 50},
        },
        "required": ["keyword"],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.POSITION_READ)
    requires = ("app.repositories.position_repository.PositionRepository",)
    timeout_seconds = 20.0

    async def execute(self, keyword: str, status: str = "open", top_k: int = 10, **_: Any) -> list[dict[str, Any]]:
        context = load_skill_context()
        keyword = (keyword or "").strip()
        if not keyword:
            raise ParamError("检索关键词不能为空")

        repository = self._load_repository()
        rows = await self._query(repository, keyword, status, top_k, context)
        return [self._shape(row) for row in rows[:top_k]]

    def _load_repository(self) -> Any:
        repo_cls = import_dependency(
            "app.repositories.position_repository",
            "PositionRepository",
            hint="Agent 组未实现岗位检索",
        )
        return repo_cls()

    async def _query(self, repository: Any, keyword: str, status: str, top_k: int, context: Any) -> list[Any]:
        for method_name in ("search_by_title", "search", "list"):
            method = getattr(repository, method_name, None)
            if method is None:
                continue
            try:
                result = method(keyword=keyword, status=status, limit=top_k, context=context)
            except TypeError:
                try:
                    result = method(keyword, status, top_k)
                except TypeError:
                    continue
            if hasattr(result, "__await__"):
                result = await result
            return list(result or [])
        require_dependency(False, "PositionRepository.search_by_title", hint="Agent 组未实现岗位检索方法")
        return []

    @staticmethod
    def _shape(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            data = dict(row)
        else:
            data = {
                "id": getattr(row, "id", None),
                "title": getattr(row, "title", None),
                "code": getattr(row, "code", None),
                "level": getattr(row, "level", None),
                "status": getattr(row, "status", None),
                "organization_id": getattr(row, "organization_id", None),
                "skill_tags": getattr(row, "skill_tags", None) or [],
                "requirements": getattr(row, "requirements", None),
            }
        if not data.get("requirements"):
            data.pop("requirements", None)
        return data


# ------------------------------------------------------------------ 技能 3：候选人列表
@skill_registry.register("candidate_list")
class CandidateListSkill(BaseSkill):
    """查询某岗位的候选人排序（来自历史匹配记录）。"""

    name = "candidate_list"
    description = "查询指定岗位的候选人排序（匹配得分从高到低）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "position_id": {"type": "integer", "description": "岗位 ID"},
            "top_k": {"type": "integer", "description": "返回条数", "default": 10, "minimum": 1, "maximum": 50},
            "min_score": {"type": "number", "description": "最低匹配分（0-100）", "default": 0, "minimum": 0, "maximum": 100},
        },
        "required": ["position_id"],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.MATCHING_READ)
    requires = ("app.services.matching_service.MatchingService.list_candidates",)
    timeout_seconds = 30.0

    async def execute(
        self,
        position_id: int,
        top_k: int = 10,
        min_score: float = 0,
        **_: Any,
    ) -> list[dict[str, Any]]:
        context = load_skill_context()
        service_cls = import_dependency(
            "app.services.matching_service",
            "MatchingService",
            hint="Agent 组未实现 MatchingService",
        )
        service = service_cls()
        method = getattr(service, "list_candidates", None) or getattr(service, "get_latest_by_position", None)
        require_dependency(
            method is not None,
            "MatchingService.list_candidates",
            hint="Agent 组未实现，可先用 matching_score 逐个打分",
        )
        result = method(position_id=position_id, top_k=top_k, min_score=min_score, context=context)
        if hasattr(result, "__await__"):
            result = await result
        rows = [self._shape(row) for row in list(result or [])]
        rows = [row for row in rows if float(row.get("score") or 0) >= min_score]
        return sorted(rows, key=lambda item: float(item.get("score") or 0), reverse=True)[:top_k]

    @staticmethod
    def _shape(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            data = dict(row)
            data.setdefault("score", data.get("total_score", 0))
        else:
            data = {
                "talent_id": getattr(row, "talent_id", None),
                "talent_name": getattr(row, "talent_name", None),
                "score": getattr(row, "score", None) or getattr(row, "total_score", 0) or 0,
                "dimension_scores": getattr(row, "dimension_scores", None) or {},
                "suggestion": getattr(row, "suggestion", None),
            }
        try:
            data["score"] = round(float(data["score"]), 2)
        except (TypeError, ValueError):
            data["score"] = 0.0
        return data


# ------------------------------------------------------------------ 函数式入口
async def calculate_matching_score(
    talent_id: int | None = None,
    position_id: int | None = None,
    talent: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await MatchingScoreSkill().execute(
        talent_id=talent_id, position_id=position_id, talent=talent, position=position
    )


async def search_positions(keyword: str, status: str = "open", top_k: int = 10) -> list[dict[str, Any]]:
    return await PositionSearchSkill().execute(keyword=keyword, status=status, top_k=top_k)


async def list_candidates(position_id: int, top_k: int = 10, min_score: float = 0) -> list[dict[str, Any]]:
    return await CandidateListSkill().execute(position_id=position_id, top_k=top_k, min_score=min_score)
