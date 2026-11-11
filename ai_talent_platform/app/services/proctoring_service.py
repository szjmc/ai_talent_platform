"""
模块：app/services/proctoring_service.py
职责：在线监考业务编排（AI 面试 / 笔试 / 面试间共用）—— 事件归一、风险累加、快照留存、HR 复盘。

标签：[Agent组] [P1]（防作弊：切屏 / 远程控制 / 投屏 / 多显示器全链路服务）

================================ 链路 ================================
    POST /proctoring/{type}/{token}/heartbeat   心跳 + 事件批量上报
    POST /proctoring/{type}/{token}/snapshot    可疑快照（摄像头 / 屏幕）
    GET  /proctoring/{type}/{token}/state       候选人自查（是否被阻断 / 还缺什么）
    GET  /proctoring/sessions                   HR：风险会话分页
    GET  /proctoring/sessions/{id}              HR：证据链（时间线 + 快照）
    POST /proctoring/sessions/{id}/review       HR：复核结论（确认作弊 / 误报）

关键实现要点：
1. **判分只在服务端**：客户端只上报事件，分值与等级一律由
   ai/proctoring/integrity.py 纯函数算；客户端即使伪造 events 也无法写入分数。
2. **双重判定**：客户端事件 + 服务端推导（心跳中断、环境漂移、共享中断）。
   前端把监听脚本删掉也逃不掉 heartbeat_lost / no_screen_share。
3. **隐私最小化**：不存全程录像，只在触发硬性风险或分数跃升时抽样留存，
   且单会话有上限（PROCTOR_SNAPSHOT_MAX_PER_SESSION），HR 复核后可删除。
4. **与业务表联动**：风险结论同时写回业务对象（AI 面试 report.integrity、
   笔试 ExamSession.violations），保证既有页面 / 报告无需改造即可看到异常。
5. **失败即降级**：监考链路异常绝不影响候选人作答（上报接口返回 ok，业务照常推进），
   仅审计日志留痕 —— 与视觉检测「不可用不阻塞考试」的策略一致。
=========================================================================
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.proctoring.integrity import (
    EVENT_LABELS,
    IntegrityState,
    RiskEvent,
    apply_events,
    assess_integrity,
    capture_baseline,
    detect_environment_changes,
    normalize_events,
)
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.ai_interview import AiInterviewSession
from app.models.invitation import ExamSession
from app.models.proctoring import SUBJECT_TYPES, ProctoringEvent, ProctoringSession, ProctoringSnapshot
from app.repositories.ai_interview_repository import AiInterviewSessionRepository
from app.repositories.invitation_repository import ExamSessionRepository
from app.repositories.proctoring_repository import (
    ProctoringEventRepository,
    ProctoringSessionRepository,
    ProctoringSnapshotRepository,
)
from app.schemas.proctoring import (
    ProctorAckResponse,
    ProctorEventIn,
    ProctorHeartbeatRequest,
    ProctorReportItem,
    ProctorReportResponse,
    ProctorRequirement,
    ProctorSessionSummary,
    ProctorSnapshotItem,
    ProctorSnapshotRequest,
    ProctorStateResponse,
)

logger = logging.getLogger(__name__)

# 允许作为监考对象类型的枚举（与 models/proctoring.SUBJECT_TYPES 保持一致）
ALLOWED_SUBJECTS = set(SUBJECT_TYPES)

# 会话事件入库上限：超出后只更新汇总（防止恶意刷爆数据库）
EVENT_ROW_LIMIT = 500
# 触发快照留存的硬性风险类型（与 integrity.HARD_VIOLATIONS 呼应，但快照策略更保守）
SNAPSHOT_TRIGGERS = {"remote_control", "casting", "impersonation", "face_mismatch", "multi_display", "virtual_camera"}


def _risk_ok(advice: str) -> str:
    return advise_label_map().get(advice, advice)


def advise_label_map() -> dict[str, str]:
    return {
        "clean": "无需处理",
        "keep": "继续观察",
        "review": "人工复核",
        "escalate": "追加问询 / 复测",
        "void": "建议作废",
    }


class ProctoringService:
    """监考业务服务（所有环节共用）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.sessions = ProctoringSessionRepository()
        self.events = ProctoringEventRepository()
        self.snapshots = ProctoringSnapshotRepository()

    # ================================================================= 配置
    @property
    def strict(self) -> bool:
        return bool(settings.PROCTOR_ENABLED and settings.PROCTOR_STRICT_MODE)

    @property
    def require_share(self) -> bool:
        return bool(settings.PROCTOR_ENABLED and settings.PROCTOR_REQUIRE_SCREEN_SHARE)

    def config_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(settings.PROCTOR_ENABLED),
            "strict": self.strict,
            "require_screen_share": self.require_share,
            "allow_multi_display": bool(settings.PROCTOR_ALLOW_MULTI_DISPLAY),
            "heartbeat_seconds": max(5, int(settings.PROCTOR_HEARTBEAT_SECONDS or 15)),
            "heartbeat_timeout_seconds": max(20, int(settings.PROCTOR_HEARTBEAT_TIMEOUT_SECONDS or 60)),
            "snapshot_interval_seconds": max(5, int(settings.PROCTOR_SNAPSHOT_INTERVAL_SECONDS or 20)),
            "snapshot_max": max(0, int(settings.PROCTOR_SNAPSHOT_MAX_PER_SESSION or 0)),
            "block_score": max(1, int(settings.PROCTOR_RISK_BLOCK_SCORE or 85)),
            "max_events": max(1, int(settings.PROCTOR_MAX_EVENTS_PER_REPORT or 30)),
        }

    # ================================================================= 对象解析
    async def _resolve_subject(
        self, subject_type: str, token: str
    ) -> tuple[str, int | None, str, str, str | None]:
        """token -> (类型, 对象 ID, token, 候选人姓名, 岗位名)；非法/过期直接报错。

        这里同时做「对象存在性」与「状态」校验，避免给已取消 / 已完成的面试
        继续记账（也防止有人拿旧 token 刷监考数据）。
        """
        if subject_type not in ALLOWED_SUBJECTS:
            raise ParamError(f"不支持的监考对象类型：{subject_type}")
        token = (token or "").strip()
        if not token:
            raise ParamError("缺少监考 token")
        if subject_type == "ai_interview":
            row = await AiInterviewSessionRepository().get_by_token(self.db, token)
            if row is None:
                raise NotFoundError("面试不存在或链接已失效")
            if row.status in {"cancelled", "expired"}:
                raise BusinessRuleError("面试已取消或链接已过期")
            return subject_type, row.id, token, row.candidate_name or "", row.position_title
        if subject_type == "exam":
            row = await ExamSessionRepository().get_by_token(self.db, token)
            if row is None:
                raise NotFoundError("笔试会话不存在或链接已失效")
            if row.status in {"expired", "cancelled"}:
                raise BusinessRuleError("笔试已结束或已取消")
            invite = await self._invite_of_exam(row)
            name = (getattr(invite, "talent_snapshot", None) or {}).get("name") if invite else ""
            title = getattr(invite, "title", None) if invite else None
            return subject_type, row.id, token, str(name or ""), title
        # interview / assessment：只有 token，无独立会话表，按 token 记账
        return subject_type, None, token, "", None

    async def _invite_of_exam(self, session: ExamSession) -> Any:
        from app.repositories.invitation_repository import TalentInvitationRepository

        if not session.invitation_id:
            return None
        return await TalentInvitationRepository().get_by_id(self.db, session.invitation_id)

    # ================================================================= 会话
    async def ensure_session(
        self,
        subject_type: str,
        token: str,
        *,
        client_ip: str | None = None,
        subject_id: int | None = None,
    ) -> tuple[ProctoringSession, IntegrityState]:
        """取或建监考会话（幂等；同一 token 只建一行）。

        `subject_id` 供内部调用方（如 HMAC 笔试页 /{assessment_id}/behaviour）显式指定
        业务主键 —— 这类页面本身已有独立的令牌校验，token 无法反查出会话行。
        """
        subject_type, resolved_id, token, name, position_title = await self._resolve_subject(subject_type, token)
        if subject_id is not None:
            resolved_id = int(subject_id)
        existing = None
        if resolved_id is not None:
            # 有业务主键时必须按主键定位：同一 token 可能对应多份测评/笔试卷，
            # 只按 token 查会命中别的会话（历史数据里 assessment 轮次就是这种情况）。
            existing = await self.sessions.get_by_subject(self.db, subject_type, resolved_id)
        if existing is None and resolved_id is None:
            existing = await self.sessions.get_by_token(self.db, token)
        cfg = self.config_snapshot()
        if existing is None:
            existing = ProctoringSession(
                subject_type=subject_type,
                subject_id=resolved_id,
                subject_token=token,
                candidate_name=name,
                position_title=position_title,
                risk_score=0,
                risk_level="clean",
                risk_advice="clean",
                strict_mode=cfg["strict"],
                require_screen_share=cfg["require_screen_share"],
                started_at=datetime.now(),
                event_counts={},
                hard_violations=[],
                state=IntegrityState().to_dict(),
            )
            existing = await self.sessions.create(self.db, existing)
            logger.info(
                "监考会话建立 subject=%s:%s ip=%s", subject_type, resolved_id, client_ip or "-"
            )
        else:
            # 候选人信息可能在首次心跳前才补齐
            if name and not existing.candidate_name:
                existing.candidate_name = name
            if position_title and not existing.position_title:
                existing.position_title = position_title
            existing.strict_mode = cfg["strict"]
            existing.require_screen_share = cfg["require_screen_share"]
        state = IntegrityState.from_dict(existing.state)
        return existing, state

    # ================================================================= 上报
    async def heartbeat(
        self,
        subject_type: str,
        token: str,
        payload: ProctorHeartbeatRequest,
        *,
        client_ip: str | None = None,
        subject_id: int | None = None,
    ) -> ProctorAckResponse:
        """一次心跳 = 客户端事件 + 服务端推导 + 风险累加 + 结论回传。"""
        if not settings.PROCTOR_ENABLED:
            return ProctorAckResponse(state=self._disabled_state())

        row, state = await self.ensure_session(
            subject_type, token, client_ip=client_ip, subject_id=subject_id
        )
        now = datetime.now()
        now_ts = now.timestamp()
        cfg = self.config_snapshot()

        # ---- 0) 断流补偿：上次心跳距今过久，先补一条 heartbeat_lost
        derived: list[RiskEvent] = []
        if row.last_heartbeat_at and not row.heartbeat_lost:
            gap = (now - row.last_heartbeat_at).total_seconds()
            if gap > cfg["heartbeat_timeout_seconds"]:
                derived.append(
                    RiskEvent(
                        type="heartbeat_lost",
                        detail=f"监考心跳中断 {int(gap)} 秒（阈值 {cfg['heartbeat_timeout_seconds']} 秒）",
                        at=now_ts,
                        weight=0,
                        source="server",
                    )
                )
                row.heartbeat_lost = True
                row.blocked_at = row.blocked_at or now

        # ---- 1) 环境快照 & 基线
        snapshot = {
            "screen_shared": bool(payload.screen_shared),
            "remote_control": bool(payload.remote_control),
            "display_count": int(payload.display_count or 1),
            "camera_id": payload.camera_id,
            "camera_label": payload.camera_label,
            "virtual_camera": bool(payload.virtual_camera),
            "timezone": payload.timezone,
            "screen_width": payload.screen_width,
            "screen_height": payload.screen_height,
            "platform": payload.platform,
        }
        state = capture_baseline(state, snapshot, now=now_ts)
        derived.extend(detect_environment_changes(state, snapshot, now=now_ts))

        # ---- 2) 强制要求（服务端策略）
        if self.require_share and not payload.screen_shared:
            derived.append(
                RiskEvent(type="no_screen_share", detail="未开启屏幕共享（本次考试要求共享整个屏幕）",
                          at=now_ts, weight=0, source="server")
            )
        if not settings.PROCTOR_ALLOW_MULTI_DISPLAY and int(payload.display_count or 1) >= 2:
            derived.append(
                RiskEvent(type="multi_display", detail=f"当前连接 {payload.display_count} 块显示器",
                          at=now_ts, weight=0, source="server")
            )
        if payload.remote_control:
            derived.append(
                RiskEvent(type="remote_control", detail="前端检测到远程控制会话", at=now_ts, weight=0, source="server")
            )
        if payload.virtual_camera:
            derived.append(
                RiskEvent(type="virtual_camera", detail="前端检测到虚拟摄像头设备", at=now_ts, weight=0, source="server")
            )

        # ---- 3) 客户端事件归一化
        events = normalize_events(
            [item.model_dump() for item in payload.events], now=now_ts, cap=cfg["max_events"]
        )
        # 客户端误报的「恢复类」信号：重新全屏 / 重新共享 -> 解除去重标记
        for ev in events:
            if ev.type in {"screen_share_started", "focus_returned", "fullscreen_restored"}:
                state.flagged.pop("tab_hidden", None)
                state.flagged.pop("fullscreen_exit", None)
                state.flagged.pop("screen_share_stopped", None)
        events = [ev for ev in events if ev.type not in {"focus_returned", "screen_share_started", "fullscreen_restored"}]

        all_events = events + derived
        state, violations, gained = apply_events(state, all_events, now=now_ts)
        report = assess_integrity(state, config=cfg)

        # ---- 4) 落库：汇总
        row.state = state.to_dict()
        row.risk_score = report["score"]
        row.risk_level = report["level"]
        row.risk_advice = report["advice"]
        row.event_counts = report["event_counts"]
        row.hard_violations = report["hard_violations"]
        row.screen_shared = bool(payload.screen_shared)
        row.remote_control = bool(payload.remote_control)
        row.display_count = int(payload.display_count or 1)
        row.event_total = int(row.event_total or 0) + len(all_events)
        row.last_heartbeat_at = now
        # 收到新的心跳即视为断流已恢复（解除标记，下次再断重新记账）
        if row.heartbeat_lost:
            state.flagged.pop("heartbeat_lost", None)
            row.state = state.to_dict()
            row.heartbeat_lost = False
        # 阻断条件：分数封顶阈值 / 作废档位 / 严格模式下命中硬性风险（远程控制、投屏、代考）
        if (
            report["level"] == "void"
            or report["score"] >= cfg["block_score"]
            or (self.strict and report["hard_violations"])
        ):
            row.blocked_at = row.blocked_at or now
        await self._persist_events(row, all_events, state)

        # ---- 5) 写回业务对象（既有页面/报告自动可见）
        await self._sync_subject(row, report, state)

        await self.db.commit()
        return ProctorAckResponse(
            state=self._candidate_state(row, report, state),
            new_violations=[ProctorReportItem(**item) for item in violations],
            score=report["score"],
            level=report["level"],
        )

    async def _persist_events(
        self, row: ProctoringSession, events: list[RiskEvent], state: IntegrityState
    ) -> None:
        """事件入库（有上限），逐条累加风险分快照便于复盘。"""
        if not events:
            return
        stored = await self.events.count_by_session(self.db, row.id)
        if stored >= EVENT_ROW_LIMIT:
            return
        room = EVENT_ROW_LIMIT - stored
        running = float(row.risk_score or 0)
        for ev in events[:room]:
            self.db.add(
                ProctoringEvent(
                    session_ref_id=row.id,
                    subject_type=row.subject_type,
                    subject_id=row.subject_id,
                    event_type=ev.type,
                    label=EVENT_LABELS.get(ev.type, ev.type),
                    detail=(ev.detail or "")[:2000],
                    weight=float(ev.weight or 0),
                    score_after=running,
                    source=ev.source,
                    occurred_at=datetime.fromtimestamp(ev.at) if ev.at else datetime.now(),
                )
            )
        await self.db.flush()

    # ================================================================= 快照
    async def save_snapshot(
        self,
        subject_type: str,
        token: str,
        payload: ProctorSnapshotRequest,
        *,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        """留存可疑快照：超过上限或未启用时静默丢弃（不阻断作答）。"""
        cfg = self.config_snapshot()
        if not settings.PROCTOR_ENABLED or cfg["snapshot_max"] <= 0:
            return {"stored": False, "reason": "monitoring_disabled"}
        row, _state = await self.ensure_session(subject_type, token, client_ip=client_ip)
        count = await self.snapshots.count_by_session(self.db, row.id)
        if count >= cfg["snapshot_max"]:
            return {"stored": False, "reason": "quota_exceeded", "total": count}

        raw = payload.image.split(",", 1)[-1] if "," in payload.image[:64] else payload.image
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise ParamError("快照解码失败") from exc
        if not data:
            raise ParamError("快照内容为空")

        # 隐私最小化：只存 640px 级别的压缩图（前端已压缩），超过 1.5MB 直接拒绝
        if len(data) > 1_500_000:
            raise ParamError("快照体积过大（上限 1.5MB）")

        storage_key, backend = await self._store_image(row, data, payload.source)
        obj = ProctoringSnapshot(
            session_ref_id=row.id,
            source=payload.source,
            storage_backend=backend,
            storage_key=storage_key,
            file_size=len(data),
            trigger_type=(payload.trigger_type or "")[:40],
            captured_at=datetime.now(),
        )
        await self.snapshots.create(self.db, obj)
        await self.db.commit()
        return {"stored": True, "id": obj.id, "url": self._snapshot_url(obj), "total": count + 1}

    async def _store_image(self, row: ProctoringSession, data: bytes, source: str) -> tuple[str, str]:
        """二进制落对象存储（local/MinIO/OSS/S3 由 STORAGE_BACKEND 决定）。"""
        from app.adapters.storage.base_storage import get_storage

        storage = get_storage()
        backend = str(getattr(storage, "name", None) or settings.STORAGE_BACKEND or "local")
        filename = f"proctor_{row.subject_type}_{row.id}_{source}_{int(datetime.now().timestamp())}.jpg"
        try:
            key = await storage.save(data, filename, subdir="proctoring")
        except Exception as exc:  # noqa: BLE001 —— 存储异常不阻断作答
            logger.warning("监考快照写入失败（跳过）：%s", exc)
            raise ParamError("快照存储失败，请稍后重试") from exc
        return key, backend

    @staticmethod
    def _snapshot_url(obj: ProctoringSnapshot) -> str:
        if not obj.storage_key:
            return ""
        try:
            from app.adapters.storage.base_storage import get_storage

            return get_storage(obj.storage_backend or None).url(obj.storage_key)
        except Exception:  # noqa: BLE001
            return ""

    # ================================================================= 写回业务
    async def _sync_subject(
        self, row: ProctoringSession, report: dict[str, Any], state: IntegrityState
    ) -> None:
        """把监考结论写回业务对象，保证既有 HR 页面 / 报告能看到异常。"""
        payload = {
            "risk_score": report["score"],
            "risk_level": report["level"],
            "risk_advice": report["advice"],
            "risk_message": report["message"],
            "hard_violations": report["hard_violations"],
            "event_counts": report["event_counts"],
            "event_labels": report["event_labels"],
            "hardware": {
                "display_count": row.display_count,
                "screen_shared": row.screen_shared,
                "remote_control": row.remote_control,
                "heartbeat_lost": row.heartbeat_lost,
            },
            "baseline": state.baseline,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            if row.subject_type == "ai_interview" and row.subject_id:
                session = await AiInterviewSessionRepository().get_by_id(self.db, row.subject_id)
                if session is not None:
                    blob = dict(session.proctor_state or {}) if isinstance(session.proctor_state, dict) else {}
                    blob["integrity"] = payload
                    blob["score"] = report["score"]
                    blob["level"] = report["level"]
                    session.proctor_state = blob
            elif row.subject_type == "exam" and row.subject_id:
                session = await ExamSessionRepository().get_by_id(self.db, row.subject_id)
                if session is not None:
                    blob = dict(session.proctor_state or {}) if isinstance(session.proctor_state, dict) else {}
                    blob["integrity"] = payload
                    session.proctor_state = blob
                    existing = list(session.violations or [])
                    recorded = {str(v.get("type")) for v in existing if isinstance(v, dict)}
                    for item in report["recent_events"]:
                        vtype = str(item.get("type") or "")
                        if vtype and vtype not in recorded and vtype in report["hard_violations"]:
                            existing.append(
                                {
                                    "ts": item.get("ts") or datetime.now().timestamp(),
                                    "type": vtype,
                                    "label": item.get("label") or EVENT_LABELS.get(vtype, vtype),
                                    "detail": item.get("detail") or "",
                                }
                            )
                    session.violations = existing[-50:]
        except Exception as exc:  # noqa: BLE001 —— 写回失败不影响监考主链路
            logger.warning("监考结论写回业务对象失败 subject=%s:%s err=%s", row.subject_type, row.subject_id, exc)

    # ================================================================= 视图
    def _candidate_state(
        self,
        row: ProctoringSession,
        report: dict[str, Any],
        state: IntegrityState,  # noqa: ARG002 —— 保留语义参数
    ) -> ProctorStateResponse:
        cfg = self.config_snapshot()
        blocked = bool(row.blocked_at) and (self.strict or report["level"] == "void")
        reason = ""
        if blocked:
            reason = report["message"] or "监考检测到严重异常，本次作答已被阻断，请联系 HR"
        requirement = ProctorRequirement(
            screen_share=self.require_share,
            single_display=not settings.PROCTOR_ALLOW_MULTI_DISPLAY,
            no_remote_control=True,
            no_virtual_camera=True,
            fullscreen=True,
        )
        warning = ""
        if report["level"] in {"watch", "review"}:
            warning = "检测到异常行为（切屏 / 设备变更等），请保持在全屏考试页面，勿切换窗口或使用远程工具"
        elif report["level"] in {"serious", "void"}:
            warning = report["message"] or "作弊嫌疑较大，记录已同步 HR"
        return ProctorStateResponse(
            enabled=bool(settings.PROCTOR_ENABLED),
            strict=self.strict,
            risk_level=report["level"],
            blocked=blocked,
            block_reason=reason,
            warning=warning,
            requirements=requirement,
            heartbeat_seconds=cfg["heartbeat_seconds"],
            snapshot_interval_seconds=cfg["snapshot_interval_seconds"],
            screen_shared=bool(row.screen_shared),
            display_count=int(row.display_count or 1),
            event_total=int(row.event_total or 0),
            last_heartbeat_at=row.last_heartbeat_at,
        )

    def _disabled_state(self) -> ProctorStateResponse:
        return ProctorStateResponse(
            enabled=False,
            strict=False,
            requirements=ProctorRequirement(
                screen_share=False,
                single_display=False,
                no_remote_control=False,
                no_virtual_camera=False,
                fullscreen=False,
            ),
        )

    async def state(self, subject_type: str, token: str) -> ProctorStateResponse:
        """候选人自查（未上报过则返回初始状态，不建会话）。"""
        if not settings.PROCTOR_ENABLED:
            return self._disabled_state()
        row = None
        subject_type, subject_id, token, _n, _p = await self._resolve_subject(subject_type, token)
        if subject_id is not None:
            row = await self.sessions.get_by_subject(self.db, subject_type, subject_id)
        if row is None:
            row = await self.sessions.get_by_token(self.db, token)
        if row is None:
            return ProctorStateResponse(
                enabled=True,
                strict=self.strict,
                requirements=ProctorRequirement(
                    screen_share=self.require_share,
                    single_display=not settings.PROCTOR_ALLOW_MULTI_DISPLAY,
                ),
                heartbeat_seconds=self.config_snapshot()["heartbeat_seconds"],
                snapshot_interval_seconds=self.config_snapshot()["snapshot_interval_seconds"],
            )
        state = IntegrityState.from_dict(row.state)
        report = assess_integrity(state, config=self.config_snapshot())
        # 断流判定是「相对当前时间」的：即使没有新上报，也能发现候选人把页面关掉了
        if row.last_heartbeat_at and not row.heartbeat_lost:
            gap = (datetime.now() - row.last_heartbeat_at).total_seconds()
            if gap > self.config_snapshot()["heartbeat_timeout_seconds"]:
                report = dict(report)
                if report["level"] in {"clean", "watch"}:
                    report["level"] = "review"
                    report["message"] = f"监考心跳已中断 {int(gap)} 秒"
        return self._candidate_state(row, report, state)

    # ================================================================= HR 侧
    async def page_sessions(
        self,
        page: PageParams,
        *,
        subject_type: str | None = None,
        risk_level: str | None = None,
        keyword: str | None = None,
        min_score: float | None = None,
    ) -> PageResult[ProctorSessionSummary]:
        from sqlalchemy import or_

        extra = []
        if min_score is not None:
            extra.append(ProctoringSession.risk_score >= float(min_score))
        if risk_level == "flagged":
            extra.append(
                or_(
                    ProctoringSession.risk_level.in_(["review", "serious", "void"]),
                    ProctoringSession.blocked_at.is_not(None),
                )
            )
        elif risk_level:
            extra.append(ProctoringSession.risk_level == risk_level)
        result = await self.sessions.paginate(
            self.db,
            page,
            filters={"subject_type": subject_type},
            keyword=keyword,
            extra_where=extra or None,
        )
        items = [
            ProctorSessionSummary.model_validate(
                {
                    **{k: getattr(row, k) for k in (
                        "id", "subject_type", "subject_id", "subject_token", "candidate_name",
                        "position_title", "risk_level", "risk_advice", "event_total",
                        "started_at", "finished_at", "last_heartbeat_at", "created_at",
                    )},
                    "risk_score": float(row.risk_score or 0),
                    "blocked": row.blocked_at is not None,
                    "hard_violations": list(row.hard_violations or []),
                }
            )
            for row in result.items
        ]
        return PageResult(items=items, total=result.total, page=result.page,
                          page_size=result.page_size, pages=result.pages)

    async def report(self, session_ref_id: int) -> ProctorReportResponse:
        """HR 证据链：汇总 + 事件时间线 + 快照 URL + 处置建议。"""
        row = await self.sessions.get_by_id_or_404(self.db, session_ref_id)
        state = IntegrityState.from_dict(row.state)
        report = assess_integrity(state, config=self.config_snapshot())
        events = await self.events.list_by_session(self.db, row.id)
        snapshot_rows = await self.snapshots.list_by_session(self.db, row.id)
        timeline = [
            ProctorReportItem(
                ts=ev.occurred_at.timestamp() if ev.occurred_at else None,
                type=ev.event_type,
                label=ev.label or EVENT_LABELS.get(ev.event_type, ev.event_type),
                detail=ev.detail or "",
                score=float(ev.weight or 0),
                source=ev.source,
            )
            for ev in reversed(events)
        ]
        if not timeline:
            # 事件表被裁剪 / 尚未落库时，退化为状态机里的时间线（保证证据不丢）
            timeline = [ProctorReportItem(**item) for item in state.timeline[-60:]]
        return ProctorReportResponse(
            id=row.id,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            subject_token=row.subject_token,
            candidate_name=row.candidate_name,
            position_title=row.position_title,
            risk_score=float(row.risk_score or 0),
            risk_level=row.risk_level,
            risk_advice=row.risk_advice,
            blocked=row.blocked_at is not None,
            hard_violations=list(row.hard_violations or []),
            event_total=int(row.event_total or 0),
            started_at=row.started_at,
            finished_at=row.finished_at,
            last_heartbeat_at=row.last_heartbeat_at,
            created_at=row.created_at,
            message=report["message"],
            require_review=bool(report["require_review"]),
            void_suggested=bool(report["void_suggested"]),
            event_counts=report["event_counts"],
            event_labels=report["event_labels"],
            timeline=timeline,
            snapshots=[
                ProctorSnapshotItem(
                    id=snap.id,
                    source=snap.source,
                    trigger_type=snap.trigger_type or "",
                    captured_at=snap.captured_at,
                    url=self._snapshot_url(snap),
                    note=snap.note,
                )
                for snap in snapshot_rows
            ],
            integrity={**report, "baseline": state.baseline},
            strict_mode=bool(row.strict_mode),
            require_screen_share=bool(row.require_screen_share),
            screen_shared=bool(row.screen_shared),
            remote_control=bool(row.remote_control),
            display_count=int(row.display_count or 1),
            heartbeat_lost=bool(row.heartbeat_lost),
        )

    async def review(self, session_ref_id: int, *, verdict: str, note: str = "") -> ProctorReportResponse:
        """HR 复核结论：confirmed 确认作弊 / false_positive 误报 / pending 待复核。

        - confirmed：风险分抬到封顶并标注 void（业务侧据此作废成绩）；
        - false_positive：清零风险分并记录复核备注（保留原始事件，不清证据）；
        - 复核动作本身写入快照备注与状态机，留痕可追溯。
        """
        row = await self.sessions.get_by_id_or_404(self.db, session_ref_id)
        state = IntegrityState.from_dict(row.state)
        verdict = (verdict or "").strip().lower()
        if verdict not in {"confirmed", "false_positive", "pending"}:
            raise ParamError("复核结论只能是 confirmed / false_positive / pending")
        state.baseline = {**state.baseline, "review": {"verdict": verdict, "note": note[:200],
                                                        "at": datetime.now().isoformat(timespec="seconds")}}
        if verdict == "confirmed":
            row.risk_score = 100
            row.risk_level = "void"
            row.risk_advice = "void"
            row.blocked_at = row.blocked_at or datetime.now()
        elif verdict == "false_positive":
            row.risk_score = 0
            row.risk_level = "clean"
            row.risk_advice = "clean"
            row.blocked_at = None
        row.state = state.to_dict()
        await self.db.commit()
        logger.info("监考复核 session=%s verdict=%s", row.id, verdict)
        return await self.report(row.id)

    async def summary_for_subject(self, subject_type: str, subject_id: int) -> dict[str, Any] | None:
        """给业务层（报告 / 详情）取监考结论；无记录返回 None（不报错）。"""
        try:
            row = await self.sessions.get_by_subject(self.db, subject_type, subject_id)
        except Exception:  # noqa: BLE001 —— 表未创建 / 库异常时不影响主流程
            return None
        if row is None:
            return None
        state = IntegrityState.from_dict(row.state)
        report = assess_integrity(state, config=self.config_snapshot())
        return {
            "session_id": row.id,
            "risk_score": float(row.risk_score or 0),
            "risk_level": row.risk_level,
            "risk_advice": row.risk_advice,
            "advice_label": _risk_ok(row.risk_advice),
            "hard_violations": list(row.hard_violations or []),
            "event_counts": dict(row.event_counts or {}),
            "event_labels": {k: EVENT_LABELS.get(k, k) for k in (row.event_counts or {})},
            "message": report["message"],
            "require_review": bool(report["require_review"]),
            "blocked": row.blocked_at is not None,
            "heartbeat_lost": bool(row.heartbeat_lost),
            "display_count": int(row.display_count or 1),
            "screen_shared": bool(row.screen_shared),
            "remote_control": bool(row.remote_control),
            "event_total": int(row.event_total or 0),
            "last_heartbeat_at": row.last_heartbeat_at,
        }


__all__ = ["ALLOWED_SUBJECTS", "ProctoringService", "advise_label_map"]
