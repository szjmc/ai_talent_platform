"""
模块：tests/test_proctoring_integrity.py
职责：在线监考 · 行为作弊判定引擎的离线守门测试（切屏 / 远程控制 / 投屏 / 多显示器 / 虚拟摄像头）。

标签：[Agent组] [P1]（防作弊：判定引擎离线验收，不依赖数据库与浏览器）

为什么需要：
    视觉检测（detector.py）看不到「画面外」的作弊。本模块是行为监考的唯一判定口径，
    一旦权重表 / 去重逻辑 / 硬性违规抬档被改坏，HR 侧会直接漏判（远程代考不被发现）
    或者狂报（一次误触切屏就判作废）。这里用纯函数把口径钉死。

覆盖：
    1. 事件归一化（别名、未知事件、毫秒时间戳、字符串形态）；
    2. 重复事件衰减但不清零、封顶 100 分；
    3. 违规去重：恢复前只记一条，恢复后再犯重新记；
    4. 硬性违规（远程控制 / 投屏 / 代考）即使分数低也抬到 serious；
    5. 判定等级与建议的阈值边界；
    6. 入场基线 + 环境漂移检测（多屏 / 换摄像头 / 停止共享 / 时区变化）；
    7. 状态可 JSON 往返（落库 proctor_state）。
"""

from __future__ import annotations

import json

import pytest

from app.ai.proctoring.integrity import (
    EVENT_LABELS,
    MAX_SCORE,
    RISK_WEIGHTS,
    IntegrityState,
    RiskEvent,
    apply_events,
    assess_integrity,
    capture_baseline,
    clear_flag,
    detect_environment_changes,
    event_weight,
    normalize_event,
    normalize_events,
)

NOW = 1_700_000_000.0


# --------------------------------------------------------------------------- 归一化
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("screen_switch", "tab_hidden"),
        ({"type": "visibilitychange"}, "tab_hidden"),
        ({"type": "Remote_Control"}, "remote_control"),
        ({"type": "miracast"}, "casting"),
        ({"type": "multi_screen"}, "multi_display"),
        ({"type": "virtual_machine"}, "vm_environment"),
        ({"name": "virtual_camera", "message": "OBS Virtual Camera"}, "virtual_camera"),
    ],
)
def test_normalize_aliases(raw, expected):
    """不同前端 / 第三方 SDK 的事件名统一收敛到规范类型（避免丢证据）。"""
    ev = normalize_event(raw, now=NOW)
    assert ev is not None and ev.type == expected
    assert ev.weight == RISK_WEIGHTS[expected]


def test_normalize_unknown_event_keeps_original_name():
    """未知事件不丢弃：记为 unknown，但把原始事件名留在 detail 里供人工判断。"""
    ev = normalize_event({"type": "something_weird", "detail": "x"}, now=NOW)
    assert ev is not None
    assert ev.type == "unknown"
    assert "something_weird" in ev.detail


def test_normalize_handles_millisecond_timestamp():
    ev = normalize_event({"type": "tab_hidden", "ts": NOW * 1000}, now=NOW)
    assert ev is not None and abs(ev.at - NOW) < 1


def test_normalize_rejects_bad_payload():
    assert normalize_event(None) is None
    assert normalize_event({}) is None
    assert normalize_events("not-a-list") == []


def test_normalize_caps_event_count():
    """单次上报最多 30 条，超过部分直接忽略（防刷分）。"""
    events = normalize_events([{"type": "tab_hidden"} for _ in range(100)], now=NOW)
    assert len(events) == 30


# --------------------------------------------------------------------------- 计分
def test_repeat_weight_decays_but_never_zero():
    first = event_weight("tab_hidden", 1)
    second = event_weight("tab_hidden", 2)
    tenth = event_weight("tab_hidden", 10)
    assert first > second > tenth > 0
    # 衰减有下限（35%），避免「刷够次数就不计分」的绕过手法
    assert tenth >= round(RISK_WEIGHTS["tab_hidden"] * 0.35, 2)


def test_score_is_capped():
    """离谱数量的远程控制事件也不能把分数刷到 100 以上（报告口径稳定）。"""
    events = [
        RiskEvent(type="remote_control", detail="x", at=NOW, weight=RISK_WEIGHTS["remote_control"], source="client")
        for _ in range(50)
    ]
    state, _violations, _gained = apply_events(None, events, now=NOW)
    assert state.score == MAX_SCORE


def test_server_derived_events_weigh_more():
    """服务端推导的事件权重更高：关掉前端脚本也逃不掉（断流 / 未共享）。"""
    client_state, _, _ = apply_events(
        None, [RiskEvent(type="multi_display", at=NOW, weight=0, source="client")], now=NOW
    )
    server_state, _, _ = apply_events(
        None, [RiskEvent(type="multi_display", at=NOW, weight=0, source="server")], now=NOW
    )
    assert server_state.score > client_state.score


# --------------------------------------------------------------------------- 去重
def test_violation_is_recorded_once_until_recovered():
    """同类风险在恢复前只记一条违规，但分数照常累加。"""
    ev = RiskEvent(type="tab_hidden", detail="切走一次", at=NOW, weight=0, source="client")
    state, violations, _ = apply_events(None, [ev], now=NOW)
    assert len(violations) == 1

    state, violations2, _ = apply_events(state, [ev, ev], now=NOW + 5)
    assert violations2 == []                     # 不重复记账
    assert state.counts["tab_hidden"] == 3       # 次数仍如实累计

    state = clear_flag(state, "tab_hidden")      # 候选人了回到全屏
    state, violations3, _ = apply_events(state, [ev], now=NOW + 10)
    assert len(violations3) == 1                 # 再犯重新记账


