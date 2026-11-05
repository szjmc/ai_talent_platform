"""
模块：app/patterns/strategy.py
职责：策略模式基础设施（用于「同类任务的多种算法」）。

标签：[架构组] [P1]（被匹配服务 / 匹配工作流复用）

典型场景：
- 人岗匹配策略：硬性过滤 / 向量软匹配 / 混合加权
- 文档切分策略：按长度 / 按语义 / 按标题层级
- 简历解析策略：规则解析 / LLM 解析 / 混合

使用示例：
    class HardFilterStrategy(MatchStrategy): ...
    class HybridStrategy(MatchStrategy): ...

    strategy = MatchStrategyFactory.create("hybrid", weights={"skill": 0.4})
    result = strategy.match(position, candidates)

待实现清单：
    - [ ] MatchStrategy 抽象基类与三个实现（业务模块负责）
    - [ ] 策略工厂（可复用本文件的 select_strategy）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

T = TypeVar("T")


class Strategy(ABC):
    """策略抽象基类：所有具体策略实现 execute。"""

    name: str = "base"

    @abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """执行策略。"""
        raise NotImplementedError


def select_strategy(
    strategies: dict[str, T], name: str | None, default: str
) -> T:
    """从策略字典中选择策略，未命中回落默认策略。"""
    key = name if name and name in strategies else default
    if key not in strategies:
        raise KeyError(f"策略不存在：{key}，可用：{sorted(strategies)}")
    return strategies[key]


# ==================================================================== 人岗匹配策略
# 标签：[架构组] [P1]（被匹配服务 / 匹配工作流复用）

MATCH_DEFAULT_WEIGHTS: dict[str, float] = {
    "skill": 0.4,
    "experience": 0.3,
    "education": 0.2,
    "quality": 0.1,
}

_EDUCATION_LEVELS: dict[str, int] = {
    "高中及以下": 1,
    "中专": 1,
    "大专": 2,
    "本科": 3,
    "硕士": 4,
    "博士": 5,
}


def _as_dict(value: Any) -> dict[str, Any]:
    """ORM 对象 / dict 统一转 dict。"""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {}


def _requirements(position: dict[str, Any]) -> dict[str, Any]:
    requirements = position.get("requirements")
    return requirements if isinstance(requirements, dict) else {}


def _required_skills(position: dict[str, Any]) -> list[str]:
    requirements = _requirements(position)
    skills = requirements.get("required_skills") or requirements.get("skills")
    if skills:
        return [str(item).strip().lower() for item in skills if str(item).strip()]
    return [str(item).strip().lower() for item in (position.get("skill_tags") or [])]


def _min_work_years(position: dict[str, Any]) -> float:
    requirements = _requirements(position)
    value = requirements.get("min_work_years") or requirements.get("experience_years")
    if value is None:
        value = position.get("min_work_years")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _min_education(position: dict[str, Any]) -> str | None:
    requirements = _requirements(position)
    return requirements.get("min_education") or requirements.get("education") or position.get("min_education")


def _education_level(value: Any) -> int:
    return _EDUCATION_LEVELS.get(str(value).strip(), 0) if value else 0


class MatchStrategy(Strategy):
    """人岗匹配策略基类。

    统一入口：``await strategy.execute(position, candidates)``
    （``match()`` 为其语义化别名，与文档示例一致）。
    """

    name = "base"

    def __init__(self, weights: dict[str, float] | None = None, **options: Any) -> None:
        self.weights = dict(weights or MATCH_DEFAULT_WEIGHTS)
        self.options = dict(options)

    # ---------------------------------------------------------------- 入口
    async def execute(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if len(args) >= 2:
            return await self.match(args[0], args[1])
        return await self.match(kwargs.get("position"), kwargs.get("candidates") or [])

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:  # type: ignore[override]
        raise NotImplementedError

    # ---------------------------------------------------------------- 工具
    def score(self, talent: Any, position: Any) -> dict[str, Any]:
        """复用 Skills 中的纯函数打分算法，避免两套算法漂移。"""
        from app.ai.skills.matching_skill import compute_match_score

        return compute_match_score(_as_dict(talent), _as_dict(position), weights=self.weights)

    @staticmethod
    def shape(talent: Any, scored: dict[str, Any]) -> dict[str, Any]:
        data = _as_dict(talent)
        return {
            "talent_id": data.get("id") or data.get("talent_id"),
            "name": data.get("name"),
            "score": scored.get("total_score", 0),
            "dimensions": scored.get("dimensions", {}),
            "highlights": scored.get("highlights", []),
            "gaps": scored.get("gaps", []),
        }


class HardFilterStrategy(MatchStrategy):
    """硬性条件过滤：学历 / 工作年限 / 必备技能，不满足直接淘汰。"""

    name = "hard"

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        pos = _as_dict(position)
        min_years = _min_work_years(pos)
        min_edu = _min_education(pos)
        required_skills = set(_required_skills(pos))
        results: list[dict[str, Any]] = []

        for candidate in candidates or []:
            talent = _as_dict(candidate)
            talent_skills = {str(item).strip().lower() for item in (talent.get("skill_tags") or [])}
            reasons: list[str] = []
            if min_edu and _education_level(talent.get("education")) < _education_level(min_edu):
                reasons.append(f"学历未达到 {min_edu}")
            if min_years and float(talent.get("work_years") or 0) < min_years:
                reasons.append(f"工作年限不足 {min_years} 年")
            if required_skills and not required_skills.issubset(talent_skills):
                reasons.append(f"缺少必备技能：{sorted(required_skills - talent_skills)}")
            if reasons:
                continue
            results.append(
                {
                    "talent_id": talent.get("id"),
                    "name": talent.get("name"),
                    "score": 100.0,
                    "dimensions": {},
                    "passed": True,
                    "reasons": [],
                }
            )
        return results


class SoftMatchStrategy(MatchStrategy):
    """加权软匹配：技能 / 经验 / 学历 / 素质加权打分并排序。"""

    name = "soft"

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        pos = _as_dict(position)
        scored = [self.shape(candidate, self.score(candidate, pos)) for candidate in candidates or []]
        return sorted(scored, key=lambda item: float(item.get("score") or 0), reverse=True)


class HybridStrategy(MatchStrategy):
    """混合策略：先硬性过滤，再对通过者加权打分排序（生产默认）。"""

    name = "hybrid"

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        hard = HardFilterStrategy(weights=self.weights, **self.options)
        passed = await hard.match(position, candidates)
        if not passed:
            return []
        passed_ids = {item.get("talent_id") for item in passed}
        pool = [
            candidate
            for candidate in candidates or []
            if (_as_dict(candidate).get("id") or _as_dict(candidate).get("talent_id")) in passed_ids
        ]
        soft = SoftMatchStrategy(weights=self.weights, **self.options)
        scored = await soft.match(position, pool)
        for item in scored:
            item["passed"] = True
        return scored


class MatchStrategyFactory:
    """匹配策略工厂。"""

    _strategies: dict[str, type[MatchStrategy]] = {
        "hard": HardFilterStrategy,
        "soft": SoftMatchStrategy,
        "hybrid": HybridStrategy,
    }

    @classmethod
    def create(cls, name: str | None = "hybrid", **kwargs: Any) -> MatchStrategy:
        strategy_cls = select_strategy(cls._strategies, name, "hybrid")
        return strategy_cls(**kwargs)

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._strategies)

    @classmethod
    def register(cls, name: str, strategy_cls: type[MatchStrategy]) -> None:
        cls._strategies[name] = strategy_cls
