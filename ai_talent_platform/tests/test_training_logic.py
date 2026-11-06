"""
模块：tests/test_training_logic.py
职责：智能培训赋能 Agent 六节点纯逻辑单测（零数据库、零 LLM、零网络）。

标签：[Dify组] [P1]（培训六节点纯逻辑单测，零依赖）

为什么单独测逻辑层：
- 六个节点的正确性全在 training_logic 里，先把这层钉死，Service / Agent 才可信；
- 不依赖 MySQL / LLM，CI 与本地都能秒跑：`pytest tests/test_training_logic.py -q`
"""

from __future__ import annotations

import pytest

from app.ai.agents import training_logic as L

GAPS = [
    {"dimension": "需求分析", "current": 62, "required": 85, "weight": 0.30},
    {"dimension": "数据分析", "current": 55, "required": 80, "weight": 0.25},
    {"dimension": "SQL与数据库", "current": 48, "required": 70, "weight": 0.15},
    {"dimension": "项目管理", "current": 78, "required": 85, "weight": 0.20},
    {"dimension": "沟通表达", "current": 82, "required": 80, "weight": 0.10},
]

COURSES = [
    {"course_id": 1, "title": "需求优先级与MVP设计", "category": "需求分析", "level": "L3",
     "duration_hours": 8, "skill_tags": ["需求分析"]},
    {"course_id": 2, "title": "数据看板与可视化", "category": "数据分析", "level": "L3",
     "duration_hours": 8, "skill_tags": ["数据分析"]},
    {"course_id": 3, "title": "SQL入门到熟练", "category": "SQL与数据库", "level": "L2",
     "duration_hours": 6, "skill_tags": ["SQL与数据库"]},
    {"course_id": 4, "title": "敏捷与Scrum实战", "category": "项目管理", "level": "L3",
     "duration_hours": 4, "skill_tags": ["项目管理"]},
]


# ============================================================== 节点 1
def test_node1_诊断剔除已达标项():
    gaps = L.diagnose_gaps(GAPS)
    dims = [g["dimension"] for g in gaps]
    assert "沟通表达" not in dims, "已达标（delta<=0）的维度不应排课"
    assert len(gaps) == 4


def test_node1_优先级与排序():
    gaps = L.diagnose_gaps(GAPS)
    assert [g["priority"] for g in gaps] == ["P0", "P0", "P0", "P2"]
    # 同档内按 缺口×权重 降序：需求分析 23×0.3=6.9 > 数据分析 25×0.25=6.25 > SQL 22×0.15=3.3
    assert [g["dimension"] for g in gaps][:3] == ["需求分析", "数据分析", "SQL与数据库"]


def test_node1_关键权重直接抬到P0():
    # 缺口很小但岗位权重 >=0.25，仍应是 P0
    gaps = L.diagnose_gaps([{"dimension": "沟通表达", "current": 78, "required": 80, "weight": 0.30}])
    assert gaps[0]["priority"] == "P0"


# ============================================================== 节点 2
def test_node2_四周边界与学时上限():
    gaps = L.diagnose_gaps(GAPS)
    rows = L.rule_schedule(gaps, max_weeks=4, max_hours_per_week=8)
    assert {row["week"] for row in rows} == {1, 2, 3, 4}
    for week in (1, 2, 3, 4):
        total = sum(row["hours"] for row in rows if row["week"] == week)
        assert total <= 8 + 1e-9, f"第 {week} 周学时超过上限：{total}"


def test_node2_容量不足必须报错():
    gaps = L.diagnose_gaps(GAPS)
    with pytest.raises(L.CapacityExceeded):
        L.rule_schedule(gaps, max_weeks=4, max_hours_per_week=4)


def test_node2_LLM排期非法被拦():
    assert L.legal_skeleton([], ["需求分析"], 4, 8) is False
    assert L.legal_skeleton([{"week": 9, "dimension": "需求分析", "hours": 4}], ["需求分析"], 4, 8) is False
    assert L.legal_skeleton([{"week": 1, "dimension": "不存在", "hours": 4}], ["需求分析"], 4, 8) is False
    assert L.legal_skeleton([{"week": 1, "dimension": "需求分析", "hours": 4}], ["需求分析"], 4, 8) is True


# ============================================================== 节点 3
def test_node3_课程全部来自库():
    gaps = L.diagnose_gaps(GAPS)
    skeleton = L.merge_skeleton(L.rule_schedule(gaps))
    items = L.match_courses(skeleton, gaps, COURSES)
    assert all(item["is_platform"] for item in items)
    assert L.library_rate(items) == 1.0
    assert {item["course_id"] for item in items} == {1, 2, 3, 4}


def test_node3_空课程库降级不编造():
    gaps = L.diagnose_gaps(GAPS)
    skeleton = L.merge_skeleton(L.rule_schedule(gaps))
    items = L.match_courses(skeleton, gaps, [])
    assert all(not item["is_platform"] for item in items)
    assert all(item["course_id"] is None and item["title"] is None for item in items)
    assert L.library_rate(items) == 0.0


def test_node3_跳过已学课程():
    gaps = L.diagnose_gaps(GAPS[:1])
    skeleton = L.merge_skeleton(L.rule_schedule(gaps))
    items = L.match_courses(skeleton, gaps, COURSES, learned_ids=[1])
    assert items[0]["course_id"] != 1, "已学过的课程不应重复推荐"


