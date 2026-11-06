"""
模块：tests/test_analytics_chart.py
职责：图表推断单元测试（纯函数，不依赖数据库 / 模型 / Vanna）。

标签：[Vanna组] [P0]（智能可视化出图：类型选择与数据组装）

============================ 实现思路与代码要点 ============================
覆盖点：
1. 不指定类型时的默认兜底（柱状）；
2. 时间标签 / 中文年月 -> 折线；
3. 占比类问题 -> 饼图，负值时不成立并回退；
4. 单行多指标 -> 雷达；两个类别 + 数值 -> 热力；
5. 显式指定优先，形状不匹配时回退并给警告；
6. 单列 / 全文本 / 空数据不出图；
7. 外观选项透传（主题、尺寸、配色、图例）。
=========================================================================
"""
from __future__ import annotations

import pytest

from app.adapters.nl2sql.chart_builder import build_chart
from app.schemas.analytics import ChartOptions


def test_default_type_is_bar():
    """没有时间、没有占比、不是多维度时，默认柱状图。"""
    chart, warnings = build_chart(
        "各部门人才数量", ["department", "cnt"], [["技术部", 10], ["市场部", 5]]
    )
    assert chart is not None
    assert chart.type == "bar"
    assert chart.x_field == "department"
    assert chart.y_field == "cnt"
    assert warnings == []
    assert chart.series[0]["data"][0] == {"name": "技术部", "value": 10.0}


def test_time_labels_make_line():
    """横轴是 2026-05 这类时间标签时输出折线图。"""
    chart, _ = build_chart("人员数量变化", ["month", "cnt"], [["2026-05", 10], ["2026-06", 12]])
    assert chart is not None
    assert chart.type == "line"


def test_chinese_month_labels_make_line():
    """中文年月标签（2026年5月）同样识别为时间序列。"""
    chart, _ = build_chart("人员数量", ["month", "cnt"], [["2026年5月", 10], ["2026年6月", 12]])
    assert chart is not None
    assert chart.type == "line"


def test_quarter_labels_make_line():
    """季度标签（2026Q2）也识别为时间。"""
    chart, _ = build_chart("季度人数", ["quarter", "cnt"], [["2026Q1", 10], ["2026Q2", 12]])
    assert chart is not None
    assert chart.type == "line"


def test_ratio_question_makes_pie():
    """问题问占比且数值合法时输出饼图。"""
    chart, _ = build_chart("各部门人才占比", ["department", "cnt"], [["技术部", 60], ["市场部", 40]])
    assert chart is not None
    assert chart.type == "pie"


def test_negative_values_fall_back_to_bar():
    """饼图不能表达负值：回退柱状图并给出提示，而不是报错。"""
    chart, warnings = build_chart("各单位人数占比", ["name", "value"], [["A", 10], ["B", -3]])
    assert chart is not None
    assert chart.type == "bar"
    assert any("饼图" in item for item in warnings)


def test_single_row_multi_metric_makes_radar():
    """一行数据 + 多个数值列 -> 雷达图。"""
    rows = [["张三", 80, 90, 70, 60]]
    chart, _ = build_chart("张三能力画像", ["姓名", "沟通", "协作", "技术", "领导力"], rows)
    assert chart is not None
    assert chart.type == "radar"
    assert len(chart.indicators or []) == 4
    assert chart.series[0]["data"][0] == {"name": "沟通", "value": 80.0}


def test_two_dimensions_make_heatmap():
    """两个类别列 + 一个数值列 -> 热力图，且轴标签去重保序。"""
    rows = [
        ["技术部", "初级", 10], ["技术部", "高级", 5],
        ["市场部", "初级", 8], ["市场部", "高级", 3],
    ]
    chart, _ = build_chart("各部门层级人数", ["dept", "level", "cnt"], rows)
    assert chart is not None
    assert chart.type == "heatmap"
    assert chart.x_labels == ["技术部", "市场部"]
    assert chart.y_labels == ["初级", "高级"]
    assert chart.values is not None and len(chart.values) == 4


def test_explicit_type_wins():
    """显式指定饼图时，即使数据不是占比也按饼图画。"""
    chart, _ = build_chart("各部门人数", ["d", "c"], [["A", 1], ["B", 2]], chart_type="pie")
    assert chart is not None
    assert chart.type == "pie"


