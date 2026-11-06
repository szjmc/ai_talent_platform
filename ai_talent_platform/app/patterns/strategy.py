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


# [岗位匹配接入] 公共策略中的四态过滤；仅FAIL淘汰，软评分仍复用现有算法。
class HardFilterStrategy(MatchStrategy):
    """硬性条件过滤：学历 / 工作年限 / 必备技能，**仅 FAIL 淘汰**。"""

    name = "hard"

    # *kwq* 新增：最近一次 match 的淘汰名单及原因。
    # 修改原因：原实现用 `continue` 直接丢弃被淘汰者且不留原因，前端无法回答
    #          「这个人为什么没出现在结果里」，也无法产出 excluded 汇总。
    # 影响范围：仅新增实例属性，返回结构仍是 list[dict]，不破坏既有签名。
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        rule_config: Any = None,
        **options: Any,
    ) -> None:
        super().__init__(weights, **options)
        self.excluded: list[dict[str, Any]] = []
        self.rule_config = rule_config

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        """四态判定后过滤候选人。

        *kwq* 修改：原逻辑直接比较 education / work_years / skill_tags 三个字段，
        缺失值会被算成「0 级学历 / 0 年经验 / 空技能集」，从而把「档案未补全」的人
        误判为不达标并淘汰（talents 三列均为 nullable，这是真实存在的误淘汰路径）。

        修改后改为调用 app/services/matching_rules.judge_hard_conditions（纯函数四态判定）：
            FAIL          -> 进 self.excluded 并记录逐条原因（唯一允许淘汰的状态）
            UNKNOWN       -> 保留，标记待补全，综合分按 B-2 口径降权（默认 15%）
            NEEDS_REVIEW  -> 保留，标记人工复核
            PASS          -> 进入后续软评分

        影响范围：返回字典新增 hard_results / hard_status / has_unknown / needs_review /
                 data_completeness；score 由固定 100 改为复用 compute_match_score 的真实分数
                 （与 SoftMatchStrategy 口径一致，避免两套分数语义）。
                 已 grep 确认仓库内暂无其他调用方，不破坏外部接口。
        """
        # *kwq* 新增：延迟导入，避免 patterns（基础设施层）在模块加载期反向依赖 services 层，
        # 同时规避 services -> patterns 的潜在循环导入。
        from app.services.matching_rules import (
            HardRuleConfig,
            aggregate_status,
            apply_unknown_penalty,
            compute_data_completeness,
            has_needs_review,
            has_unknown,
            is_excluded,
            judge_hard_conditions,
        )

        config = self.rule_config or HardRuleConfig()
        pos = _as_dict(position)
        position_id = pos.get("id") or pos.get("position_id")
        self.excluded = []
        passed: list[dict[str, Any]] = []

        for candidate in candidates or []:
            talent = _as_dict(candidate)
            talent_id = talent.get("id") or talent.get("talent_id")

            hard_results = judge_hard_conditions(
                talent,
                pos,
                config=config,
                position_id=position_id,
                talent_id=talent_id,
            )

            if is_excluded(hard_results):
                self.excluded.append(
                    {
                        "talent_id": talent_id,
                        "name": talent.get("name"),
                        "status": "FAIL",
                        "reasons": [
                            f"{item.item}：{item.reason}"
                            for item in hard_results
                            if item.status.value == "FAIL"
                        ],
                    }
                )
                continue

            scored = self.score(talent, pos)
            score = apply_unknown_penalty(
                float(scored.get("total_score") or 0), hard_results, config
            )
            passed.append(
                {
                    "talent_id": talent_id,
                    "name": talent.get("name"),
                    "score": score,
                    "dimensions": scored.get("dimensions", {}),
                    # 注意：这里的 highlights 是「命中的技能名」（事实），
                    # 与接口解释文本（自然语言）是两个字段，见 schemas/matching.py 的 matched_skills。
                    "highlights": scored.get("highlights", []),
                    "gaps": scored.get("gaps", []),
                    "passed": True,
                    "reasons": [],
                    # *kwq* 新增：四态明细与数据可信度，供 Service 组装 MatchItem 与解释证据
                    "hard_results": [item.model_dump(mode="json") for item in hard_results],
                    "hard_status": aggregate_status(hard_results).value,
                    "has_unknown": has_unknown(hard_results),
                    "needs_review": has_needs_review(hard_results),
                    "data_completeness": compute_data_completeness(talent),
                }
            )

        return sorted(passed, key=lambda item: float(item.get("score") or 0), reverse=True)