# ============================================================== 节点 5
def test_node5_滞后判定分级():
    # 总 28h，时间过 60% → 应完成 16.8h
    assert L.compute_progress(28, 0.6, 4.0)["alert"] == "HR预警"    # 落后 46%
    assert L.compute_progress(28, 0.6, 11.0)["alert"] == "提醒"     # 落后约 21%
    assert L.compute_progress(28, 0.6, 16.0)["alert"] == "正常"     # 落后约 3%
    assert L.compute_progress(28, 0.6, 28.0)["alert"] == "已按期完成"


def test_node5_无任务不除零():
    result = L.compute_progress(0, 0.5, 0)
    assert result["alert"] == "无学习任务"
    assert result["completion"] == 0.0


# ============================================================== 节点 6
def test_node6_达标发复测事件():
    result = L.assess(88, plan_id=1, talent_id=10086, dimensions=["需求分析"])
    assert result["passed"] is True
    assert [event["type"] for event in result["events"]] == ["training.retest.required"]
    assert result["events"][0]["dims"] == ["需求分析"]


def test_node6_关闭开关不发事件():
    result = L.assess(88, auto_retest=False)
    assert result["events"] == []


def test_node6_未达标需人工():
    result = L.assess(59)
    assert result["passed"] is False
    assert result["need_human"] is True
    assert result["events"][0]["type"] == "training.plan.adjust"


# ============================================================== 岗位能力模型（节点 1 前置）
# 为什么单独测这一层：岗位模型是「外部标准」，诊断是「本模块算法」。
# 标准合并错了，后面排期、匹配、学时全都会跟着错，而且很难看出来。
def test_岗位模型_只给当前分时补齐目标分与权重():
    gaps = [{"dimension": "需求分析", "current": 60}]
    model = {"需求分析": {"required": 85, "weight": 0.30}}

    merged, filled = L.merge_requirement_model(gaps, model)

    assert filled == 1
    assert merged[0]["required"] == 85.0
    assert merged[0]["weight"] == 0.30
    assert merged[0]["current"] == 60, "当前分来自测评，绝不能被岗位模型改写"


def test_岗位模型_不覆盖调用方显式给的标准():
    """测评 Agent 明确给了目标分，就以它的为准——岗位模型只补空缺。"""
    gaps = [{"dimension": "需求分析", "current": 60, "required": 90, "weight": 0.4}]
    model = {"需求分析": {"required": 85, "weight": 0.30}}

    merged, filled = L.merge_requirement_model(gaps, model)

    assert filled == 0
    assert merged[0]["required"] == 90.0
    assert merged[0]["weight"] == 0.4


def test_岗位模型_强制覆盖模式():
    """换岗位重新诊断时，要允许岗位标准覆盖旧标准。"""
    gaps = [{"dimension": "需求分析", "current": 60, "required": 90, "weight": 0.4}]
    model = {"需求分析": {"required": 85, "weight": 0.3}}

    merged, filled = L.merge_requirement_model(gaps, model, only_missing=False)

    assert filled == 1
    assert merged[0]["required"] == 85.0
    assert merged[0]["weight"] == 0.3


def test_岗位模型_兼容简写写法与独立权重表():
    """岗位模型是人填的 JSON，{"维度": 80} 这种简写也必须能读。"""
    gaps = [{"dimension": "数据分析", "current": 55}]

    merged, _ = L.merge_requirement_model(gaps, {"数据分析": 88}, {"数据分析": 0.3})

    assert merged[0]["required"] == 88.0
    assert merged[0]["weight"] == 0.3


def test_岗位模型_权重越界被夹到合法区间():
    gaps = [{"dimension": "需求分析", "current": 60}]

    merged, _ = L.merge_requirement_model(gaps, {"需求分析": {"weight": 1.7}})

    assert merged[0]["weight"] == 1.0


def test_岗位模型_没覆盖的维度保持原样():
    gaps = [
        {"dimension": "需求分析", "current": 60},
        {"dimension": "团队协作", "current": 70},
    ]

    merged, filled = L.merge_requirement_model(gaps, {"需求分析": 85})

    assert filled == 1, "只有一个维度命中岗位模型"
    assert merged[0]["required"] == 85.0
    assert "required" not in merged[1], "岗位模型没定义的维度不能被塞值"


def test_岗位模型_合并不修改入参():
    """纯函数不该有副作用：入参 dict 被原地改写会在别处引发玄学 bug。"""
    gaps = [{"dimension": "需求分析", "current": 60}]

    L.merge_requirement_model(gaps, {"需求分析": 85})

    assert gaps == [{"dimension": "需求分析", "current": 60}]


def test_培训事件全部登记在统一注册表():
    """5 个培训事件必须都在 EventBus.ALL_EVENTS 里。

    为什么值得单独钉一条：事件名是散落在业务代码里的字符串，写错一个字母不会报错，
    只会让订阅方永远收不到。P1-2 修的就是「只登记了 plan_created，其余 4 个发出去直接丢」
    —— 而且丢得悄无声息，功能看起来是做了的。
    """
    from app.infrastructure.messaging.event_bus import ALL_EVENTS

    missing = [e for e in L.TRAINING_EVENTS if e not in ALL_EVENTS]
    assert not missing, f"这些培训事件没进统一注册表，发出去会被静默丢弃：{missing}"
    assert len(L.TRAINING_EVENTS) == 5, "培训对外事件数量变了，记得同步文档与订阅者"
