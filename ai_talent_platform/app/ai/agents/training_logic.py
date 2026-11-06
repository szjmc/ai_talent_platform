"""
模块：app/ai/agents/training_logic.py
职责：智能培训赋能 Agent 的纯业务算法层（6 个节点的确定性内核）。

标签：[Dify组] [P1]（培训六节点确定性算法，零第三方依赖）

设计原则：
1. **零第三方依赖**：不 import sqlalchemy / pydantic / fastapi，便于离线单测与演示；
2. **只做计算，不做 IO**：数据由 Service 查好传进来，因此本层可被 Agent 与 Service 共用；
3. **课程实体只在 match_courses 产生**：排期节点拿不到课程池，从信息结构上杜绝编造课程；
4. 所有阈值集中在本文件顶部，改一个数就能做"对照实验"，也便于答辩时解释。

节点映射：
    节点1 短板诊断   diagnose_gaps
    节点2 方案排期   rule_schedule / merge_skeleton / legal_skeleton
    节点3 课程匹配   match_courses / library_rate
    节点4 推送存档   summarize
    节点5 进度跟踪   compute_progress
    节点6 考核复测   assess
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

# ============================================================== 阈值（可调）
GAP_HIGH = 20.0            # 缺口 >= 20 分判 P0
GAP_MID = 8.0              # 缺口 >= 8 分判 P1
KEY_WEIGHT = 0.25          # 岗位关键维度权重阈值，命中直接 P0
MAX_WEEKS = 4              # 默认培训周期（周）
MAX_HOURS_PER_WEEK = 8.0   # 每周学时上限
MAX_CHUNK_HOURS = 4.0      # 单个学习单元最长 4h，避免一门课压满一周
PASS_SCORE = 80.0          # 结课考核及格线
LAG_ALERT = 0.40           # 落后 >= 40% 触发 HR 预警
LAG_WARN = 0.20            # 落后 >= 20% 触发提醒
LEVEL_SLACK = 1            # 课程层级允许偏离目标层级的档数
MIN_EST_HOURS = 3.0        # 单项短板最少预估学时
MAX_EST_HOURS = 8.0        # 单项短板最多预估学时
DEFAULT_REQUIRED = 80.0    # 缺 required 时的兜底目标分
DEFAULT_WEIGHT = 0.2       # 缺 weight 时的兜底权重

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}

# ============================================================== 事件契约
# 为什么在这里再定义一遍而不是 import event_bus：
# 本层承诺「零第三方依赖、零基础设施依赖」，可离线单测；
# 值与 event_bus 的 EVENT_TRAINING_* 常量严格一致，由 test_training_events 守门。
EVENT_PLAN_CREATED = "training.plan_created"
EVENT_LAG_ALERT = "training.lag.alert"
EVENT_LAG_REMIND = "training.lag.remind"
EVENT_RETEST_REQUIRED = "training.retest.required"
EVENT_PLAN_ADJUST = "training.plan.adjust"

# 培训模块产出 / 消费的全部事件（供通知订阅者与注册表复用）
TRAINING_EVENTS: tuple[str, ...] = (
    EVENT_PLAN_CREATED,
    EVENT_LAG_ALERT,
    EVENT_LAG_REMIND,
    EVENT_RETEST_REQUIRED,
    EVENT_PLAN_ADJUST,
)


class CapacityExceeded(ValueError):
    """周期容量装不下全部学时（必须显式暴露，不允许静默丢学时）。"""

    def __init__(self, demand: float, capacity: float, suggest_weeks: int) -> None:
        self.demand = round(demand, 1)
        self.capacity = round(capacity, 1)
        self.suggest_weeks = suggest_weeks
        super().__init__(
            f"短板总学时 {self.demand}h 超出周期容量 {self.capacity}h，"
            f"建议延长至 {suggest_weeks} 周或下调目标分"
        )


class PlanInvalid(ValueError):
    """排期结果不合法（周次越界 / 维度不存在 / 学时非正）。"""


# ============================================================== 通用小工具
def to_float(value: Any, default: float | None = None) -> float | None:
    """把 Decimal / str / None 安全转成 float。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def round1(value: float) -> float:
    """统一保留一位小数，避免浮点尾差让报告忽长忽短。"""
    return round(float(value), 1)


