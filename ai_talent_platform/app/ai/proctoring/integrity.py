"""在线监考 · 行为作弊判定引擎（切屏 / 远程控制 / 外接投影 / 多显示器 / 虚拟摄像头）。

标签：[Agent组] [P1]（面试与笔试全流程防作弊：环境与行为风险引擎）

================================ 设计思路 ================================
视觉检测（detector.py）只能看「摄像头画面里有什么」，而真实作弊大量发生在画面之外：
远程代考（TeamViewer / ToDesk / 向日葵）、外接投影或采集卡把题目投到第二块屏、
另一个人通过耳机念答案、虚拟摄像头回放录制视频、切到别的标签页搜答案……这些都是
**浏览器侧信号**，必须由前端采集 + 服务端判定。

1. 纯函数设计：本模块**不做任何 IO**，输入是前端上报的事件 + 上一轮状态，
   输出是「规整后的事件 / 新增风险分 / 违规类型」，可 100% 离线单测；
   落库与接口留给 services/proctoring_service.py。
2. 事件归一（normalize_signal）：兼容前端各种大小写、别名
   （如 "screen_switch" / "visibility" / "tab_hidden" 统一为 tab_hidden），
   未知事件按 unknown 记录，绝不因为「事件名拼错」而丢掉证据。
3. 风险打分（RISK_WEIGHTS）：按「作弊嫌疑 × 可绕过难度」分级，
   同一类事件多轮次衰减计分（第 1 次满分，之后逐次打折），避免误触一次
   就判死，也避免刷屏把分数刷爆；单个会话封顶 100 分。
4. 状态机式违规去重（flagged）：同一风险在「恢复前」只记一条违规，
   恢复后再犯重新记账（与视觉检测的 flagged 语义一致，HR 复盘不会看到几百条重复）。
5. 判定结论（assess_integrity）：分数 → 风险等级 → 处理建议
   （clean / watch 观察 / review 人工复核 / serious 严重嫌疑 / void 建议作废），
   HR 页面与报告直接消费该结论，口径唯一。

关键取舍：
- **服务端只信前端上报的「事件」，不信前端自己算的分**：分数一律由本模块重算；
- 前端心跳里带 display / remote / device 快照，服务端据此判断「是否真开了共享、
  是否真有多屏」，即使用户把前端脚本改掉也会在心跳缺失时被判为 signal_lost；
- 所有阈值都来自 settings.PROCTOR_*，便于按企业策略调松紧（生产建议偏严）。
=========================================================================
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- 风险权重
# 分值含义：单次事件的「作弊嫌疑强度」（0-40）。
# 一次切屏 12 分、远程控制 40 分、外接投影 22 分……封顶 100 分。
RISK_WEIGHTS: dict[str, float] = {
    # —— 屏幕 / 焦点类（切屏、最小化、失焦、全屏被退出）——
    "tab_hidden": 12.0,
    "window_blur": 6.0,
    "fullscreen_exit": 14.0,
    "screen_share_stopped": 25.0,
    "display_changed": 16.0,
    "multi_display": 18.0,
    # —— 远程控制 / 投屏 / 虚拟机类 ——
    "remote_control": 40.0,
    "casting": 30.0,
    "virtual_display": 26.0,
    "vm_environment": 24.0,
    # —— 设备类 ——
    "virtual_camera": 30.0,
    "unknown_camera": 10.0,
    "audio_output": 20.0,
    "external_audio": 12.0,
    "device_removed": 15.0,
    "device_added": 8.0,
    "network_proxy": 20.0,
    "copy_paste": 10.0,
    "face_mismatch": 35.0,
    "impersonation": 40.0,
    "clipboard_burst": 6.0,
    "idle": 6.0,
    # —— 采集链路异常（可能是恶意断流，也可能是网络抖动，权重适中）——
    "heartbeat_lost": 15.0,
    "no_screen_share": 25.0,
    "proctor_disabled": 5.0,
    # —— 未知事件兜底 ——
    "unknown": 4.0,
}

# 事件别名 -> 规范类型（前端 / 旧版页面 / 第三方 SDK 常见写法）
_EVENT_ALIASES: dict[str, str] = {
    "visibility": "tab_hidden",
    "visibilitychange": "tab_hidden",
    "tab_hidden": "tab_hidden",
    "screen_switch": "tab_hidden",
    "switch_screen": "tab_hidden",
    "blur": "window_blur",
    "window_blur": "window_blur",
    "focus_lost": "window_blur",
    "fullscreen_exit": "fullscreen_exit",
    "fullscreenchange": "fullscreen_exit",
    "share_stop": "screen_share_stopped",
    "screen_share_stopped": "screen_share_stopped",
    "display_change": "display_changed",
    "display_changed": "display_changed",
    "multi_screen": "multi_display",
    "multi_display": "multi_display",
    "remote": "remote_control",
    "remote_control": "remote_control",
    "remote_desktop": "remote_control",
    "casting": "casting",
    "cast": "casting",
    "miracast": "casting",
    "airplay": "casting",
    "virtual_display": "virtual_display",
    "virtual_screen": "virtual_display",
    "virtual_machine": "vm_environment",
    "vm": "vm_environment",
    "vm_environment": "vm_environment",
    "virtual_camera": "virtual_camera",
    "unknown_camera": "unknown_camera",
    "audio_setup": "audio_output",
    "audio_output": "audio_output",
    "external_audio": "external_audio",
    "device_removed": "device_removed",
    "device_added": "device_added",
    "proxy": "network_proxy",
    "network_proxy": "network_proxy",
    "vpn": "network_proxy",
    "copy": "copy_paste",
    "paste": "copy_paste",
    "copy_paste": "copy_paste",
    "clipboard_burst": "clipboard_burst",
    "face_mismatch": "face_mismatch",
    "impersonation": "impersonation",
    "idle": "idle",
    "heartbeat_lost": "heartbeat_lost",
    "no_screen_share": "no_screen_share",
    "proctor_disabled": "proctor_disabled",
    "unknown": "unknown",
}

# 前端展示用的中文标签（HR 报告 / 告警文案共用）
EVENT_LABELS: dict[str, str] = {
    "tab_hidden": "切换标签页 / 最小化窗口",
    "window_blur": "窗口失去焦点",
    "fullscreen_exit": "退出考试全屏",
    "screen_share_stopped": "停止屏幕共享",
    "display_changed": "显示器配置变更",
    "multi_display": "检测到多显示器",
    "remote_control": "检测到远程控制软件",
    "casting": "检测到投屏 / 外接投影",
    "virtual_display": "检测到虚拟显示器",
    "vm_environment": "检测到虚拟机环境",
    "virtual_camera": "检测到虚拟摄像头",
    "unknown_camera": "摄像头型号与首次不一致",
    "audio_output": "检测到第二音频输出设备",
    "external_audio": "检测到外接音频设备",
    "device_removed": "监考设备被移除",
    "device_added": "监考期间接入新设备",
    "network_proxy": "检测到代理 / VPN",
    "copy_paste": "复制粘贴行为",
    "clipboard_burst": "短时间内大量剪贴板操作",
    "face_mismatch": "人脸与入场照片不一致",
    "impersonation": "疑似他人代考",
    "idle": "长时间无操作",
    "heartbeat_lost": "监考心跳丢失",
    "no_screen_share": "未开启屏幕共享",
    "proctor_disabled": "监考能力被关闭",
    "unknown": "未知异常事件",
}

# 事件来源：客户端上报 / 服务端根据心跳与配置推导（服务端来源权重更高）
SERVER_SOURCES = {"heartbeat_lost", "no_screen_share", "proctor_disabled"}

# 判定阈值（分数）-> 等级 / 建议 / 文案
RISK_LEVELS: tuple[tuple[float, str, str, str], ...] = (
    (0.0, "clean", "clean", "未发现异常行为"),
    (15.0, "watch", "keep", "存在轻微异常，建议持续观察"),
    (35.0, "review", "review", "存在多项异常，建议人工复核监考记录"),
    (60.0, "serious", "escalate", "作弊嫌疑较大，建议追加面试问询或复测"),
    (85.0, "void", "void", "作弊嫌疑极大，建议作废本次成绩/面试"),
)

# 硬性违规：命中即直达 serious（与分数无关，例如检测到远程控制 + 多人）
HARD_VIOLATIONS = {"remote_control", "impersonation", "face_mismatch", "casting"}

# 会话级封顶
MAX_SCORE = 100.0
# 重复事件衰减：第 n 次的分值 = 权重 * max(floor, 1 / n)
_REPEAT_FLOOR = 0.35
# 单次上报允许的最大事件数（防滥用）
MAX_EVENTS_PER_REPORT = 30


@dataclass(frozen=True)
class RiskEvent:
    """归一化后的一个行为事件。"""

    type: str
    detail: str = ""
    at: float = 0.0
    weight: float = 0.0
    source: str = "client"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.at, 3),
            "type": self.type,
            "label": EVENT_LABELS.get(self.type, self.type),
            "detail": self.detail,
            "weight": round(self.weight, 1),
            "source": self.source,
        }


@dataclass
class IntegrityState:
    """会话级监考状态机（可 JSON 序列化，落库在 proctor_state 的 integrity 键下）。"""

    score: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)      # 各类事件累计次数
    timeline: list[dict[str, Any]] = field(default_factory=list)  # 最近事件（截断）
    flagged: dict[str, bool] = field(default_factory=dict)    # 违规去重标记
    last_seen: float = 0.0                                    # 最近一次心跳/上报时间
    started_at: float = 0.0
    baseline: dict[str, Any] = field(default_factory=dict)    # 入场快照（首帧设备/环境）

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "counts": dict(self.counts),
            "timeline": self.timeline[-60:],
            "flagged": dict(self.flagged),
            "last_seen": round(self.last_seen, 3),
            "started_at": round(self.started_at, 3),
            "baseline": dict(self.baseline),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "IntegrityState":
        if not isinstance(raw, dict):
            return cls()
        state = cls()
        try:
            state.score = float(raw.get("score") or 0)
        except (TypeError, ValueError):
            state.score = 0.0
        counts = raw.get("counts")
        if isinstance(counts, dict):
            state.counts = {}
            for key, value in counts.items():
                try:
                    state.counts[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        timeline = raw.get("timeline")
        if isinstance(timeline, list):
            state.timeline = [item for item in timeline if isinstance(item, dict)][-60:]
        flagged = raw.get("flagged")
        if isinstance(flagged, dict):
            state.flagged = {str(k): bool(v) for k, v in flagged.items()}
        try:
            state.last_seen = float(raw.get("last_seen") or 0)
        except (TypeError, ValueError):
            state.last_seen = 0.0
        try:
            state.started_at = float(raw.get("started_at") or 0)
        except (TypeError, ValueError):
            state.started_at = 0.0
        baseline = raw.get("baseline")
        if isinstance(baseline, dict):
            state.baseline = dict(baseline)
        return state


# --------------------------------------------------------------------------- 归一化
def _clip(text: Any, limit: int = 180) -> str:
    return str(text or "").strip().replace("\n", " ")[:limit]


def _coerce_ts(value: Any, fallback: float) -> float:
    """时间戳归一：接受秒 / 毫秒 / 字符串；明显异常（未来或过旧）按 fallback 处理。"""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return fallback
    if ts > 1e11:          # 毫秒时间戳
        ts /= 1000.0
    if ts <= 0 or ts > fallback + 300 or ts < fallback - 86_400:
        return fallback
    return ts


def normalize_event(raw: Any, *, now: float | None = None) -> RiskEvent | None:
    """把前端上报的任意事件统一成 RiskEvent；无法识别返回 None（调用方按条数计）。

    支持两种形态：
        {"type": "screen_switch", "detail": "...", "ts": 1710000000}
        "screen_switch"（纯字符串，兼容简易页面）
    """
    now = time.time() if now is None else now
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        return None
    raw_type = _clip(raw.get("type") or raw.get("name") or raw.get("event"), 48).lower()
    if not raw_type:
        return None
    vtype = _EVENT_ALIASES.get(raw_type, raw_type if raw_type in RISK_WEIGHTS else "unknown")
    detail = _clip(raw.get("detail") or raw.get("message") or raw.get("value"))
    if vtype == "unknown" and raw_type not in _EVENT_ALIASES:
        # 保留原始事件名，HR 复盘时能看到前端到底报了什么
        detail = f"{raw_type}: {detail}" if detail else raw_type
    at = _coerce_ts(raw.get("ts") or raw.get("time") or raw.get("at"), now)
    weight = RISK_WEIGHTS.get(vtype, RISK_WEIGHTS["unknown"])
    source = "server" if vtype in SERVER_SOURCES else _clip(raw.get("source") or "client", 16)
    return RiskEvent(type=vtype, detail=detail, at=at, weight=weight, source=source)


def normalize_events(events: Any, *, now: float | None = None, cap: int = MAX_EVENTS_PER_REPORT) -> list[RiskEvent]:
    """批量归一化（保持上报顺序，超出上限只保留前 cap 条，防止恶意刷分）。"""
    if not isinstance(events, (list, tuple)):
        return []
    out: list[RiskEvent] = []
    for item in list(events)[: max(1, cap)]:
        ev = normalize_event(item, now=now)
        if ev is not None:
            out.append(ev)
    return out


def event_weight(vtype: str, occurrence: int) -> float:
    """第 occurrence 次（从 1 开始）该类事件的实际计分：逐次衰减但不归零。"""
    base = RISK_WEIGHTS.get(vtype, RISK_WEIGHTS["unknown"])
    if occurrence <= 1:
        return base
    return round(base * max(_REPEAT_FLOOR, 1.0 / occurrence), 2)


# --------------------------------------------------------------------------- 主状态机
def apply_events(
    state: IntegrityState | dict[str, Any] | None,
    events: list[RiskEvent],
    *,
    now: float | None = None,
    cap: float = MAX_SCORE,
    timeline_limit: int = 60,
) -> tuple[IntegrityState, list[dict[str, Any]], float]:
    """应用一批事件：返回 (新状态, 新增违规, 本次加分)。

    「新增违规」= 该类风险在恢复前首次出现的记录（用于 ExamSession.violations
    与 HR 页面的异常列表），结构 {ts, type, label, detail, score}；
    分值每次出现都会累加，违规条目只记第一次，避免刷屏。
    """
    now = time.time() if now is None else now
    st = state if isinstance(state, IntegrityState) else IntegrityState.from_dict(state)
    if not st.started_at:
        st.started_at = now
    violations: list[dict[str, Any]] = []
    gained = 0.0

    for ev in events:
        # 服务端推导事件的权重加倍（前端关掉监听也逃不掉）
        occur = int(st.counts.get(ev.type, 0)) + 1
        st.counts[ev.type] = occur
        unit = event_weight(ev.type, occur) * (1.6 if ev.source == "server" else 1.0)
        gained += unit
        st.score = min(cap, st.score + unit)
        entry = {**ev.to_dict(), "weight": round(unit, 1)}
        st.timeline.append(entry)
        if not st.flagged.get(ev.type):
            st.flagged[ev.type] = True
            violations.append(
                {
                    "ts": ev.at or now,
                    "type": ev.type,
                    "label": EVENT_LABELS.get(ev.type, ev.type),
                    "detail": ev.detail or EVENT_LABELS.get(ev.type, ev.type),
                    "score": round(st.score, 1),
                    "source": ev.source,
                }
            )

    if len(st.timeline) > timeline_limit:
        st.timeline = st.timeline[-timeline_limit:]
    st.last_seen = max(st.last_seen, now)
    return st, violations, round(gained, 2)


def clear_flag(state: IntegrityState | dict[str, Any] | None, vtype: str) -> IntegrityState:
    """风险恢复（例如重新进入全屏、重新开始共享）：解除去重标记，下次再犯重新记账。"""
    st = state if isinstance(state, IntegrityState) else IntegrityState.from_dict(state)
    st.flagged.pop(vtype, None)
    return st


def assess_integrity(
    state: IntegrityState | dict[str, Any] | None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把监考状态换算成 HR 可直接消费的结论（等级 / 建议 / 文案 / 命中项）。

    硬性违规（远程控制、投屏、疑似代考）会把等级直接抬到 serious，
    即使分数还没累积到阈值 —— 这类风险不应被「时间不够」稀释。
    """
    st = state if isinstance(state, IntegrityState) else IntegrityState.from_dict(state)
    score = max(0.0, min(MAX_SCORE, float(st.score or 0)))
    level, advice, message = "clean", "clean", "未发现异常行为"
    for threshold, lv, adv, msg in RISK_LEVELS:
        if score >= threshold:
            level, advice, message = lv, adv, msg
    hard_hits = sorted({t for t in st.counts if t in HARD_VIOLATIONS})
    if hard_hits and level in {"clean", "watch", "review"}:
        level, advice = "serious", "escalate"
        labels = "、".join(EVENT_LABELS.get(t, t) for t in hard_hits[:3])
        message = f"命中硬性风险（{labels}），建议追加人工问询或复测"

    cfg = config or {}
    require_share = bool(cfg.get("require_screen_share", False))
    missing_share = require_share and not st.baseline.get("screen_shared")
    return {
        "score": round(score, 1),
        "level": level,
        "advice": advice,
        "message": message,
        "require_review": level in {"review", "serious", "void"},
        "void_suggested": level == "void",
        "hard_violations": hard_hits,
        "missing_screen_share": missing_share,
        "event_counts": dict(sorted(st.counts.items(), key=lambda kv: -kv[1])),
        "event_labels": {k: EVENT_LABELS.get(k, k) for k in st.counts},
        "recent_events": st.timeline[-10:],
        "total_events": int(sum(st.counts.values())),
        "last_seen": st.last_seen or None,
    }


