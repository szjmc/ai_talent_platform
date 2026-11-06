"""
模块：app/ai/workflows/talent_decision_report.py
职责：人才决策报告工作流（Agent 组完整版——多分组取数 / 风险预警 / 周期报告 / 继任）。

============================ 合并说明（必读） ============================
合并 origin/develop 后，app/ai/workflows/analysis_workflow.py 采用 develop 线实现
（run_analysis_workflow，LangGraph 风格，tests/test_analytics_channel 消费）。

本文件承载 Agent 组完整版 run_analysis：
    - collect_data：按 QUERY_GROUP_MAP 多分组取数（权限档过滤）
    - build_charts / group_charts：图表分组与紧凑化
    - detect_risks：14 条风险规则（低分 / 培训 / 结构 / 活跃 / 数据质量）
    - build_insight_prompt：拼业务推理提示词
    - run_analysis：工作流总入口（支持 report_type 周期报告）
消费方：app/ai/agents/analytics_agent.py（单次分析 + 周期报告）
        app/routers/agent_router.py（/report/period 周期报告接口）

与 analysis_workflow.py 职责分离，两者不冲突。
=========================================================================
"""

from __future__ import annotations

import json
import datetime
import logging
from typing import Any

from app.config.settings import settings
from app.ai.skills.sql_skill import query_database
from app.adapters.nl2sql.chart_builder import build_chart

logger = logging.getLogger(__name__)



DEFAULT_QUERY_MAX_ROWS = 200
MAX_LLM_INPUT_CHARS = 8000

# ---------------------------------------------------------------- 周期报告固定模板（周报 / 月报 / 年报）
# 核心认知：周/月/年报不是三套东西，而是「同一条数据、三种切法」——
#   周报盯执行（快/极简）、月报对目标（中/看趋势）、年报看资产（慢/做战略）。
#   底层就三类数据（测评、培训、个人档案），周期报告不重新找数据，只换「详略 + 读者关心的问题」。
#
# 与「用户问一句话生成报告」的区别：
#   周期报告是系统定期自动生成的例行汇报，有【固定板块骨架】，板块顺序、每块要回答的问题都写死，
#   不随用户输入变化。LLM 只负责「往固定骨架里填当期数据 + 写一句话解读」，不得增删板块。
#
# sections：每个周期报告必须按此顺序输出的固定板块。每个板块字段：
#   title  = 板块标题（写死，LLM 不得改）
#   guide  = 这一板块要回答的业务问题（LLM 据此组织内容）
#   data   = 数据支撑标记：
#            "ok"       -> 当前取数清单 QUERY_GROUP_MAP 能查到真实数据，LLM 正常填数
#            "pending"  -> 当前缺少对应底层数据（如行动表/成本/离职记录），LLM 必须明确
#                          写「本板块需接入 XX 数据后启用」，禁止基于现有数据编造数字
PERIOD_REPORT_TEMPLATE: dict[str, dict[str, Any]] = {
    "weekly": {
        "label": "周报",
        "time_span": "最近 7 天",
        "compare": "本周与上周",
        "position": "执行层 · 快 · 盯变化",
        "soul": "只看「变」，不看「全」——没变的不用重复讲，leader 没时间",
        # 周报 6 板块：一句话摘要 → 指标快照 → 变化异动 → 预警 → 行动跟踪 → 下周关注
        "sections": [
            {"title": "本周一句话摘要", "guide": "本周最该知道的一件大事，用一句话点出", "data": "ok"},
            {"title": "本周关键指标快照", "guide": "测评达标、培训完成、新增风险等本周数字", "data": "ok"},
            {"title": "变化与异动", "guide": "相比上周哪些数字变了、谁的任务到期/新增（依赖培训轨迹时间戳）", "data": "pending"},
            {"title": "预警", "guide": "本周新触发的风险/流失预警（重点，来自 14 条规则）", "data": "ok"},
            {"title": "行动跟踪", "guide": "上周布置的事本周完成没（依赖行动表）", "data": "pending"},
            {"title": "下周关注", "guide": "下周该盯谁、推进什么（P0 即时行动）", "data": "ok"},
        ],
    },
    "monthly": {
        "label": "月报",
        "time_span": "最近 30 天",
        "compare": "本月与上月",
        "position": "管理层 · 中 · 对目标、看趋势",
        "soul": "拿「目标」当尺子——没有目标月报就是周报加长版",
        # 月报 7 板块：摘要 → 目标达成度 → 趋势对比 → 结构月览 → 风险机会 → 行动责任人 → 下月预测
        "sections": [
            {"title": "月度摘要", "guide": "这个月整体一句话总结", "data": "ok"},
            {"title": "目标达成度", "guide": "月度 KPI（复测达标率、培训完成率）vs 目标，差多少（目标值依赖目标配置）", "data": "pending"},
            {"title": "趋势对比", "guide": "本月 vs 上月 vs 三个月前，方向向上还是向下（依赖多月份快照）", "data": "pending"},
            {"title": "人才结构月览", "guide": "人员/测评/培训结构本月的变化", "data": "ok"},
            {"title": "风险与机会", "guide": "当前 P0/P1 风险 + 新增机会点（来自 14 条规则）", "data": "ok"},
            {"title": "行动与责任人", "guide": "本月推进项、下月计划，每条带 owner（依赖行动表）", "data": "pending"},
            {"title": "下月预测", "guide": "按当前节奏下月关键指标会到多少（简单推算）", "data": "pending"},
        ],
    },
    "yearly": {
        "label": "年报",
        "time_span": "最近 365 天",
        "compare": "本年与去年",
        "position": "战略层 · 慢 · 看资产、做规划",
        "soul": "回答「值不值」和「明年怎么投」",
        # 年报 7 板块：摘要 → 资产盘点 → 能力回顾 → 关键人才梯队 → 风险治理 → 成本收益 → 次年战略
        "sections": [
            {"title": "年度执行摘要", "guide": "这一年人才大盘/能力/梯队的一句话总结", "data": "ok"},
            {"title": "人才资产盘点", "guide": "人数、学历、潜力、部门结构的年度变化（依赖年度快照对比）", "data": "pending"},
            {"title": "能力发展回顾", "guide": "测评、培训的年度投入与产出（完成率、达标率、提升幅度）", "data": "ok"},
            {"title": "关键人才与梯队", "guide": "核心人才表现、继任储备、流失情况（依赖离职记录）", "data": "pending"},
            {"title": "年度风险治理", "guide": "今年处理了哪些风险、结果如何", "data": "ok"},
            {"title": "成本与收益", "guide": "培养投入 vs 产出（依赖成本数据）", "data": "pending"},
            {"title": "明年战略规划", "guide": "明年人才战略、投入方向、目标", "data": "ok"},
        ],
    },
}