def test_incompatible_explicit_type_falls_back():
    """显式要热力图但只有两列：回退柱状图并提示。"""
    chart, warnings = build_chart("各部门人数", ["d", "c"], [["A", 1], ["B", 2]], chart_type="heatmap")
    assert chart is not None
    assert chart.type == "bar"
    assert warnings


def test_question_keyword_selects_line():
    """问题里点名“折线图”时直接采用。"""
    chart, _ = build_chart("用折线图展示人数", ["d", "c"], [["A", 1], ["B", 2]])
    assert chart is not None
    assert chart.type == "line"


def test_multi_series_supported():
    """多个数值列时，柱状/折线输出多个系列。"""
    rows = [["2026-05", 1, 2], ["2026-06", 3, 4]]
    chart, _ = build_chart("新增与离职趋势", ["month", "新增", "离职"], rows)
    assert chart is not None
    assert chart.type == "line"
    assert len(chart.series) == 2
    assert chart.y_field is None


def test_single_column_has_no_chart():
    """单列结果画不出图，返回 None 交给前端降级。"""
    chart, _ = build_chart("公司总人数", ["total"], [[10]])
    assert chart is None


def test_text_only_has_no_chart():
    """没有数值列时不出图。"""
    chart, _ = build_chart("部门列表", ["name", "code"], [["技术部", "T01"]])
    assert chart is None


def test_empty_rows_has_no_chart():
    """空结果不出图。"""
    chart, _ = build_chart("各部门人数", ["department", "cnt"], [])
    assert chart is None


def test_options_are_passed_through():
    """主题、尺寸、配色、图例由请求透传到配置，后端不参与渲染。"""
    options = ChartOptions(
        theme="dark", width=1000, height=500,
        palette=["#123456"], show_legend=False,
    )
    chart, _ = build_chart(
        "各部门人数", ["d", "c"], [["A", 1], ["B", 2]], options=options
    )
    assert chart is not None
    assert chart.theme == "dark"
    assert chart.size == {"width": 1000, "height": 500}
    assert chart.style == {"show_legend": False, "palette": ["#123456"]}


@pytest.mark.parametrize("question, expected", [
    ("用折线图展示月度占比趋势", "line"),
    ("用柱状图展示学历比例", "bar"),
    ("用雷达图展示能力占比", "radar"),
])
def test_named_chart_wins_over_ratio_semantics(question, expected):
    """所有明确图名都优先于占比提示，不受关键词表排列影响。"""
    chart, warnings = build_chart(
        question, ["对象", "技术", "沟通", "协作"], [["A", 10, 20, 30]],
    )
    assert chart.type == expected
    assert warnings == []


def test_request_type_wins_over_named_chart_and_ratio():
    """API显式类型仍拥有最高优先级。"""
    chart, _ = build_chart(
        "用折线图展示占比", ["名称", "人数"], [["A", 1], ["B", 2]], chart_type="bar",
    )
    assert chart.type == "bar"


@pytest.mark.parametrize("kind", ["bar", "line"])
def test_null_points_keep_positions_in_each_series(kind):
    """首尾/中间空洞独立保留，不能移位，也不能把空值补成业务零。"""
    chart, _ = build_chart(
        "月度变化", ["月份", "新增", "离职"],
        [["一月", None, 1], ["二月", 0, None], ["三月", 3, 2], ["四月", None, 4]],
        chart_type=kind,
    )
    assert chart.x_labels == ["一月", "二月", "三月", "四月"]
    assert [point["value"] for point in chart.series[0]["data"]] == [None, 0, 3, None]
    assert [point["value"] for point in chart.series[1]["data"]] == [1, None, 2, 4]
    assert all([point["name"] for point in item["data"]] == chart.x_labels for item in chart.series)


def test_null_and_negative_pie_fallback_retains_original_values():
    """饼图不适配时改柱图并说明，空值和负值不能被删掉或取绝对值。"""
    chart, warnings = build_chart(
        "增长占比", ["部门", "增长"], [["A", 10], ["B", None], ["C", -5]],
    )
    assert chart.type == "bar"
    assert warnings
    assert [point["value"] for point in chart.series[0]["data"]] == [10, None, -5]