class SoftMatchStrategy(MatchStrategy):
    """加权软匹配：技能 / 经验 / 学历 / 素质加权打分并排序。

    *kwq* 修改（MATCH-019）：软匹配同样产出四态明细，但**不做任何淘汰**。
    修改原因：原实现只打分、不判定，导致 items 里没有 hard_results，stats 的
             unknown / needs_review 恒为 0。同一批候选人在 hybrid 下显示「1 人待补全、
             1 人待复核」，切到 soft 就变成「0」—— 不是变好了，而是根本没算，
             属于误导性统计，也让「四态判定」这一核心能力在 soft 通道下不可见。
    影响范围：返回项新增 hard_results / hard_status / has_unknown / needs_review /
             data_completeness 字段；分数算法与「不淘汰」行为保持不变，
             excluded 仍为空（soft 的语义就是只排序不筛人）。
    """

    name = "soft"

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        rule_config: Any = None,
        **options: Any,
    ) -> None:
        super().__init__(weights, **options)
        self.rule_config = rule_config

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        # *kwq* 新增：延迟导入，理由同 HardFilterStrategy —— 避免 patterns 在加载期反向依赖 services。
        from app.services.matching_rules import (
            HardRuleConfig,
            aggregate_status,
            compute_data_completeness,
            has_needs_review,
            has_unknown,
            judge_hard_conditions,
        )

        config = self.rule_config or HardRuleConfig()
        pos = _as_dict(position)
        position_id = pos.get("id") or pos.get("position_id")

        scored: list[dict[str, Any]] = []
        for candidate in candidates or []:
            talent = _as_dict(candidate)
            talent_id = talent.get("id") or talent.get("talent_id")
            hard_results = judge_hard_conditions(
                talent,
                pos,
                config=config,
                position_id=position_id,
                talent_id=talent_id,
            )
            item = dict(self.shape(candidate, self.score(candidate, pos)))
            item.update(
                {
                    "hard_results": [raw.model_dump(mode="json") for raw in hard_results],
                    "hard_status": aggregate_status(hard_results).value,
                    "has_unknown": has_unknown(hard_results),
                    "needs_review": has_needs_review(hard_results),
                    "data_completeness": compute_data_completeness(talent),
                }
            )
            scored.append(item)

        return sorted(scored, key=lambda item: float(item.get("score") or 0), reverse=True)


class HybridStrategy(MatchStrategy):
    """混合策略：先硬性过滤，再对通过者加权打分排序（生产默认）。"""

    name = "hybrid"

    # *kwq* 新增：透传淘汰名单，便于 Service 组装 MatchRunResponse.excluded（原实现无此出口）。
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        rule_config: Any = None,
        **options: Any,
    ) -> None:
        super().__init__(weights, **options)
        self.rule_config = rule_config
        self.excluded: list[dict[str, Any]] = []

    async def match(self, position: Any, candidates: list[Any]) -> list[dict[str, Any]]:
        # *kwq* 新增：延迟导入（理由同 HardFilterStrategy：避免 patterns 顶层依赖 services）。
        from app.services.matching_rules import HardRuleConfig, apply_unknown_penalty_by_flag

        hard = HardFilterStrategy(weights=self.weights, rule_config=self.rule_config, **self.options)
        passed = await hard.match(position, candidates)
        # *kwq* 新增：把硬过滤淘汰名单上抛给调用方（否则混合策略下淘汰原因彻底丢失）。
        self.excluded = list(hard.excluded)
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

        # *kwq* 新增：把四态明细回填到最终结果，并重新施加 UNKNOWN 降权。
        # 原因：SoftMatchStrategy 会用 compute_match_score 重算分数，
        #      若不重新降权，UNKNOWN 候选人在混合策略下会「恢复」满分，降权形同虚设。
        config = self.rule_config or HardRuleConfig()
        hard_map = {item.get("talent_id"): item for item in passed}
        for item in scored:
            item["passed"] = True
            extra = hard_map.get(item.get("talent_id"))
            if not extra:
                continue
            item["score"] = apply_unknown_penalty_by_flag(
                float(item.get("score") or 0), bool(extra.get("has_unknown")), config
            )
            item["hard_results"] = extra.get("hard_results", [])
            item["hard_status"] = extra.get("hard_status")
            item["has_unknown"] = extra.get("has_unknown", False)
            item["needs_review"] = extra.get("needs_review", False)
            item["data_completeness"] = extra.get("data_completeness", 0.0)
        return sorted(scored, key=lambda item: float(item.get("score") or 0), reverse=True)


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