# 周期类型 -> 合法值集合（供 router 校验 + agent 兜底判断）
PERIOD_TYPES: tuple[str, ...] = tuple(PERIOD_REPORT_TEMPLATE.keys())


def period_label(report_type: str) -> str:
    """根据周期类型 + 当前日期算出「周期标签」，如「2026年第45周」「2026年11月」「2026年度」。

    传进：report_type（weekly / monthly / yearly）
    干了：用标准库计算 ISO 周号 / 月份 / 年份（不手工算时间戳，避免算错）
    传出：给人看的周期标签字符串
    """
    today = datetime.date.today()
    if report_type == "weekly":
        year, week, _ = today.isocalendar()
        return f"{year}年第{week}周"
    if report_type == "monthly":
        return f"{today.year}年{today.month}月"
    if report_type == "yearly":
        return f"{today.year}年度"
    return ""

# ---------------------------------------------------------------- 查询清单（写死的一套问题）
# 每个分组：name 给人看，min_scope 是最低权限档，questions 是要查的一组统计问题。
# 问题措辞必须与 Vanna 训练记忆（data/vanna/corpus.json 的 examples）一字不差，
# 否则 NL2SQL 翻译可能失败。新增维度时，先确认训练记忆里有没有对应问句。
QUERY_GROUP_MAP: dict[str, dict[str, Any]] = {
    "overview": {
        "name": "人才大盘",
        "min_scope": "dept",  # 管理员（dept 及以上）才能查
        "questions": [
            "人才总数是多少？",
            "各学历层次的人才分布？",
            "各潜力等级的人才数量？",
            "各部门路径的人才数量？",
            "人才平均工作年限是多少？",
        ],
    },
    "assessment": {
        "name": "测评分析",
        "min_scope": "dept",
        "questions": [
            "各能力等级测评次数？",
            "测评平均分是多少？",
            # 规则 1.2 低分记录：corpus.json 第 25 条已训练「测评分低于60的次数？」。
            "测评分低于60的次数？",
            # 规则 1.4 维度短板：corpus 无对应维度问句，仍需 Vanna 组补训练记忆，暂不启用。
            # "各能力维度测评平均得分？",                 # 规则 1.4 维度短板（需确认维度名）
        ],
    },
    "training": {
        "name": "培训分析",
        "min_scope": "dept",
        "questions": [
            "各学习状态的轨迹条数？",
            "参与学习的人才数？",
            # TODO(接入时确认)：学习时长风险规则（规则 2.3）需要「学习时长」数据，但尚未确认
            # 「达标时长」的业务定义（corpus.json 明确禁止自行编造阈值）。
            # "各人才的学习时长统计？",                   # 规则 2.3 学习时长不足（待确认达标标准）
        ],
    },
    # 人才结构趋势 + 活跃度：已确认数据源就绪（快照现有 14 个月、event_type 枚举已明确），
    # 问句措辞与 corpus.json 已训练问句对齐（第 13/33/34/35 条）。
    "structure": {
        "name": "人才结构趋势",
        "min_scope": "dept",
        "questions": [
            "每月人才总量趋势？",                        # 规则 3.4 人才总量环比（corpus 第13条）
            # 规则 3.3 人岗匹配低：依赖「匹配分低于60分」问句，corpus 暂无，暂不启用。
            # "匹配分低于 60 分的人才数量？",
        ],
    },
    "activity": {
        "name": "员工活跃度",
        "min_scope": "dept",
        "questions": [
            "最近七天各行为类型次数？",                  # 规则 4.1 活跃度（corpus 第34条）
            "各人才最新学习事件时间？",                  # 规则 4.2 长期无学习（corpus 第33条）
        ],
    },
    "self": {
        "name": "个人档案",
        "min_scope": "self",  # 普通员工（self）就能查
        "questions": [
            "工作年限最多的十位人才？",
            "平均匹配分最高的十位人才？",
        ],
    },
}

# 权限档位：self 最低，all 最高（越大越宽）
_SCOPE_LEVEL: dict[str, int] = {"self": 1, "dept": 2, "dept_sub": 3, "all": 4}

# ---------------------------------------------------------------- 文案映射（去演示化）
# 把 Vanna 返回的「演示人才XX / /910001/ / completed / in_progress」等测试味词汇，
# 换成面向业务的中文表达，让报告更专业。只影响展示文案，不改数据。
_LABEL_MAP: dict[str, str] = {
    "completed": "已完成",
    "in_progress": "进行中",
    "learning": "学习中",
    "not_started": "未开始",
    "待提升": "待提升",
    "良好": "良好",
    "优秀": "优秀",
    "待评分": "待评分",
}
# 部门路径 -> 部门名称（演示数据的 org_path 只有数字，映射成可读名称）
_ORG_PATH_MAP: dict[str, str] = {
    "/910001/": "研发部",
    "/910002/": "人力部",
    "/910003/": "市场部",
}


def _beautify_label(value: str) -> str:
    """把单个体标签「去演示化」：状态词、部门路径映射成业务中文名。"""
    text = str(value)
    if text in _LABEL_MAP:
        return _LABEL_MAP[text]
    if text in _ORG_PATH_MAP:
        return _ORG_PATH_MAP[text]
    return text


# ---------------------------------------------------------------- 权限过滤
def _allowed_groups(data_scope: str) -> list[str]:
    """根据当前用户的权限档，算出「能查哪些分组」。

    传进：data_scope（self / dept / dept_sub / all）
    干了：把「权限不够的分组」过滤掉
    传出：允许查询的分组 key 列表
    """
    level = _SCOPE_LEVEL.get(data_scope, 0)
    allowed: list[str] = []
    for key, group in QUERY_GROUP_MAP.items():
        need = _SCOPE_LEVEL.get(group["min_scope"], 0)
        if level >= need:
            allowed.append(key)
    return allowed