# ============================================================== 节点 1：短板诊断
def compute_delta(gap: dict[str, Any]) -> float | None:
    """计算缺口：优先用 delta，缺失则由 required - current 推导。"""
    delta = to_float(gap.get("delta"))
    if delta is not None:
        return delta
    current = to_float(gap.get("current"))
    required = to_float(gap.get("required"))
    if current is None or required is None:
        return None
    return required - current


def priority_of(delta: float, weight: float) -> str:
    """优先级：先按缺口分档，命中岗位关键维度直接抬到 P0。"""
    if delta >= GAP_HIGH:
        priority = "P0"
    elif delta >= GAP_MID:
        priority = "P1"
    else:
        priority = "P2"
    if weight >= KEY_WEIGHT:
        priority = "P0"
    return priority


def estimate_hours(delta: float) -> float:
    """学时估算：缺口越大给越多，但卡在 3~8 小时。"""
    return round1(max(MIN_EST_HOURS, min(MAX_EST_HOURS, round(delta / 4) + 2)))


def merge_requirement_model(
    gaps: Iterable[dict[str, Any]],
    dimensions: Mapping[str, Any] | None = None,
    weights: Mapping[str, Any] | None = None,
    *,
    only_missing: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """节点 1 前置：把「岗位能力模型」的目标分与权重合并进原始缺口。

    为什么要单独一个函数：
        岗位模型是**外部给的标准**（HR 维护、会升版本），诊断是**本模块的算法**。
        先合并标准、再跑诊断，职责才分得清，也才能单独测「模型变了会怎样」。

    为什么必须容错两种写法：岗位模型是 JSONB/JSON，是人手工或 AI 填的，
    实际数据里两种形态都出现过——
        {"需求分析": 80}                              只给目标分
        {"需求分析": {"required": 80, "weight": 0.25}} 目标分 + 权重

    优先级（高 → 低）：调用方显式给的值 > 岗位能力模型 > 诊断时的默认值。

    :param only_missing: 默认 True，只补「调用方没给」的字段。
                         设为 False 时岗位模型强制覆盖（用于「换岗位重新诊断」的场景）。
    :param weights: 独立权重字典，优先级高于 dimensions 里内嵌的 weight
    :return: (合并后的缺口列表, 从岗位模型补到值的维度数)；
             为 0 时调用方应把来源标注为 explicit 或 default。
    """
    dim_map = dimensions if isinstance(dimensions, Mapping) else {}
    weight_map = weights if isinstance(weights, Mapping) else {}
    merged: list[dict[str, Any]] = []
    filled = 0

    for raw in gaps or []:
        item = dict(raw)
        dimension = str(item.get("dimension") or "").strip()
        spec = dim_map.get(dimension) if dimension else None

        required: float | None = None
        weight: float | None = None
        if isinstance(spec, Mapping):
            required = to_float(spec.get("required"))
            weight = to_float(spec.get("weight"))
        elif spec is not None:
            required = to_float(spec)  # 简写：{"维度": 80}

        # 独立 weights 字段覆盖 dimensions 内嵌权重
        override = to_float(weight_map.get(dimension)) if dimension else None
        if override is not None:
            weight = override

        hit = False
        if required is not None and (not only_missing or to_float(item.get("required")) is None):
            item["required"] = required
            hit = True
        if weight is not None and (not only_missing or to_float(item.get("weight")) is None):
            item["weight"] = max(0.0, min(1.0, weight))
            hit = True
        if hit:
            filled += 1
        merged.append(item)

    return merged, filled


def diagnose_gaps(raw_gaps: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """节点 1：剔除已达标项、算优先级与预估学时、按优先级排序。

    排序规则：先 P0→P2，同档内按「缺口 × 权重」降序（可解释：既要补得多，也要岗位看重）。
    已达标（delta <= 0）不排课，这是"个性化"的第一层。

    兜底顺序很重要：**先补默认值，再算缺口**。
    早期实现是先调 compute_delta（它缺 required 就返回 None）再补默认值，
    结果「只给了当前分、没给目标分」的输入会被整条丢掉——
    而岗位能力模型恰恰是「只补目标分」的来源，顺序一错就等于岗位模型没生效。
    """
    result: list[dict[str, Any]] = []
    for raw in raw_gaps or []:
        dimension = str(raw.get("dimension") or "").strip()
        if not dimension:
            continue

        current = to_float(raw.get("current"))
        if current is None:
            continue

        weight = to_float(raw.get("weight"), DEFAULT_WEIGHT)
        weight = DEFAULT_WEIGHT if weight is None else max(0.0, min(1.0, weight))
        required = to_float(raw.get("required"), DEFAULT_REQUIRED)
        required = DEFAULT_REQUIRED if required is None else required

        delta = round1(required - current)
        if delta <= 0:
            continue

        result.append(
            {
                "dimension": dimension,
                "current": current,
                "required": required,
                "delta": delta,
                "weight": weight,
                "priority": priority_of(delta, weight),
                "est_hours": estimate_hours(delta),
            }
        )

    result.sort(key=lambda g: (PRIORITY_RANK[g["priority"]], -(g["delta"] * g["weight"])))
    return result


def demand_hours(gaps: Sequence[dict[str, Any]]) -> float:
    """全部短板需要的总学时。"""
    return round1(sum(float(g.get("est_hours") or 0) for g in gaps))


# ============================================================== 节点 2：方案排期
def rule_schedule(
    gaps: Sequence[dict[str, Any]],
    max_weeks: int = MAX_WEEKS,
    max_hours_per_week: float = MAX_HOURS_PER_WEEK,
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """节点 2：规则排期——按优先级顺序把学时塞进周次，每周不超上限。

    :param strict: True 时容量不足直接抛 CapacityExceeded（推荐，错误必须可见）；
                   False 时自动延长周期（最多 12 周）以保证一定能排出结果。
    """
    if not gaps:
        return []
    max_hours_per_week = max(1.0, float(max_hours_per_week))
    demand = demand_hours(gaps)
    capacity = max_weeks * max_hours_per_week
    if demand > capacity:
        suggest = math.ceil(demand / max_hours_per_week)
        if strict:
            raise CapacityExceeded(demand, capacity, suggest)
        max_weeks = min(12, max(suggest, max_weeks))

    items: list[dict[str, Any]] = []
    week, used = 1, 0.0
    for gap in gaps:
        left = float(gap.get("est_hours") or 0)
        while left > 0:
            chunk = min(left, MAX_CHUNK_HOURS)
            if used + chunk > max_hours_per_week + 1e-9:
                week, used = week + 1, 0.0
            if week > max_weeks:
                raise CapacityExceeded(demand, capacity, math.ceil(demand / max_hours_per_week))
            items.append(
                {
                    "week": week,
                    "dimension": gap["dimension"],
                    "hours": round1(chunk),
                    "reason": f"{gap['dimension']}缺口 {gap['delta']:g} 分（{gap['priority']} 优先）",
                }
            )
            used = round1(used + chunk)
            left = round1(left - chunk)
    return items


def merge_skeleton(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """同周同维度合并，让方案看起来像人排的（而不是碎片）。"""
    bucket: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        key = (int(item["week"]), str(item["dimension"]))
        if key in bucket:
            bucket[key]["hours"] = round1(bucket[key]["hours"] + float(item["hours"]))
        else:
            bucket[key] = {
                "week": key[0],
                "dimension": key[1],
                "hours": round1(float(item["hours"])),
                "reason": item.get("reason") or "",
            }
    return [bucket[key] for key in sorted(bucket)]


def legal_skeleton(
    items: Any,
    dimensions: Iterable[str],
    max_weeks: int = MAX_WEEKS,
    max_hours_per_week: float = MAX_HOURS_PER_WEEK,
) -> bool:
    """校验 LLM 排期是否合法：维度必须在短板内、周次在范围内、学时为正且不超上限。"""
    if not isinstance(items, list) or not items:
        return False
    allowed = set(dimensions)
    week_hours: dict[int, float] = {}
    for item in items:
        if not isinstance(item, dict):
            return False
        dimension = str(item.get("dimension") or "")
        if dimension not in allowed:
            return False
        try:
            week = int(item.get("week"))
            hours = float(item.get("hours"))
        except (TypeError, ValueError):
            return False
        if not (1 <= week <= max_weeks) or hours <= 0:
            return False
        week_hours[week] = week_hours.get(week, 0.0) + hours
    # 每周总学时不得超出上限（留 0.1 容差）
    return all(hours <= max_hours_per_week + 0.1 for hours in week_hours.values())


# ============================================================== 节点 3：课程匹配
_LEVEL_CN = {"入门": 1, "初级": 1, "中级": 2, "高级": 3, "资深": 4, "专家": 4}
_LEVEL_RE = re.compile(r"^L?\s*([1-5])$", re.IGNORECASE)


def parse_level(value: Any) -> int:
    """把课程层级统一成 1~5 的整数（兼容 L3 / 3 / 高级 等写法）。"""
    if value is None or value == "":
        return 3
    if isinstance(value, (int, float)):
        return max(1, min(5, int(value)))
    text = str(value).strip()
    match = _LEVEL_RE.match(text)
    if match:
        return int(match.group(1))
    for word, level in _LEVEL_CN.items():
        if word in text:
            return level
    return 3


def target_level(required: Any) -> int:
    """按岗位要求分反推课程目标层级。"""
    score = to_float(required, DEFAULT_REQUIRED) or DEFAULT_REQUIRED
    if score >= 85:
        return 4
    if score >= 75:
        return 3
    return 2


# 维度名归一化：这些后缀不影响「是不是同一个能力项」
_DIMENSION_SUFFIXES = ("能力项", "能力", "技能", "知识", "方向", "素养", "水平")
# 相似度阈值：低于此值宁可降级为外部建议，也不挂错课（错课比没课更误导学员）
DIMENSION_SIMILARITY = 0.8


def normalize_dimension(value: Any) -> str:
    """把维度名 / 课程分类归一化，消除录入差异带来的假性不匹配。"""
    text = re.sub(r"\s+", "", str(value or "")).lower()
    text = re.sub(r"[（(].*?[)）]", "", text)
    text = text.replace("／", "/").replace("：", ":")
    for suffix in _DIMENSION_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _dimension_alike(left: str, right: str) -> bool:
    """两个已归一化的维度名是否指同一件事。

    2 字短名（如「沟通」）只接受精确相等，否则「数据」会命中「数据分析」，
    把不相干的课程挂进计划——这种错配比降级更糟。
    """
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < 3:
        return False
    if left.startswith(right) or right.startswith(left):
        return True
    return SequenceMatcher(None, left, right).ratio() >= DIMENSION_SIMILARITY


def course_match_dimension(course: dict[str, Any], dimension: str) -> bool:
    """课程是否属于该能力维度：category 精确相同，或 skill_tags 命中。

    **必须一字不差**（这是本模块的显式约束，不是疏漏）：排课宁可降级为
    「外部建议」，也不能凭相似度把课挂到错的维度上——错配的课程比没课更误导人。
    录入差异由 `create_course` 的维度提示 + 排课降级日志负责暴露，见 `_dimension_alike`。
    """
    if str(course.get("category") or "").strip() == dimension:
        return True
    tags = {str(tag).strip() for tag in (course.get("skill_tags") or [])}
    return dimension in tags


def _pick_course(
    dimension: str,
    required: Any,
    courses: Sequence[dict[str, Any]],
    learned: set[int],
    taken: set[int],
    level_slack: int = LEVEL_SLACK,
) -> dict[str, Any] | None:
    """只从课程池里选：维度一致 → 未学过未占用 → 层级贴合 → 学时优先。"""
    target = target_level(required)
    pool = [
        course
        for course in courses
        if course_match_dimension(course, dimension)
        and int(course.get("course_id") or 0) not in learned
        and int(course.get("course_id") or 0) not in taken
    ]
    if not pool:
        return None
    level = {int(c.get("course_id") or 0): parse_level(c.get("level")) for c in pool}
    fit = [c for c in pool if target - level_slack <= level[int(c.get("course_id") or 0)] <= target]
    if not fit:
        fit = sorted(pool, key=lambda c: abs(level[int(c.get("course_id") or 0)] - target))[:1]
    return sorted(
        fit,
        key=lambda c: (
            abs(level[int(c.get("course_id") or 0)] - target),
            -float(c.get("duration_hours") or 0),
        ),
    )[0]


def match_courses(
    skeleton: Sequence[dict[str, Any]],
    gaps: Sequence[dict[str, Any]],
    courses: Sequence[dict[str, Any]],
    learned_ids: Iterable[int] = (),
    level_slack: int = LEVEL_SLACK,
) -> list[dict[str, Any]]:
    """节点 3：把排期里的"维度"落成课程库里的真实课程。

    - 库里有课 → 挂 course_id / title / level，is_platform=True；
    - 库里没课 → 降级为"外部学习方向建议"，is_platform=False（绝不编造课程名）；
    - 同一维度跨多周时复用同一门课，避免重复选课。
    """
    required_map = {g["dimension"]: g.get("required") for g in gaps}
    learned = {int(cid) for cid in learned_ids if cid is not None}
    taken: set[int] = set()
    picked: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []

    for row in skeleton:
        dimension = row["dimension"]
        course = picked.get(dimension)
        if course is None:
            course = _pick_course(
                dimension, required_map.get(dimension), courses, learned, taken, level_slack
            )
            if course is not None:
                course_id = int(course.get("course_id") or 0)
                taken.add(course_id)
                picked[dimension] = course

        if course is not None:
            items.append(
                {
                    **row,
                    "course_id": int(course.get("course_id") or 0),
                    "title": course.get("title"),
                    "level": parse_level(course.get("level")),
                    "duration_hours": to_float(course.get("duration_hours"), 0.0),
                    "is_platform": True,
                    "reason": f"{row.get('reason', '')}；匹配课程 L{parse_level(course.get('level'))}",
                }
            )
        else:
            target = target_level(required_map.get(dimension))
            # 降级不是错误，但必须可观测：运营要对账「是真没课，还是课名写错了一个字」。
            # 只写日志、不改匹配结果——相似课程绝不自动挂进来。
            categories = sorted({str(c.get("category")) for c in courses if c.get("category")})
            near = [
                name
                for name in categories
                if _dimension_alike(normalize_dimension(name), normalize_dimension(dimension))
            ]
            logger.warning(
                "维度「%s」在课程库无精确匹配，已降级为外部建议；疑似应为：%s；库内分类：%s",
                dimension,
                near or "无",
                categories,
            )
            items.append(
                {
                    **row,
                    "course_id": None,
                    "title": None,
                    "level": target,
                    "duration_hours": 0.0,
                    "is_platform": False,
                    "reason": f"{row.get('reason', '')}；平台暂无该维度课程，建议外部学习",
                    "external_suggestion": (
                        f"{dimension}：目标达到 L{target}，建议公开课/外部机构"
                        f"（非平台课程，需 HR 线下采购）"
                    ),
                }
            )
    return items


def library_rate(items: Sequence[dict[str, Any]]) -> float | None:
    """课程来自平台库的比例：无平台课程时返回 None（不得对外宣称 100%）。"""
    if not items:
        return 0.0
    total = len(items)
    hit = sum(1 for item in items if item.get("is_platform"))
    return hit / total if total else 0.0


# ============================================================== 节点 4：方案摘要
def summarize(
    talent_name: str,
    gaps: Sequence[dict[str, Any]],
    items: Sequence[dict[str, Any]],
    total_weeks: int,
    total_hours: float,
    external_count: int,
    pass_score: float = PASS_SCORE,
) -> str:
    """节点 4：生成一句话方案说明（前端卡片 / 推送文案共用）。"""
    dims = "、".join(dict.fromkeys(str(item["dimension"]) for item in items))
    text = (
        f"针对 {talent_name} 的 {len(gaps)} 项能力短板，围绕 {dims} 制定 "
        f"{total_weeks} 周方案，共 {round1(total_hours)} 学时，"
        f"第 {total_weeks} 周结课考核（≥{pass_score:g} 分）。"
    )
    if external_count:
        text += f"其中 {external_count} 个学习单元平台无对应课程，已转为外部学习方向建议（非平台课程）。"
    return text


# ============================================================== 节点 5：进度跟踪
def compute_progress(
    total_hours: float,
    elapsed_ratio: float,
    completed_h: float,
    *,
    lag_alert: float = LAG_ALERT,
    lag_warn: float = LAG_WARN,
) -> dict[str, Any]:
    """节点 5：按"时间进度 vs 完成学时"判定正常 / 提醒 / HR 预警。

    注意：本函数只做计算，事件由 Service 发布（图里不能写 while 循环）。
    """
    total = float(total_hours or 0)
    if total <= 0:
        return {
            "expected_h": 0.0,
            "completed_h": 0.0,
            "lag_ratio": 0.0,
            "completion": 0.0,
            "alert": "无学习任务",
        }
    elapsed = max(0.0, min(1.0, float(elapsed_ratio or 0)))
    done = max(0.0, float(completed_h or 0))
    expected = total * elapsed
    lag = (expected - done) / total
    completion = min(1.0, done / total)

    if done >= total:
        alert = "已按期完成"
    elif lag >= lag_alert:
        alert = "HR预警"
    elif lag >= lag_warn:
        alert = "提醒"
    else:
        alert = "正常"

    return {
        "expected_h": round1(expected),
        "completed_h": round1(done),
        "lag_ratio": round(lag, 2),
        "completion": round(completion, 2),
        "alert": alert,
    }


def lag_event(alert: str, plan_id: int | str, progress: dict[str, Any]) -> dict[str, Any] | None:
    """节点 5：按判定结果产出事件（None 表示不需要发）。

    调用方约定：只有定时扫描（lag-scan）才发布本函数产出的事件；
    查询接口（GET progress）只算不推，否则页面刷新一次就重复推送一次。
    """
    if alert == "HR预警":
        return {
            "type": EVENT_LAG_ALERT,
            "level": "HR预警",
            "plan_id": plan_id,
            "detail": (
                f"落后计划 {progress['lag_ratio']:.0%}"
                f"（应完成 {progress['expected_h']}h，实际 {progress['completed_h']}h）"
            ),
        }
    if alert == "提醒":
        return {
            "type": EVENT_LAG_REMIND,
            "level": "提醒",
            "plan_id": plan_id,
            "detail": f"落后计划 {progress['lag_ratio']:.0%}，建议本周补齐",
        }
    return None


# ============================================================== 节点 6：考核复测
def assess(
    score: float | None,
    *,
    pass_score: float = PASS_SCORE,
    auto_retest: bool = True,
    plan_id: int | str | None = None,
    talent_id: int | None = None,
    dimensions: Sequence[str] = (),
) -> dict[str, Any]:
    """节点 6：考核判定 + 生成复测事件（事件回流测评 Agent，形成闭环）。"""
    if score is None:
        return {"passed": None, "assessment": "未提交考核", "events": [], "need_human": False}

    value = float(score)
    if value >= pass_score:
        events: list[dict[str, Any]] = []
        if auto_retest:
            events.append(
                {
                    "type": EVENT_RETEST_REQUIRED,
                    "level": "正常",
                    "plan_id": plan_id,
                    "talent_id": talent_id,
                    "dims": list(dimensions),
                    "detail": f"考核 {value:g} 分通过，建议 2 周后复测以验证能力真实提升",
                }
            )
        return {
            "passed": True,
            "assessment": f"通过（{value:g} 分）",
            "events": events,
            "need_human": False,
        }

    return {
        "passed": False,
        "assessment": f"未通过（{value:g} 分）",
        "events": [
            {
                "type": EVENT_PLAN_ADJUST,
                "level": "提醒",
                "plan_id": plan_id,
                "detail": f"考核 {value:g} 分未达 {pass_score:g}，建议延长 2 周并补强薄弱维度",
            }
        ],
        "need_human": True,
    }


__all__ = [
    "CapacityExceeded",
    "PlanInvalid",
    "EVENT_LAG_ALERT",
    "EVENT_LAG_REMIND",
    "EVENT_PLAN_ADJUST",
    "EVENT_PLAN_CREATED",
    "EVENT_RETEST_REQUIRED",
    "TRAINING_EVENTS",
    "GAP_HIGH",
    "GAP_MID",
    "KEY_WEIGHT",
    "MAX_WEEKS",
    "MAX_HOURS_PER_WEEK",
    "PASS_SCORE",
    "LAG_ALERT",
    "LAG_WARN",
    "assess",
    "compute_delta",
    "compute_progress",
    "course_match_dimension",
    "demand_hours",
    "diagnose_gaps",
    "estimate_hours",
    "lag_event",
    "legal_skeleton",
    "library_rate",
    "match_courses",
    "merge_requirement_model",
    "merge_skeleton",
    "parse_level",
    "priority_of",
    "rule_schedule",
    "summarize",
    "target_level",
    "to_float",
]
