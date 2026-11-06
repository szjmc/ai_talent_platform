"""
模块：app/adapters/nl2sql/chart_builder.py
职责：图表类型选择与配置组装，被 VannaAdapter._infer_chart 调用。

标签：[Vanna组] [P0]（智能可视化出图：类型选择 + 数据组装）

============================ 实现思路与代码要点 ============================
选择顺序（越靠前优先级越高）：
    1. 请求显式指定 chart_type（bar / line / pie / radar / heatmap）；
    2. 问题里点名要某种图（“柱状图”“饼图”“折线图”“雷达图”“热力图”）；
    3. 问题含「占比 / 比例 / 百分比 / 比重」且数值合法  -> 饼图；
    4. 首列是时间（值类型是日期，或标签是 2026-05 / 2026年5月 / 2026Q2）-> 折线图；
    5. 单行结果 + 至少 3 个数值列                     -> 雷达图；
    6. 三列（两个类别列 + 一个数值列）                -> 热力图；
    7. 其余                                          -> 柱状图。
约束：
- 只输出配置，不画图、不执行任何脚本；
- 数据不适合绘图（不足两列、没有数值列、空结果）时返回 None，由前端降级成表格；
- 显式指定的类型与数据形状不匹配时，自动回退柱状图并在 warnings 里说明，不报错。
=========================================================================
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.schemas.analytics import ChartConfig, ChartOptions

# 后端支持的图表类型（与前端 ECharts 约定一致）
_TYPES = ("bar", "line", "pie", "radar", "heatmap")

# 类型的中文名，用于警告文案
_TYPE_NAMES = {
    "bar": "柱状图",
    "line": "折线图",
    "pie": "饼图",
    "radar": "雷达图",
    "heatmap": "热力图",
}

# 问题里“点名要图”的关键词；按顺序匹配，先命中的优先
_QUESTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pie", ("饼图", "环形图")),
    ("line", ("折线图", "曲线图", "趋势图", "走势图")),
    ("radar", ("雷达图",)),
    ("heatmap", ("热力图", "矩阵图", "交叉图")),
    ("bar", ("柱状图", "柱形图", "条形图")),
)

# 语义提示在所有明确图名之后匹配，避免“折线图展示占比”被选成饼图。
_QUESTION_SEMANTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pie", ("占比", "比例", "百分比", "比重")),
    ("radar", ("能力画像", "维度画像")),
    ("heatmap", ("热力分布",)),
)

# 问题里的时间语义词：与 SQL 的时间函数同时出现才判定为时间序列
_TIME_WORDS = ("趋势", "走势", "月度", "季度", "年度", "逐月", "逐年", "近几个月", "近半年")

# SQL 里出现这些函数/写法，说明查询本身按时间聚合
_SQL_TIME_FUNCS = ("date_trunc", "to_char", "extract(", "::date", "date(", "to_date")

# 时间标签的常见格式：2026-05 / 2026/5/1 / 2026年5月 / 2026Q2 / Q2 2026
_TIME_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^\d{4}-\d{1,2}$",
        r"^\d{4}-\d{1,2}-\d{1,2}$",
        r"^\d{4}/\d{1,2}$",
        r"^\d{4}/\d{1,2}/\d{1,2}$",
        r"^\d{4}年\d{1,2}月$",
        r"^\d{4}年\d{1,2}月\d{1,2}日$",
        r"^\d{4}[Qq][1-4]$",
        r"^[Qq][1-4]\d{4}$",
        r"^\d{4}-\d{1,2}-\d{1,2}T.*$",
    )
)


# ============================================================== 对外入口
def build_chart(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    *,
    chart_type: str = "auto",
    options: ChartOptions | None = None,
    sql: str = "",
) -> tuple[ChartConfig | None, list[str]]:
    """根据查询结果生成图表配置。

    返回 (配置, 警告列表)；配置为 None 表示这份数据不适合画图。
    调用方（VannaAdapter.ask）负责把警告合并进响应，不修改数据本身。
    """
    warnings: list[str] = []
    # 少于两列、没有数据行：画不出有意义的图
    if len(columns) < 2 or not rows:
        return None, warnings

    numeric = _numeric_columns(columns, rows)
    if not numeric:
        return None, warnings

    # 1) 选类型：显式指定 > 问题关键词 > 数据形态推断
    preferred = chart_type if chart_type in _TYPES else _type_from_question(question)
    kind = preferred or _infer_type(question, columns, rows, numeric, sql)

    # 2) 按类型组装数据
    chart = _build(kind, question, columns, rows, numeric)

    # 3) 类型与数据不匹配：回退柱状图，给出可读提示，不让整个问数失败
    if chart is None:
        chart = _build("bar", question, columns, rows, numeric)
        if chart is None:
            return None, warnings
        if preferred or kind != "bar":
            warnings.append(f"{_TYPE_NAMES.get(kind, kind)}不适用于当前数据，已自动改为柱状图")

    _apply_options(chart, options)
    return chart, warnings


# ============================================================== 类型选择
def _infer_type(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
    sql: str,
) -> str:
    """按数据形态推断类型：占比 -> 时间 -> 雷达 -> 热力 -> 柱状。"""
    labels = [_label(row[0]) for row in rows]
    if _ratio_requested(question) and _pie_values_ok(rows, numeric[0]):
        return "pie"
    if _is_time_series(question, labels, sql):
        return "line"
    if _radar_shape(rows, numeric):
        return "radar"
    if _heatmap_shape(columns, rows, numeric):
        return "heatmap"
    return "bar"


def _type_from_question(question: str) -> str | None:
    """先找明确图名，再找占比等语义提示；都没有时返回 None。"""
    text = question or ""
    for groups in (_QUESTION_KEYWORDS, _QUESTION_SEMANTICS):
        for kind, keywords in groups:
            if any(keyword in text for keyword in keywords):
                return kind
    return None


def _ratio_requested(question: str) -> bool:
    """问题是否在问占比类指标。"""
    return any(word in (question or "") for word in ("占比", "比例", "百分比", "比重"))


def _is_time_series(question: str, labels: list[str], sql: str) -> bool:
    """判断横轴是不是时间。

    两种成立方式：
    1. 标签本身就长得像日期（2026-05、2026年5月、2026Q2）——最强信号；
    2. 问题里有“趋势/月度/季度”等词，并且 SQL 里确实用了日期函数。
    这样既不依赖模型措辞，也不会把普通编号误判成时间。
    """
    if len(labels) >= 2 and all(_is_time_label(label) for label in labels):
        return True
    question_hit = any(word in (question or "") for word in _TIME_WORDS)
    sql_hit = any(func in (sql or "").lower() for func in _SQL_TIME_FUNCS)
    return question_hit and sql_hit


def _is_time_label(value: Any) -> bool:
    """单个标签是否时间形态。"""
    if isinstance(value, (datetime, date)):
        return True
    text = str(value).strip()
    return any(pattern.match(text) for pattern in _TIME_PATTERNS)


def _radar_shape(rows: list[list[Any]], numeric: list[int]) -> bool:
    """雷达图：只有一行（一个对象）且至少三个数值维度。"""
    return len(rows) == 1 and len(numeric) >= 3


def _heatmap_shape(columns: list[str], rows: list[list[Any]], numeric: list[int]) -> bool:
    """热力图：三列，前两列是类别、第三列是数值，且两个维度都有多个取值。"""
    if len(columns) != 3 or numeric != [2]:
        return False
    x_count = len({_label(row[0]) for row in rows})
    y_count = len({_label(row[1]) for row in rows})
    return x_count >= 2 and y_count >= 2 and len(rows) >= 4


# ============================================================== 数据组装
def _build(
    kind: str,
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
) -> ChartConfig | None:
    """按类型组装配置；形状不匹配时返回 None。"""
    if kind == "pie":
        return _build_pie(question, columns, rows, numeric)
    if kind == "radar":
        return _build_radar(question, columns, rows, numeric)
    if kind == "heatmap":
        return _build_heatmap(question, columns, rows, numeric)
    return _build_xy(kind, question, columns, rows, numeric)


def _build_xy(
    kind: str,
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
) -> ChartConfig | None:
    """柱状图 / 折线图：首列做横轴，每个数值列一个系列（支持多系列）。"""
    labels = [_label(row[0]) for row in rows]
    series: list[dict[str, Any]] = []
    for index in numeric:
        data = [
            # NULL 是缺失点，不是 0；保留位置让前端和报告都能对齐横轴。
            {"name": labels[position], "value": _number(row[index])}
            for position, row in enumerate(rows)
        ]
        if data:
            series.append({"name": columns[index], "data": data})
    if not series:
        return None
    return ChartConfig(
        type=kind,
        title=_title(question),
        x_field=columns[0],
        y_field=columns[numeric[0]] if len(numeric) == 1 else None,
        x_labels=labels,
        series=series,
    )


def _build_pie(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
) -> ChartConfig | None:
    """饼图：取第一个数值列；有负数或全零时不可用（饼图不能表达负值）。"""
    index = numeric[0]
    values = [_number(row[index]) for row in rows]
    if any(value is None or value < 0 for value in values):
        return None
    if sum(values) <= 0:
        return None
    labels = [_label(row[0]) for row in rows]
    data = [{"name": labels[position], "value": value} for position, value in enumerate(values)]
    return ChartConfig(
        type="pie",
        title=_title(question),
        x_field=columns[0],
        y_field=columns[index],
        x_labels=labels,
        series=[{"name": columns[index], "data": data}],
    )


def _build_radar(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
) -> ChartConfig | None:
    """雷达图：一行数据、多个数值维度；每个维度单独给量程，避免量纲不同被压扁。"""
    if len(rows) != 1 or len(numeric) < 3:
        return None
    row = rows[0]
    values = [_number(row[index]) for index in numeric]
    if any(value is None for value in values):
        return None
    # 所有维度共用一个量程，雷达形状才能真实反映高低差异；
    # 若每个维度各给各的量程，所有点都会落在同一个半径上，图形失去意义。
    common_max = _nice_max(max(values))
    points = [
        {"name": columns[index], "value": value}
        for index, value in zip(numeric, values)
    ]
    indicators = [{"name": columns[index], "max": common_max} for index in numeric]
    return ChartConfig(
        type="radar",
        title=_title(question),
        indicators=indicators,
        series=[{"name": "指标", "data": points}],
    )


def _build_heatmap(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    numeric: list[int],
) -> ChartConfig | None:
    """热力图：x/y 两个类别轴 + 数值，values 用 [x下标, y下标, 数值] 三元组表达。"""
    if len(columns) != 3 or numeric != [2]:
        return None
    x_labels = list(dict.fromkeys(_label(row[0]) for row in rows))
    y_labels = list(dict.fromkeys(_label(row[1]) for row in rows))
    values: list[list[Any]] = []
    for row in rows:
        number = _number(row[2])
        if number is None:
            continue
        values.append([x_labels.index(_label(row[0])), y_labels.index(_label(row[1])), number])
    if not values:
        return None
    return ChartConfig(
        type="heatmap",
        title=_title(question),
        x_field=columns[0],
        y_field=columns[1],
        value_field=columns[2],
        x_labels=x_labels,
        y_labels=y_labels,
        values=values,
    )


# ============================================================== 小工具
def _apply_options(chart: ChartConfig, options: ChartOptions | None) -> ChartConfig:
    """把外观选项写进配置；后端只透传，不参与渲染。"""
    if options is None:
        return chart
    chart.theme = options.theme
    chart.size = {"width": options.width, "height": options.height}
    style: dict[str, Any] = {"show_legend": options.show_legend}
    if options.palette:
        style["palette"] = list(options.palette)
    chart.style = style
    return chart


def _numeric_columns(columns: list[str], rows: list[list[Any]]) -> list[int]:
    """找出可当指标使用的数值列（首列固定当标签，不参与判断）。"""
    result: list[int] = []
    for index in range(1, len(columns)):
        values = [row[index] for row in rows if index < len(row)]
        if not any(value is not None for value in values):
            continue
        # 允许 NULL 空洞，但不允许文本/布尔混进来
        if all(_number(value) is not None for value in values if value is not None):
            result.append(index)
    return result


def _pie_values_ok(rows: list[list[Any]], index: int) -> bool:
    """饼图前置条件：数值全部非空、非负，且总和大于零。"""
    values = [_number(row[index]) for row in rows]
    return all(value is not None and value >= 0 for value in values) and sum(values) > 0


def _number(value: Any) -> float | None:
    """把单元格转成有限浮点数；布尔、文本、NULL、NaN/Inf 一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    return None


def _label(value: Any) -> str:
    """把横轴值转成字符串标签，日期统一成 ISO 形式。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _title(question: str) -> str:
    """图表标题取问题前 80 个字符。"""
    return (question or "").strip()[:80]


def _nice_max(value: float) -> int:
    """雷达图维度量程：留 20% 余量并向上取整，保证数据点不顶格。"""
    if value <= 0:
        return 100
    return max(1, math.ceil(value * 1.2))