# ---------------------------------------------------------------- 节点 1：取数
async def collect_data(
    question: str,
    data_scope: str = "self",
    max_rows: int = DEFAULT_QUERY_MAX_ROWS,
) -> dict[str, Any]:
    """第 1 节点：按查询清单，循环查多组数据。

    传进：用户问题 question + 权限档 data_scope
    干了：根据权限圈定分组 → 循环调 query_database 查每一组
    传出：{collected: 多份结果, missing: 缺失清单, has_data: 是否取到数}

    说明：这里保持串行。曾尝试用 asyncio.gather 并发，但 Vanna 组 ask 内部
    走 asyncio.to_thread + 同步 generate_sql，实测并发未缩短耗时（LLM 生成 SQL
    是瓶颈，Vanna 内部已有 _QUERY_SLOTS=5 并发闸），故保持简单串行、可读优先。
    """
    allowed = _allowed_groups(data_scope)

    collected: dict[str, Any] = {}
    missing: list[str] = []

    for key in allowed:
        group = QUERY_GROUP_MAP[key]
        for q in group["questions"]:
            label = f"{group['name']}-{q}"
            try:
                # 不传 user_scope，权限从 context 注入，由 sql_skill 内部读取
                res = await query_database(q, max_rows=max_rows)
            except Exception as exc:  # noqa: BLE001 - 单个查询失败：跳过，不拖垮整条链
                missing.append(label)
                collected[label] = {
                    "question": q,
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "error": str(exc),
                }
                continue

            if int(res.get("row_count") or 0) == 0:
                missing.append(label)
            collected[label] = res

    # 只要有一组取到数，就算「有数据」
    has_data = any(
        isinstance(v, dict) and int(v.get("row_count") or 0) > 0 for v in collected.values()
    )

    return {
        "question": question,
        "collected": collected,
        "missing": missing,
        "has_data": has_data,
        "data_scope": data_scope,
    }


# ---------------------------------------------------------------- 分组与紧凑图策略
# 图表按「分析类型」分组（人才大盘 / 测评分析 / 培训分析 / 个人档案），
# 报告渲染时按分组一行一块，每块上方配一句 LLM 生成的总结语。
_GROUP_ORDER: tuple[str, ...] = ("overview", "assessment", "training", "self")

# 「Top N 排名」类问题：条目多时只留前 N 个，图更紧凑，不占大片版面。
# 判断依据：问题里带「十位」「前几名」这类字眼（对应 QUERY_GROUP_MAP 里 self 组的两个问题）。
_TOP_N_KEYWORDS: tuple[str, ...] = ("十位", "前十", "前五", "最多的", "最高的")
_TOP_N_LIMIT = 5


def _group_of_label(label: str) -> str:
    """从 label（形如「人才大盘-各学历层次的人才分布？」）里解析出分组 key。

    传进：图表 label 字符串
    干了：拿前缀「-」之前的部分，反查 QUERY_GROUP_MAP 的名字
    传出：分组 key（overview / assessment / training / self），查不到返回 "other"
    """
    head = label.split("-", 1)[0] if "-" in label else ""
    for key, group in QUERY_GROUP_MAP.items():
        if group["name"] == head:
            return key
    return "other"


def _is_top_n_question(label: str) -> bool:
    """判断这张图是不是「Top N 排名」类（条目多、适合截断成紧凑图）。"""
    return any(word in label for word in _TOP_N_KEYWORDS)


def _trim_top_n(rows: list[list[Any]], limit: int = _TOP_N_LIMIT) -> list[list[Any]]:
    """Top N 排名类数据：只留前 limit 行，图表更紧凑。

    传进：原始 rows（已经按数值降序排好的排名数据）
    干了：截断到前 limit 行
    传出：截断后的 rows
    """
    return rows[:limit]


# ---------------------------------------------------------------- 节点 2：出图
def build_charts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """第 2 节点：数据驱动出图。

    传进：collect_data 的结果
    干了：遍历「有数据」的每组结果，逐组调 build_chart 出图
          - 占比/分布类问题显式指定饼图（chart_type="pie"）
          - 其余让 build_chart 自动推断
          - Top N 排名类问题截断到前 5，图更紧凑
          - 每张图补一个 group 字段，供报告按分组归类
    传出：图表配置列表（有几组数据出几张图，没数据/不适合画图的自动跳过）
    """
    charts: list[dict[str, Any]] = []
    collected = data.get("collected") or {}

    # 这些问题问的是「占比/分布」，用饼图更直观
    _PIE_KEYWORDS = ("分布", "数量", "占比")

    for label, res in collected.items():
        if not isinstance(res, dict):
            continue
        columns = list(res.get("columns") or [])
        rows = [list(r) for r in (res.get("rows") or [])]
        if not columns or not rows:
            continue

        # Top N 排名类：截断到前 5，让图不占大片版面
        if _is_top_n_question(label):
            rows = _trim_top_n(rows)

        # 分布/数量类 -> 强制饼图；否则自动推断
        preferred = "pie" if any(k in label for k in _PIE_KEYWORDS) else "auto"

        try:
            chart, _warnings = build_chart(label, columns, rows, chart_type=preferred)
        except Exception:  # noqa: BLE001 - 单张图失败不拖垮整份报告
            chart = None

        if chart is None:
            continue

        # ChartConfig 是 Pydantic 模型，转成 dict 供前端渲染
        chart_dict = chart.model_dump(mode="json") if hasattr(chart, "model_dump") else dict(chart)

        # 去演示化：把标签、标题里的测试词汇映射成业务名
        chart_dict = _beautify_chart(chart_dict)
        # 打上分组标签，供报告按「分析类型」归类排版
        chart_dict["group"] = _group_of_label(label)
        charts.append(chart_dict)

    return charts