# --------------------------------------------------------------------------- 设备快照
def capture_baseline(
    state: IntegrityState | dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    *,
    now: float | None = None,
) -> IntegrityState:
    """入场基线：首次上报的摄像头 / 屏幕 / 网络指纹，后续漂移即判违规。

    只记录「用于比对」的稳定字段，避免把瞬时值（亮度、帧率）写进基线造成误判。
    """
    now = time.time() if now is None else now
    st = state if isinstance(state, IntegrityState) else IntegrityState.from_dict(state)
    if st.baseline or not isinstance(snapshot, dict):
        return st
    keep = ("camera_id", "camera_label", "display_count", "screen_width", "screen_height",
            "timezone", "language", "platform", "hardware_concurrency", "screen_shared",
            "remote_control", "virtual_camera")
    st.baseline = {k: snapshot.get(k) for k in keep if snapshot.get(k) is not None}
    st.started_at = st.started_at or now
    return st


def detect_environment_changes(
    state: IntegrityState | dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    *,
    now: float | None = None,
) -> list[RiskEvent]:
    """对比入场基线与本轮环境快照，自动派生环境类风险事件（前端无法伪造）。"""
    now = time.time() if now is None else now
    st = state if isinstance(state, IntegrityState) else IntegrityState.from_dict(state)
    events: list[RiskEvent] = []
    if not isinstance(snapshot, dict) or not st.baseline:
        return events

    def emit(vtype: str, detail: str) -> None:
        events.append(
            RiskEvent(type=vtype, detail=detail, at=now, weight=RISK_WEIGHTS.get(vtype, 4.0), source="server")
        )

    base = st.baseline
    cam_id = snapshot.get("camera_id")
    if base.get("camera_id") and cam_id and str(cam_id) != str(base["camera_id"]):
        emit("unknown_camera", f"摄像头由 {base.get('camera_label') or base['camera_id']} 变更为 {snapshot.get('camera_label') or cam_id}")
    if snapshot.get("virtual_camera") and not base.get("virtual_camera"):
        emit("virtual_camera", "本轮上报虚拟摄像头")
    if snapshot.get("remote_control") and not base.get("remote_control"):
        emit("remote_control", "本轮上报远程控制会话")
    try:
        displays = int(snapshot.get("display_count") or 1)
        baseline_displays = int(base.get("display_count") or 1)
    except (TypeError, ValueError):
        displays, baseline_displays = 1, 1
    if displays > baseline_displays:
        emit("display_changed", f"显示器数量 {baseline_displays} → {displays}")
    if displays >= 2:
        emit("multi_display", f"当前连接 {displays} 块显示器")
    if base.get("screen_shared") and snapshot.get("screen_shared") is False:
        emit("screen_share_stopped", "屏幕共享已中断")
    tz = snapshot.get("timezone")
    if base.get("timezone") and tz and tz != base["timezone"]:
        emit("network_proxy", f"时区由 {base['timezone']} 变更为 {tz}（疑似代考/代理）")
    return events


__all__ = [
    "EVENT_LABELS",
    "HARD_VIOLATIONS",
    "MAX_EVENTS_PER_REPORT",
    "MAX_SCORE",
    "RISK_LEVELS",
    "RISK_WEIGHTS",
    "IntegrityState",
    "RiskEvent",
    "apply_events",
    "assess_integrity",
    "capture_baseline",
    "clear_flag",
    "detect_environment_changes",
    "event_weight",
    "normalize_event",
    "normalize_events",
]