def test_violation_carries_label_and_score():
    _, violations, _ = apply_events(
        None, [RiskEvent(type="remote_control", detail="ToDesk", at=NOW, weight=0, source="client")], now=NOW
    )
    assert violations[0]["label"] == EVENT_LABELS["remote_control"]
    assert violations[0]["score"] > 0
    assert violations[0]["detail"] == "ToDesk"


# --------------------------------------------------------------------------- 判定
def test_clean_session():
    report = assess_integrity(None)
    assert report["level"] == "clean" and report["require_review"] is False
    assert report["score"] == 0


@pytest.mark.parametrize(
    ("event_type", "times", "min_level"),
    [
        ("tab_hidden", 1, "clean"),        # 单次切屏：12 分，仍属观察区
        ("tab_hidden", 3, "watch"),        # 多次切屏进入观察
        ("casting", 1, "serious"),         # 投屏 = 硬性风险，直接严重
        ("remote_control", 1, "serious"),  # 远程控制 = 硬性风险
        ("impersonation", 1, "serious"),   # 疑似代考
    ],
)
def test_risk_levels(event_type, times, min_level):
    events = [
        RiskEvent(type=event_type, at=NOW, weight=0, source="client") for _ in range(times)
    ]
    state, _, _ = apply_events(None, events, now=NOW)
    report = assess_integrity(state)
    order = ["clean", "watch", "review", "serious", "void"]
    assert order.index(report["level"]) >= order.index(min_level), report


def test_hard_violation_escalates_even_with_low_score():
    """硬性违规不应被「分数还没攒够」稀释：一条投屏就必须 serious。"""
    state, _, _ = apply_events(
        None, [RiskEvent(type="casting", at=NOW, weight=0, source="client")], now=NOW
    )
    report = assess_integrity(state)
    assert report["level"] in {"serious", "void"}
    assert report["require_review"] is True
    assert "casting" in report["hard_violations"]
    assert "投屏" in report["message"]


def test_missing_screen_share_reported():
    report = assess_integrity({"counts": {"tab_hidden": 1}}, config={"require_screen_share": True})
    assert report["missing_screen_share"] is True


def test_report_exposes_labels_and_counts():
    state, _, _ = apply_events(
        None,
        [
            RiskEvent(type="tab_hidden", at=NOW, weight=0, source="client"),
            RiskEvent(type="tab_hidden", at=NOW, weight=0, source="client"),
            RiskEvent(type="remote_control", at=NOW, weight=0, source="client"),
        ],
        now=NOW,
    )
    report = assess_integrity(state)
    assert report["event_counts"]["tab_hidden"] == 2
    assert report["event_labels"]["remote_control"] == EVENT_LABELS["remote_control"]
    assert report["total_events"] == 3


# --------------------------------------------------------------------------- 环境漂移
def test_baseline_only_captures_stable_fields():
    state = capture_baseline(None, {"camera_id": "cam-1", "camera_label": "FaceTime HD",
                                    "display_count": 1, "luma": 120}, now=NOW)
    assert state.baseline["camera_id"] == "cam-1"
    assert "luma" not in state.baseline       # 瞬时值不进基线（避免误判）


def test_detect_multi_display_and_camera_switch():
    state = capture_baseline(None, {"camera_id": "cam-1", "display_count": 1, "screen_shared": True}, now=NOW)
    events = detect_environment_changes(
        state,
        {"camera_id": "virtual-cam", "display_count": 2, "screen_shared": False},
        now=NOW + 10,
    )
    types = {ev.type for ev in events}
    assert {"unknown_camera", "display_changed", "multi_display", "screen_share_stopped"} <= types
    # 服务端推导来源，权重更高
    assert all(ev.source == "server" for ev in events)


def test_detect_timezone_change_means_proxy():
    """时区突变通常意味着换人/代理（同一台机器不应切时区）。"""
    state = capture_baseline(None, {"timezone": "Asia/Shanghai"}, now=NOW)
    events = detect_environment_changes(state, {"timezone": "America/New_York"}, now=NOW + 3)
    assert [ev.type for ev in events] == ["network_proxy"]


def test_detect_ignores_when_no_baseline():
    assert detect_environment_changes(None, {"display_count": 3}, now=NOW) == []


# --------------------------------------------------------------------------- 序列化
def test_state_json_roundtrip():
    """状态机必须能无损落 JSON 列（proctor_state），否则刷新页面就丢证据。"""
    state, _, _ = apply_events(
        None,
        [RiskEvent(type="tab_hidden", detail="切屏", at=NOW, weight=0, source="client")],
        now=NOW,
    )
    state = capture_baseline(state, {"camera_id": "cam-9", "display_count": 1}, now=NOW)
    payload = json.loads(json.dumps(state.to_dict()))
    restored = IntegrityState.from_dict(payload)
    assert restored.score == state.score
    assert restored.counts == state.counts
    assert restored.baseline == state.baseline
    assert restored.flagged == state.flagged


def test_state_tolerates_garbage_payload():
    """库里历史脏数据（或旧版本结构）不能把监考链路搞崩。"""
    state = IntegrityState.from_dict({"score": "abc", "counts": {"tab_hidden": "x"}, "timeline": "bad"})
    assert state.score == 0
    assert isinstance(state.timeline, list)