def group_charts(charts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把扁平的图表列表按分组归类，返回有序的分组结构。

    传进：build_charts 产出的图表列表（每张图带 group 字段）
    干了：按 _GROUP_ORDER 的顺序，把同组图收拢到一组，每组带 name / key / charts
    传出：[{key, name, charts:[...]}, ...]，无图的组自动跳过
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for chart in charts:
        key = chart.get("group") or "other"
        grouped.setdefault(key, []).append(chart)

    result: list[dict[str, Any]] = []
    for key in _GROUP_ORDER:
        if key in grouped:
            result.append({
                "key": key,
                "name": QUERY_GROUP_MAP.get(key, {}).get("name", key),
                "charts": grouped[key],
            })
    # 兜底：不在预定义顺序里的组（如 other）排在最后
    for key, group_charts_list in grouped.items():
        if key not in _GROUP_ORDER:
            result.append({
                "key": key,
                "name": key,
                "charts": group_charts_list,
            })
    return result


def _beautify_chart(chart: dict[str, Any]) -> dict[str, Any]:
    """把图表里的测试味标签（演示人才/部门路径/英文状态）换成业务中文名。"""
    if "x_labels" in chart and chart["x_labels"]:
        chart["x_labels"] = [_beautify_label(str(x)) for x in chart["x_labels"]]
    if "series" in chart:
        for series in chart["series"]:
            if isinstance(series, dict) and isinstance(series.get("data"), list):
                for item in series["data"]:
                    if isinstance(item, dict) and "name" in item:
                        item["name"] = _beautify_label(str(item["name"]))
    return chart


# ---------------------------------------------------------------- 风险阈值常量（统一管控，接入真实数据后可在此校准）
# 说明：这些阈值当前是「合理默认值」，基于测试数据拍定，不是最终业务标准。
# 正式接入真实数据后，应由 HR 业务方确认各阈值，再回填此处。改这里一处即全局生效。
class _RiskThresholds:
    """风险阈值常量池。所有阈值集中在这里，便于业务校准与审计。"""

    # 能力短板（fact_assessment_history）
    ASSESS_LOW_RATIO_HIGH = 0.40   # 待提升/待评分占比 ≥40% → 高风险
    ASSESS_LOW_RATIO_MID = 0.25    # 待提升/待评分占比 ≥25% → 中风险
    ASSESS_LOW_SCORE = 60          # 单次测评总分 < 60 视为「低分」（规则 1.2）
    ASSESS_AVG_HIGH = 70           # 测评平均分 < 70 → 高风险（规则 1.3）
    ASSESS_AVG_MID = 80            # 测评平均分 < 80 → 中风险（规则 1.3）
    # 维度短板（规则 1.4）：某维度得分显著低于均值时警示
    DIM_GAP_RATIO = 0.80           # 维度得分 < 全体均值×0.8 → 判定短板（待业务确认）

    # 培训转化（fact_training_track）
    TRAIN_DONE_LOW = 0.30          # 完成率 < 30% → 高风险（规则 2.1）
    TRAIN_DONE_MID = 0.60          # 完成率 < 60% → 中风险（规则 2.1）
    TRAIN_INPROGRESS_HIGH = 0.60   # 在途占比 > 60% → 警示（规则 2.2）
    # TODO(接入时确认)：学习时长「达标标准」无业务定义，规则 2.3 暂不启用

    # 人才结构（dim_talent_wide + fact_talent_snapshot）
    HIGH_POTENTIAL_LOW = 0.20      # 高潜力占比 < 20% → 警示（规则 3.2）
    MATCH_SCORE_LOW = 60           # 匹配分 < 60 视为「低匹配」（规则 3.3）
    MATCH_LOW_RATIO_HIGH = 0.30    # 低匹配占比 > 30% → 高风险（规则 3.3）
    # TODO(接入时确认)：人才总量环比（规则 3.4）需多月份快照，测试数据只有2个月，暂不启用

    # 活跃/流失预警（fact_behavior_event + fact_training_track）
    # TODO(接入时确认)：event_type 枚举、行为事件时间口径未确认，规则 4.1/4.2 暂不启用
    INACTIVE_DAYS = 30             # 长期无学习行为阈值（天）（规则 4.2，待业务确认）


def _safe_int(value: Any) -> int:
    """把任意值安全转成 int，失败返回 0。避免脏数据/None 导致规则崩溃。"""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _safe_float(value: Any) -> float:
    """把任意值安全转成 float，失败返回 0.0。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _sum_two_col(rows: list[list[Any]], match: set[str]) -> tuple[int, int]:
    """对「[类别, 数量]」两列数据做汇总。

    传进：rows（两列：类别、数量）+ match（要累加到 total 里的类别集合）
    干了：遍历累加总数 total，以及 match 命中的类别数量
    传出：(命中数量, 总数)
    """
    hit = 0
    total = 0
    for row in rows:
        if len(row) < 2:
            continue
        key = str(row[0])
        cnt = _safe_int(row[1])
        total += cnt
        if key in match:
            hit += cnt
    return hit, total


def _pick_num(rows: list[list[Any]], index: int = 0) -> float:
    """从单值结果里取一个数字。用于「平均分」「总数」这类单行单列的结果。"""
    if not rows:
        return 0.0
    row = rows[0]
    if len(row) <= index:
        return 0.0
    return _safe_float(row[index])


def _append_risk(
    risks: list[dict[str, Any]],
    name: str,
    level: str,
    phenomenon: str,
    basis: str,
    detail: str,
) -> None:
    """统一追加一条风险。level 为高/中/低，phenomenon 是客观数字事实，business_impact 留给 LLM 补写。"""
    risks.append({
        "risk": name,
        "level": level,
        "phenomenon": phenomenon,
        "basis": basis,
        "detail": detail,
    })


# ---------------------------------------------------------------- 节点 2.5：风险判断
def detect_risks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """风险判断：用写死的规则对聚合指标做判断，不交给 LLM 算数。

    传进：collect_data 的结果（多组聚合数据）
    干了：从 collected 里抽出关键指标，按 5 大类规则判断风险
    传出：风险清单 [{risk, level, phenomenon, basis, detail}]

    覆盖 5 大类 14 条规则（详见 docs/人才数据风险规则设计文档.md）：
        ① 能力短板（测评不达标占比 / 低分记录 / 平均分低 / 维度短板）
        ② 培训转化（完成率低 / 在途积压 / 学习时长不足）
        ③ 人才结构（学历断层 / 潜力断层 / 匹配分低 / 总量下降）
        ④ 活跃/流失预警（活跃骤降 / 长期无学习）
        ⑤ 数据质量（数据缺失）

    注意：部分规则依赖尚未确认的数据维度（维度得分、学习时长、多月份快照、
    行为事件），这些规则的数据源在 QUERY_GROUP_MAP 里暂以注释占位，接入真实数据
    后再启用。当前能跑通的是依赖现有查询维度的规则（1.1/1.3/2.1/2.2/3.2/5.1）。
    """
    risks: list[dict[str, Any]] = []
    collected = data.get("collected") or {}
    T = _RiskThresholds

    # ============ ① 能力短板（fact_assessment_history）============
    # --- 规则 1.1：测评不达标占比过高 ---
    assess_level = collected.get("测评分析-各能力等级测评次数？")
    if isinstance(assess_level, dict):
        rows = assess_level.get("rows") or []
        low, total = _sum_two_col(rows, {"待提升", "待评分"})
        if total > 0:
            ratio = low / total
            if ratio >= T.ASSESS_LOW_RATIO_HIGH:
                _append_risk(
                    risks, "测评能力短板明显", "高",
                    f"测评「待提升/待评分」占比 {ratio:.1%}",
                    f"待提升/待评分 {low} 次 / 总 {total} 次",
                    f"测评「待提升/待评分」占比 {ratio:.1%}，超过 {T.ASSESS_LOW_RATIO_HIGH:.0%} 警戒线，整体能力达标率偏低。",
                )
            elif ratio >= T.ASSESS_LOW_RATIO_MID:
                _append_risk(
                    risks, "测评能力有待关注", "中",
                    f"测评「待提升/待评分」占比 {ratio:.1%}",
                    f"待提升/待评分 {low} 次 / 总 {total} 次",
                    f"测评「待提升/待评分」占比 {ratio:.1%}，建议关注能力建设。",
                )

    # --- 规则 1.2：低分记录（total_score < 60）---
    assess_low_score = collected.get("测评分析-测评分低于60的次数？")
    if isinstance(assess_low_score, dict):
        low_cnt = _safe_int(_pick_num(assess_low_score.get("rows") or []))
        if low_cnt > 0:
            _append_risk(
                risks, "存在低分测评记录", "中",
                f"低分(<{T.ASSESS_LOW_SCORE})测评 {low_cnt} 次",
                f"低分阈值 {T.ASSESS_LOW_SCORE}",
                f"存在 {low_cnt} 次低于 {T.ASSESS_LOW_SCORE} 分的测评，建议关注对应人才。",
            )

    # --- 规则 1.3：测评平均分偏低 ---
    assess_avg = collected.get("测评分析-测评平均分是多少？")
    if isinstance(assess_avg, dict):
        avg = _pick_num(assess_avg.get("rows") or [])
        if avg > 0:
            if avg < T.ASSESS_AVG_HIGH:
                _append_risk(
                    risks, "测评平均分偏低", "高",
                    f"测评平均分 {avg:.1f}",
                    f"警戒线 {T.ASSESS_AVG_HIGH}",
                    f"测评平均分 {avg:.1f}，低于 {T.ASSESS_AVG_HIGH} 分，整体能力水平偏低。",
                )
            elif avg < T.ASSESS_AVG_MID:
                _append_risk(
                    risks, "测评平均分有待提升", "中",
                    f"测评平均分 {avg:.1f}",
                    f"警戒线 {T.ASSESS_AVG_MID}",
                    f"测评平均分 {avg:.1f}，建议关注能力建设。",
                )

    # --- 规则 1.4：维度短板（dimension_scores JSONB）---
    # TODO(接入时确认)：需先确认维度名（专业能力/学习能力/沟通协作）及「各能力维度测评平均得分？」
    # 训练记忆是否存在。当前数据源未启用，规则挂起。启用后逻辑：
    #   读维度得分，若某维度 < 全体维度均值×DIM_GAP_RATIO，则判为该维度短板。

    # ============ ② 培训转化（fact_training_track）============
    # --- 规则 2.1：培训完成率过低 ---
    train = collected.get("培训分析-各学习状态的轨迹条数？")
    if isinstance(train, dict):
        rows = train.get("rows") or []
        done, total = _sum_two_col(rows, {"completed", "已完成"})
        if total > 0:
            ratio = done / total
            if ratio < T.TRAIN_DONE_LOW:
                _append_risk(
                    risks, "培训完成率偏低", "高",
                    f"培训完成率仅 {ratio:.1%}",
                    f"已完成 {done} 条 / 总 {total} 条",
                    f"培训完成率仅 {ratio:.1%}，大量学习轨迹停留在「进行中」。",
                )
            elif ratio < T.TRAIN_DONE_MID:
                _append_risk(
                    risks, "培训完成率待提升", "中",
                    f"培训完成率 {ratio:.1%}",
                    f"已完成 {done} 条 / 总 {total} 条",
                    f"培训完成率 {ratio:.1%}，建议推进在途学习闭环。",
                )

            # --- 规则 2.2：在途积压（in_progress/learning 占比过高）---
            in_progress, _ = _sum_two_col(rows, {"in_progress", "进行中", "learning", "学习中"})
            if total > 0 and in_progress / total > T.TRAIN_INPROGRESS_HIGH:
                _append_risk(
                    risks, "培训在途积压", "中",
                    f"在途轨迹占比 {in_progress / total:.1%}",
                    f"在途 {in_progress} 条 / 总 {total} 条",
                    f"在途学习轨迹占比 {in_progress / total:.1%}，超 {T.TRAIN_INPROGRESS_HIGH:.0%}，存在积压。",
                )

    # --- 规则 2.3：学习时长不足（study_minutes）---
    # TODO(接入时确认)：「达标时长」无业务定义，corpus.json 禁止自行编造阈值。规则挂起。

    # ============ ③ 人才结构（dim_talent_wide + fact_talent_snapshot）============
    # --- 规则 3.1：学历断层 ---
    # TODO(接入时确认)：需明确「断层」的判定口径（如某学历占比 >80% 或某档占比 <5%）。
    # 当前仅有 2 个学历取值（本科/硕士），判断意义有限，规则挂起。

    # --- 规则 3.2：潜力梯队断层（高潜力占比过低）---
    potential = collected.get("人才大盘-各潜力等级的人才数量？")
    if isinstance(potential, dict):
        rows = potential.get("rows") or []
        high, total = _sum_two_col(rows, {"高", "高潜力"})
        if total > 0 and high / total < T.HIGH_POTENTIAL_LOW:
            _append_risk(
                risks, "高潜力人才占比偏低", "中",
                f"高潜力人才占比 {high / total:.1%}",
                f"高潜力 {high} 人 / 总 {total} 人",
                f"高潜力人才占比 {high / total:.1%}，低于 {T.HIGH_POTENTIAL_LOW:.0%}，人才梯队后劲不足。",
            )

    # --- 规则 3.3：人岗匹配偏低（match_avg_score < 60 占比 > 30%）---
    # TODO(接入时确认)：依赖「匹配分低于 60 分的人才数量？」查询，且匹配分阈值需业务确认。规则挂起。

    # --- 规则 3.4：人才总量下降（趋势首尾环比）---
    trend = collected.get("人才结构趋势-每月人才总量趋势？")
    if isinstance(trend, dict):
        rows = trend.get("rows") or []
        # 「每月人才总量趋势？」返回 [月份, 总量] 序列，取首、尾两个月做环比。
        if len(rows) >= 2:
            first = _safe_int(rows[0][1]) if len(rows[0]) >= 2 else 0
            last = _safe_int(rows[-1][1]) if len(rows[-1]) >= 2 else 0
            if first > 0 and last < first:
                drop = (first - last) / first
                if drop >= 0.10:
                    _append_risk(
                        risks, "人才总量下降", "高",
                        f"总量从 {first} 降至 {last}（降幅 {drop:.1%}）",
                        f"趋势首月 {first} → 末月 {last}",
                        f"人才总量环比下降 {drop:.1%}，需关注人才流失或招聘缺口。",
                    )

    # ============ ④ 活跃/流失预警（fact_behavior_event + fact_training_track）============
    # --- 规则 4.1：行为活跃骤降（简化：近七天完全无活跃才预警）---
    activity = collected.get("员工活跃度-最近七天各行为类型次数？")
    if isinstance(activity, dict):
        rows = activity.get("rows") or []
        total_events = 0
        for row in rows:
            if len(row) >= 2:
                total_events += _safe_int(row[1])
        if total_events == 0:
            _append_risk(
                risks, "近期无活跃行为", "中",
                "近七天行为事件数为 0",
                "行为事件表近 7 天无记录",
                "近七天系统内无任何行为事件，可能存在数据断供或员工活跃度异常。",
            )

    # --- 规则 4.2：长期无学习行为 ---
    last_learn = collected.get("员工活跃度-各人才最新学习事件时间？")
    if isinstance(last_learn, dict):
        rows = last_learn.get("rows") or []
        # 「各人才最新学习事件时间？」返回 [人才, 最近学习时间]，统计距今天数超阈值的人数。
        inactive = 0
        today = datetime.date.today()
        for row in rows:
            if len(row) < 2:
                continue
            val = row[1]
            if val is None:
                inactive += 1
                continue
            try:
                if isinstance(val, str):
                    d = datetime.date.fromisoformat(val[:10])
                else:
                    d = val.date() if hasattr(val, "date") else val
                if (today - d).days > T.INACTIVE_DAYS:
                    inactive += 1
            except (ValueError, TypeError):
                continue
        if inactive > 0:
            _append_risk(
                risks, "长期无学习行为", "中",
                f"{inactive} 人超 {T.INACTIVE_DAYS} 天无学习",
                f"长期无学习阈值 {T.INACTIVE_DAYS} 天",
                f"有 {inactive} 人超过 {T.INACTIVE_DAYS} 天无学习行为，建议跟进学习计划。",
            )

    # ============ ⑤ 数据质量（元风险）============
    # --- 规则 5.1：数据缺失 ---
    missing = data.get("missing") or []
    if missing:
        _append_risk(
            risks, "部分维度数据缺失", "中",
            f"{len(missing)} 个维度未取到数据",
            f"缺失维度：{'、'.join(missing[:3])}{' 等' if len(missing) > 3 else ''}",
            f"有 {len(missing)} 个维度的数据缺失，可能影响分析完整性，建议核查数据链路。",
        )

    # 无风险时也给一个「健康」标记，让报告结构完整
    if not risks:
        risks.append({
            "risk": "未发现明显风险",
            "level": "低",
            "phenomenon": "",
            "basis": "",
            "detail": "当前各维度指标均处于健康区间。",
        })

    return risks


# ---------------------------------------------------------------- 节点 3：拼提示词
def build_insight_prompt(data: dict[str, Any], period_type: str | None = None) -> str:
    """第 3 节点：把多份查询结果 + 风险清单拼成 LLM 提示词（本函数不调 LLM）。

    【重要】本函数是「第二次 LLM 业务推理」的输入构造唯一入口：
        analytics_agent._build_business_meta 会读取 report["prompt"]（即本函数的返回值）
        作为业务推理的 user 消息，不要再拷贝一份拼装逻辑，避免两处漂移。
        LLM 调用由 agent 层负责，本函数只拼文本。

    传进：collect_data + build_charts + detect_risks 的结果；period_type 可选（weekly/monthly/yearly）
    干了：只拼「有数据」的部分 + 风险清单 + 按权限加角色约束 + 标注缺失；
          若 period_type 合法，注入对应周期报告的叙事模板（只改叙事口径，不改 schema）
    传出：给 agent 层调 LLM 用的完整提示词字符串
    """
    collected = data.get("collected") or {}
    missing = data.get("missing") or []
    risks = data.get("risks") or []
    data_scope = data.get("data_scope", "self")

    # 周期报告：注入固定板块骨架，要求 LLM 严格按板块组织结论，不得自由发挥
    period_guide = ""
    if period_type in PERIOD_REPORT_TEMPLATE:
        tpl = PERIOD_REPORT_TEMPLATE[period_type]
        section_lines = []
        for i, sec in enumerate(tpl.get("sections") or [], start=1):
            data_flag = sec.get("data", "ok")
            # 有数据：正常要求填数；占位：明确禁止编造，只写「需接入XX数据后启用」
            if data_flag == "pending":
                hint = "（本板块当前缺少对应底层数据，请如实写「本板块需接入相应数据后启用」，禁止编造数字）"
            else:
                hint = sec["guide"]
            section_lines.append(f"{i}. {sec['title']}——{hint}")
        section_text = "\n".join(section_lines)
        period_guide = (
            f"\n\n【{tpl['label']}固定模板】本次是系统定时生成的{tpl['label']}，"
            f"时间跨度为「{tpl['time_span']}」，对比口径为「{tpl['compare']}」。\n"
            f"本报告定位：{tpl.get('position', '')}。写作之魂：{tpl.get('soul', '')}。\n"
            f"必须严格按以下固定板块顺序组织 conclusions（每个板块至少一条结论，"
            f"标题用板块名，不要增删板块、不要改板块顺序）：\n{section_text}\n"
            f"conclusions 里每条结论格式为「【板块名】结论内容」。"
        )

    parts: list[str] = []
    for label, res in collected.items():
        if not isinstance(res, dict) or int(res.get("row_count") or 0) == 0:
            continue
        block = (
            f"【{label}】\n"
            f"列: {json.dumps(list(res.get('columns') or []), ensure_ascii=False)}\n"
            f"数据: {json.dumps(list(res.get('rows') or []), ensure_ascii=False, default=str)}"
        )
        parts.append(block)

    body = "\n\n".join(parts)[:MAX_LLM_INPUT_CHARS]

    # 风险清单：数字已由规则算好，这里只把「事实」交给 LLM，让它做两件事——
    # 1) 定级 P0/P1/P2（业务优先级）；2) 写 business_impact（业务影响）。
    # 注意：不要求 LLM 重新算数字，防止数学幻觉。
    risk_text = ""
    if risks:
        risk_lines = [
            f"- 风险「{r.get('risk')}」：事实={r.get('phenomenon')}；依据={r.get('basis')}"
            for r in risks
        ]
        risk_text = (
            "\n\n【已识别的风险事实（数字已由规则算好，请勿重新计算）】\n"
            + "\n".join(risk_lines)
            + "\n请为每条风险做三件事：①判断业务优先级 priority（P0=直接影响人才上岗/晋升/经营，"
            "P1=造成资源浪费但不直接阻断业务，P2=轻微或潜在）；②补写 business_impact（一句话说明对业务的实际影响）；"
            "③尽量补 drill_hint（下钻提示），根据已有数据指出该风险集中在哪类人群或部门（如数据不足以判断则填空字符串，不要编造）。"
            "优先级要区分开，不要全部判成同一级。"
        )

    # 角色约束：普通员工不输出组织风险 / 团队建议
    role_line = ""
    if data_scope == "self":
        role_line = (
            "当前用户是普通员工，只能描述本人的档案、测评、培训信息，"
            "禁止输出团队大盘、岗位缺口、组织风险和管理建议。\n"
        )

    prompt = role_line + "请根据以下数据写分析结论与决策建议：\n\n" + body + risk_text + period_guide

    # 分组总结：让 LLM 按「分析类型」每组输出一句总结，报告按行归类后每行配一句
    group_names = [
        QUERY_GROUP_MAP.get(key, {}).get("name", key) for key in _GROUP_ORDER
    ]
    group_line = (
        "\n\n【分组总结】请针对以下每一类分析，各写一句总结性的话（直接点出该类的关键结论，"
        "须带具体数字，不要空话）：" + "、".join(group_names) + "。"
    )

    prompt += group_line

    # 图表主次布局：让 LLM 判断每张图是「核心展示 / 背景参考 / 附录明细」
    chart_titles = [c.get("title", "") for c in (data.get("charts") or [])]
    if chart_titles:
        prompt += (
            "\n\n【图表布局】请为下列每张图表判断展示层级 display："
            "main=核心洞察，主页直接展示；fold=背景参考，默认折叠；appendix=明细数据，放附录。"
            "测评/培训/风险相关的图通常为 main；学历/潜力/部门分布等背景图为 fold；"
            "TOP 名单明细为 appendix。图表列表：" + "；".join(chart_titles) + "。"
        )

    # 重点人才：让 LLM 从数据里挑出 TOP3
    prompt += (
        "\n\n【重点人才】请从数据中挑出 3 位最值得关注的人才（综合工作年限与匹配分），"
        "填入 key_talent_top3 数组，用姓名即可。"
    )

    # 元信息与小样本提示：报告需要「上下文 + 样本量提醒」，避免小样本百分比被误读
    prompt += (
        "\n\n【报告元信息】请在 meta 字段里补全报告用途、数据截止日期、数据范围，"
        "并用一句话说明风险警戒线（threshold_desc）。若总样本人数少于 30，"
        "必须在 sample_tip 里写小样本提示，提醒读者百分比受样本量影响、审慎解读。"
    )

    # 可量化 KPI 目标：行动建议必须带可衡量目标，否则无法跟踪
    prompt += (
        "\n\n【量化目标】行动建议（suggestions 的 p0/p1）必须带可量化的 KPI 目标，"
        "例如复测达标率、培训完成率等，并在 meta.kpi_goal 里给出具体数值，避免空泛口号。"
    )

    if missing:
        prompt += f"\n\n【注意】以下数据缺失，请勿编造，仅说明缺失即可：{'；'.join(missing)}"

    prompt += INSIGHT_OUTPUT_SCHEMA
    return prompt


# 结构化输出约束：让 LLM 严格返回 JSON，报告渲染模块无脑取字段
INSIGHT_SYSTEM_PROMPT = (
    "你是一名人才数据分析师。你只能根据下面提供的查询结果回答问题，"
    "严禁编造、推测或补充查询结果中不存在的数据。"
    "如果查询结果为空，请直接说明没有取到相关数据。"
    "结论必须引用具体数字作为依据。"
)
INSIGHT_OUTPUT_SCHEMA = (
    "\n\n请严格按以下 JSON 格式输出，不要输出任何额外文字、解释或 markdown 代码块：\n"
    '{"report_audience": "leader", '
    '"meta": {"report_purpose": "2026Q4人才盘点简报", "data_cutoff": "数据截止日期", '
    '"data_scope": "全公司12名人才", "sample_tip": "小样本提示(样本<30人时必填)", '
    '"threshold_desc": "能力待提升占比>40%标记P0高风险", '
    '"kpi_goal": {"reassess_pass_rate": "≥70%", "train_complete_rate": "≥60%"}}, '
    '"conclusions": ["结论1（做业务解读，不要复述原始数字）", "结论2", "结论3"], '
    '"suggestions": {"p0": ["P0优先行动（带量化KPI目标）"], "p1": ["P1后续行动（带量化KPI目标）"], "warn_if_no_action": "不干预的后果"}, '
    '"group_summaries": {"人才大盘": "一句总结", "测评分析": "一句总结", "培训分析": "一句总结", "个人档案": "一句总结"}, '
    '"risk_list": [{"name": "风险名", "priority": "P0", "phenomenon": "客观数字事实", "business_impact": "业务影响", "drill_hint": "下钻提示(哪类人群/部门集中)"}], '
    '"chart_display": {"图表标题": "main", "图表标题": "fold", "图表标题": "appendix"}, '
    '"key_talent_top3": ["姓名1", "姓名2", "姓名3"], '
    '"appendix_note": "附录明细的一句话解读"}\n'
    "字段规则：\n"
    "1. report_audience：管理者看选 leader，HR 分析看选 hr_analyst；\n"
    "2. meta 必须完整填写：report_purpose 说明盘点用途；data_cutoff 写数据截止日期；data_scope 写数据范围；"
    "threshold_desc 用一句话说明风险警戒线从何而来；\n"
    "3. meta.sample_tip：若总样本人数少于 30，必须写一条小样本提示（如「总样本仅12人，百分比受小样本影响，请审慎解读」）；\n"
    "4. meta.kpi_goal 给出可量化的行动目标：reassess_pass_rate（复测达标率）与 train_complete_rate（培训完成率）各给一个带百分比的目标；\n"
    "5. conclusions 做业务翻译（如「学历结构均衡，无断层风险」），不要罗列数字；\n"
    "6. suggestions 分 p0/p1 两级，每条建议必须带可量化的 KPI 目标（如「复测达标率≥70%」），并给出 warn_if_no_action 一句话后果；\n"
    "7. group_summaries 只写有对应数据的分组，每组一句带数字的总结；\n"
    "8. risk_list 直接引用已识别的风险事实，priority 要区分 P0/P1/P2，不要全部同级；每条尽量补 drill_hint，指出问题集中在哪类人群或部门（若数据无法判断则填空字符串）；\n"
    "9. chart_display 的 key 必须与上面图表列表中的标题完全一致，value 只能是 main/fold/appendix；\n"
    "10. key_talent_top3 挑 3 位综合最优人才，用姓名；\n"
    "11. appendix_note：若附录有明细图表，写一句解读说明这些 TOP 名单代表什么业务含义。"
)


# ---------------------------------------------------------------- 节点 4：报告结构
def build_report(data: dict[str, Any], analysis_type: str = "analysis") -> dict[str, Any]:
    """第 4 节点：组装最终报告结构。

    传进：前面各节点的结果
    干了：整理成结构化字段（含图表列表 + 风险清单 + 权限档 + 缺失清单）
    传出：最终报告 dict
    """
    charts = data.get("charts") or []
    risks = data.get("risks") or []
    report: dict[str, Any] = {
        "analysis_type": analysis_type,
        "question": data.get("question", ""),
        "row_count": sum(
            int(v.get("row_count") or 0)
            for v in (data.get("collected") or {}).values()
            if isinstance(v, dict)
        ),
        "charts": charts,
        "chart_count": len(charts),
        "chart_groups": group_charts(charts),
        "risks": risks,
        "risk_count": len(risks),
        "missing": data.get("missing") or [],
        "data_scope": data.get("data_scope", "self"),
        "query_time": datetime.datetime.now().isoformat(),
    }
    return report


# ---------------------------------------------------------------- 拒答消息
def build_reject_message(data: dict[str, Any]) -> dict[str, Any]:
    """取不到数据时的拒答消息（区分错误类型，对用户友好可引导）。"""
    error_type = data.get("error_type")
    if error_type == "query_error":
        message = "查询执行失败，请简化问题或更换查询维度。"
    elif error_type == "no_permission":
        message = "你没有该维度的数据访问权限。"
    else:  # SQL 成功执行，但 row_count = 0
        message = "暂未匹配到该范围的数据，请检查部门、时间范围是否正确。"
    return {
        "answered": False,
        "message": message,
        "question": data.get("question", ""),
        "error_type": error_type,
    }


# ---------------------------------------------------------------- 总入口
async def run_analysis(
    question: str,
    data_scope: str = "self",
    max_rows: int = DEFAULT_QUERY_MAX_ROWS,
    report_type: str | None = None,
) -> dict[str, Any]:
    """工作流总入口：collect_data -> build_charts -> detect_risks -> build_insight_prompt。

    传进：用户问题 question + 权限档 data_scope；report_type 可选（weekly/monthly/yearly）
    干了：取数 + 出图 + 风险判断 + 拼提示词（不调 LLM，LLM 由基座 run 执行）；
          report_type 合法时，注入对应周期报告叙事模板，并把周期标签写进 report
    传出：
        - 取不到数据：返回 answered=False 的拒答结构
        - 取到数据：返回 answered=True + prompt + data（供基座调 LLM、parse_output 解析）
    """
    data = await collect_data(question, data_scope=data_scope, max_rows=max_rows)

    # 防幻觉闸门：一组数据都没取到，直接拒答，不进 LLM
    if not data.get("has_data"):
        return build_reject_message({"question": question})

    data["charts"] = build_charts(data)
    data["risks"] = detect_risks(data)
    prompt = build_insight_prompt(data, period_type=report_type)
    data["report"] = build_report(data)

    result: dict[str, Any] = {
        "answered": True,
        "question": question,
        "prompt": prompt,
        "data": data,
    }
    # 周期报告：把类型与周期标签带出来，供 agent / 前端展示
    if report_type in PERIOD_REPORT_TEMPLATE:
        result["report_type"] = report_type
        result["period_label"] = period_label(report_type)
    return result
