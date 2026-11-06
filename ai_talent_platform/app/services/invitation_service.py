"""人才邀约业务服务：创建发送、公开落地、笔试抽题/作答/交卷、监考帧检测。

标签：[Agent组] [P1]

设计要点：
- 题量按**简历解析结果**（work_years + skill_tags）计算，可用请求覆盖；
  题目在**创建邀约时**从题库（assessment_questions，Vanna组）随机抽样快照进
  ExamSession.question_ids，此后题库增删不影响已发试卷。
- 公开接口仅凭 token 访问（候选人无账号）；token 由 secrets 生成并查重。
- 邮件/短信发送走 invite_notify（SMTP 未配置=mock 记日志，永不阻断）；
- 监考帧检测走 ai/proctoring（YOLO→Haar→disabled 三级降级）。
- answer 字段在任何公开响应中都不序列化（题面只含 id/type/content/options）。
"""

from __future__ import annotations

import base64
import json
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.proctoring import evaluate_proctor, frame_luma_stats, get_detector
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.core.pagination import PageParams, build_page_result
from app.core.response import PageResult
from app.models.assessment import AssessmentQuestion
from app.models.invitation import ExamSession, TalentInvitation
from app.models.talent import Talent
from app.repositories.invitation_repository import (
    ExamSessionRepository,
    TalentInvitationRepository,
)
from app.schemas.invitation import (
    ExamQuestionResponse,
    ExamResultResponse,
    ExamStartResponse,
    FrameCheckResponse,
    InviteCreateRequest,
    InviteResponse,
    InviteSendResult,
    InviteVisitResponse,
    ViolationItem,
)
from app.services.invite_notify import build_invite_payload, send_email, send_sms

logger = logging.getLogger(__name__)

_EXAM_TYPES = ("written_test", "assessment")
_KNOWN_CHANNELS = ("sms", "email")


# --------------------------------------------------------------------------- 纯函数
def derive_question_count(work_years: int | None, skill_tags: Any) -> int:
    """按简历解析结果计算笔试题量（离线可测）。

    规则：基础 6 题 + min(工作年限,4) + min(技能数,8)//2 -> 期望 6~14 题，
    统一夹到 [5, 20]（题库不足时由调用方再向下取）。
    """
    wy = max(0, int(work_years or 0))
    skills = [s for s in (skill_tags or []) if isinstance(s, str) and s.strip()] if isinstance(skill_tags, (list, tuple)) else []
    n = 6 + min(wy, 4) + min(len(skills), 8) // 2
    return max(5, min(20, n))


def _scalar(value: Any) -> Any:
    """把 {key/text} 字典、单元素包装等归一为标量（平台 options 约定 [{key,text}]）。"""
    if isinstance(value, dict):
        for k in ("key", "value", "text"):
            if value.get(k) is not None:
                return value[k]
        return None
    if isinstance(value, (list, tuple)):
        return _scalar(value[0]) if value else None
    return value


def normalize_answer(value: Any) -> Any:
    """单选归一为字符串，多选归一为去重列表（保持出现顺序），文本保留。"""
    if isinstance(value, (list, tuple)):
        seen: list[str] = []
        for v in value:
            s = str(_scalar(v)).strip() if _scalar(v) is not None else ""
            if s and s not in seen:
                seen.append(s)
        return seen
    if value is None:
        return None
    scalar = _scalar(value)
    if scalar is None:
        return None
    return str(scalar).strip()


def grade_answers(
    questions: list[AssessmentQuestion], answers: dict[str, Any]
) -> tuple[float, float, dict[str, Any], bool]:
    """客观题自动阅卷（离线可测）。

    返回 (得分, 卷面总分, 逐题明细, 是否存在待人工文本题)。
    - single: 归一化字符串全等；multiple: 集合全等（不计顺序）；
    - text: manual=True，score 记 0（待人工，不参与自动得分）。
    """
    score = 0.0
    total = 0.0
    detail: dict[str, Any] = {}
    has_manual = False
    for q in questions:
        qid = str(q.id)
        total += float(q.score or 0)
        got = answers.get(qid)
        if got is None:
            got = answers.get(int(qid)) if qid.isdigit() else None
        if q.type == "single":
            expected = normalize_answer(q.answer)
            ok = got is not None and normalize_answer(got) == expected
            point = float(q.score or 0) if ok else 0.0
            detail[qid] = {"correct": ok, "got": normalize_answer(got), "score": point, "manual": False}
            score += point
        elif q.type == "multiple":
            expected_raw = q.answer if isinstance(q.answer, list) else ([q.answer] if q.answer is not None else [])
            expected_set = set(normalize_answer(expected_raw) or [])
            got_list = normalize_answer(got) or []
            got_set = set(got_list if isinstance(got_list, list) else [got_list])
            ok = bool(expected_set) and got_set == expected_set
            point = float(q.score or 0) if ok else 0.0
            detail[qid] = {"correct": ok, "got": got_list, "score": point, "manual": False}
            score += point
        else:  # text（及未知题型）待人工
            has_manual = True
            detail[qid] = {"correct": None, "got": normalize_answer(got), "score": 0.0, "manual": True}
    return round(score, 2), round(total, 2), detail, has_manual


def build_invitation_link(token: str) -> str:
    return f"{settings.INVITE_BASE_URL.rstrip('/')}/invite/{token}"


# --------------------------------------------------------------------------- 服务
class InvitationService:
    """人才邀约 / 笔试 / 面试间业务。"""

    def __init__(self, db: AsyncSession | None) -> None:
        self.db = db
        self.repo = TalentInvitationRepository()
        self.sessions = ExamSessionRepository()

    # -------------------------------------------------------------- 序列化
    def _to_response(self, row: TalentInvitation, talent_name: str | None = None) -> InviteResponse:
        name = talent_name
        if not name and isinstance(row.talent_snapshot, dict):
            name = row.talent_snapshot.get("name")
        return InviteResponse(
            id=row.id,
            talent_id=row.talent_id,
            talent_name=name,
            type=row.type,
            title=row.title or "",
            token=row.token,
            url=build_invitation_link(row.token),
            status=row.status,
            channels=list(row.channels or []),
            send_results=[InviteSendResult(**r) for r in (row.send_results or []) if isinstance(r, dict)],
            question_count=row.question_count,
            duration_minutes=row.duration_minutes,
            interview_time=row.interview_time,
            expires_at=row.expires_at,
            created_at=getattr(row, "created_at", None),
        )

    # -------------------------------------------------------------- 创建/查询（host）
    async def create(self, body: InviteCreateRequest, *, current_user: Any) -> InviteResponse:
        if self.db is None:  # pragma: no cover - 路由层保证有 db
            raise BusinessRuleError("数据库不可用", code=5001, status_code=500)
        for ch in body.channels:
            if ch not in _KNOWN_CHANNELS:
                raise ParamError(f"不支持的发送渠道: {ch}")

        # 1) 人才解析：库内档案优先，否则用请求快照（演示卡片无档案行）
        talent: Talent | None = None
        if body.talent_id:
            talent = (await self.db.execute(
                select(Talent).where(Talent.id == body.talent_id, Talent.is_deleted.is_(False))
            )).scalar_one_or_none()
            if talent is None:
                raise NotFoundError("人才不存在")
        snapshot: dict[str, Any] = {
            "name": (talent.name if talent else body.talent_name) or "",
            "mobile": (talent.mobile if talent else body.mobile) or "",
            "email": (talent.email if talent else body.email) or "",
        }
        if "sms" in body.channels and not snapshot.get("mobile"):
            raise ParamError("短信发送需要手机号：请完善人才档案或在请求中提供 mobile")
        if "email" in body.channels and not snapshot.get("email"):
            raise ParamError("邮件发送需要邮箱：请完善人才档案或在请求中提供 email")

        # 2) 题量（简历解析信号）+ 抽题快照
        question_count: int | None = None
        question_ids: list[int] = []
        if body.type in _EXAM_TYPES:
            pool_ids = [
                int(i) for i in (await self.db.execute(
                    select(AssessmentQuestion.id).where(AssessmentQuestion.is_deleted.is_(False))
                )).scalars()
            ]
            if not pool_ids:
                raise BusinessRuleError(
                    "题库为空：请先在测评模块导入题目（或运行 scripts/seed_question_bank.py 生成演示题库）"
                )
            want = body.question_count or derive_question_count(
                getattr(talent, "work_years", None), getattr(talent, "skill_tags", None)
            )
            question_count = min(max(1, want), len(pool_ids))
            question_ids = random.sample(pool_ids, question_count)

        # 3) 落库
        duration = body.duration_minutes or settings.INVITE_EXAM_DURATION_MINUTES
        expires_hours = body.expires_hours or settings.INVITE_TOKEN_TTL_HOURS
        token = await self.repo.mint_token(self.db)
        title = body.title or (
            "面试邀约" if body.type == "interview" else ("测评邀约" if body.type == "assessment" else "笔试邀约")
        )
        invite = await self.repo.create(
            self.db,
            {
                "talent_id": body.talent_id,
                "talent_snapshot": snapshot,
                "type": body.type,
                "token": token,
                "title": title,
                "channels": list(body.channels),
                "question_count": question_count,
                "duration_minutes": duration,
                "interview_time": body.interview_time,
                "expires_at": datetime.now() + timedelta(hours=expires_hours),
                "status": "created",
                "created_by": getattr(current_user, "id", None),
            },
        )
        if body.type in _EXAM_TYPES:
            await self.sessions.create(
                self.db,
                {
                    "invitation_id": invite.id,
                    "token": token,
                    "question_ids": question_ids,
                    "answers": None,
                    "violations": [],
                    "proctor_state": {},
                    "status": "in_progress",
                },
            )
        await self.db.commit()

        # 4) 通知（commit 后发送，链接已持久；mock 降级不阻断）
        link = build_invitation_link(token)
        interview_time_iso = (
            body.interview_time.strftime("%Y-%m-%d %H:%M") if body.interview_time else None
        )
        subject, text_body = build_invite_payload(
            type_=body.type, title=title, candidate_name=snapshot.get("name", ""), link=link, interview_time=interview_time_iso
        )
        results: list[dict[str, Any]] = []
        for ch in body.channels:
            if ch == "sms":
                results.append(await send_sms(snapshot.get("mobile", ""), f"{subject}\n{text_body}"))
            elif ch == "email":
                results.append(await send_email(snapshot.get("email", ""), subject, text_body))
        invite.send_results = results
        invite.status = "sent" if any(r.get("ok") for r in results) else "created"
        await self.db.commit()
        return self._to_response(invite, snapshot.get("name"))

    async def page_list(
        self,
        page: PageParams,
        *,
        talent_id: int | None = None,
        type_: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> PageResult[Any]:
        if self.db is None:
            return build_page_result([], 0, page)
        filters: dict[str, Any] = {}
        if talent_id:
            filters["talent_id"] = talent_id
        if type_:
            filters["type"] = type_
        if status:
            filters["status"] = status
        result = await self.repo.paginate(self.db, page, filters=filters or None, keyword=keyword or None)
        rows: list[TalentInvitation] = list(result.items or [])
        names = await self._talent_names([r.talent_id for r in rows if r.talent_id])
        items = [self._to_response(r, names.get(r.talent_id)) for r in rows]
        result.items = items
        return result

    async def detail(self, invite_id: int) -> InviteResponse:
        row = await self._get_invite_by_id(invite_id)
        names = await self._talent_names([row.talent_id] if row.talent_id else [])
        return self._to_response(row, names.get(row.talent_id))

    async def _talent_names(self, ids: list[int | None]) -> dict[int, str]:
        clean = [i for i in ids if i]
        if not clean:
            return {}
        rows = (await self.db.execute(select(Talent.id, Talent.name).where(Talent.id.in_(clean)))).all()
        return {int(r[0]): str(r[1]) for r in rows}

    async def _get_invite_by_id(self, invite_id: int) -> TalentInvitation:
        if self.db is None:
            raise NotFoundError("邀约不存在")
        row = await self.repo.get_by_id(self.db, invite_id)
        if row is None:
            raise NotFoundError("邀约不存在")
        return row

    # -------------------------------------------------------------- 公开（候选人）
    async def _get_valid_invite(self, token: str) -> TalentInvitation:
        if self.db is None:
            raise NotFoundError("邀约不存在或已失效")
        invite = await self.repo.get_by_token(self.db, token)
        if invite is None:
            raise NotFoundError("邀约不存在或已失效")
        if invite.status == "cancelled":
            raise BusinessRuleError("邀约已被取消")
        if invite.expires_at and invite.expires_at < datetime.now():
            if invite.status not in ("submitted", "finished", "expired"):
                invite.status = "expired"
                await self.db.commit()
            raise BusinessRuleError("邀约链接已过期")
        return invite

    async def visit(self, token: str) -> InviteVisitResponse:
        """落地页信息（打开链接即访问；首次打开把状态推进到 opened）。"""
        invite = await self._get_valid_invite(token)
        if invite.status in ("created", "sent"):
            invite.status = "opened"
            await self.db.commit()
        detector = get_detector()
        ice: list[dict[str, Any]] = []
        try:
            parsed = json.loads(settings.WEBRTC_ICE_SERVERS or "[]")
            if isinstance(parsed, list):
                ice = [x for x in parsed if isinstance(x, dict)]
        except (ValueError, TypeError):  # pragma: no cover - 配置容错
            ice = []
        return InviteVisitResponse(
            type=invite.type,
            title=invite.title or "",
            status=invite.status,
            candidate_name=(invite.talent_snapshot or {}).get("name"),
            interview_time=invite.interview_time,
            expires_at=invite.expires_at,
            duration_minutes=invite.duration_minutes,
            question_count=invite.question_count,
            proctor_enabled=detector.enabled,
            proctor_engine=detector.engine,
            frame_interval_seconds=max(2, settings.PROCTOR_INTERVAL_SECONDS),
            ice_servers=ice,
        )

    async def _get_session_for_token(self, token: str) -> tuple[TalentInvitation, ExamSession]:
        invite = await self._get_valid_invite(token)
        if invite.type not in _EXAM_TYPES:
            raise BusinessRuleError("该邀约不是笔试/测评，无法作答")
        session = await self.sessions.get_by_token(self.db, token) if self.db else None
        if session is None:
            raise NotFoundError("作答会话不存在")
        return invite, session

    async def _load_questions(self, question_ids: list[int]) -> list[AssessmentQuestion]:
        if not question_ids:
            return []
        rows = (await self.db.execute(
            select(AssessmentQuestion).where(AssessmentQuestion.id.in_(question_ids))
        )).scalars().all()
        by_id = {q.id: q for q in rows}
        return [by_id[i] for i in question_ids if i in by_id]

    @staticmethod
    def _sanitize(questions: list[AssessmentQuestion]) -> list[ExamQuestionResponse]:
        """题面脱敏：answer 绝不序列化；options 归一为 [{key,text}]（key 即作答值）。"""
        out: list[ExamQuestionResponse] = []
        for i, q in enumerate(questions):
            options: list[dict[str, str]] | None = None
            if isinstance(q.options, list) and q.options:
                options = []
                for idx, o in enumerate(q.options):
                    if isinstance(o, dict):
                        key = str(o.get("key") or o.get("value") or "").strip() or chr(65 + idx)
                        text = str(o.get("text") or o.get("label") or key)
                    else:
                        key, text = chr(65 + idx), str(o)
                    options.append({"key": key, "text": text})
            out.append(
                ExamQuestionResponse(
                    id=q.id, seq=i + 1, type=q.type, content=q.content, options=options
                )
            )
        return out

    async def start_exam(self, token: str, *, client_ip: str | None = None) -> ExamStartResponse:
        """开始/恢复作答：首次打开锁定截止时间，返回题面快照（无 answer）。"""
        invite, session = await self._get_session_for_token(token)
        detector = get_detector()
        now = datetime.now()
        if session.status == "submitted":
            return ExamStartResponse(
                status="submitted",
                started_at=session.started_at,
                deadline=session.deadline,
                questions=[],
                total_score=float(session.total_score or 0),
                proctor_enabled=detector.enabled,
                proctor_engine=detector.engine,
                frame_interval_seconds=max(2, settings.PROCTOR_INTERVAL_SECONDS),
            )
        # 截止时间：min(链接过期, 首次打开+时长)
        if session.started_at is None:
            session.started_at = now
            deadline = now + timedelta(minutes=invite.duration_minutes)
            if invite.expires_at:
                deadline = min(deadline, invite.expires_at)
            session.deadline = deadline
            session.status = "in_progress"
            if client_ip:
                session.candidate_ip = client_ip
            await self.db.commit()
        elif session.deadline and session.deadline < now:
            session.status = "expired"
            await self.db.commit()
            raise BusinessRuleError("考试已超时")
        questions = await self._load_questions([int(i) for i in (session.question_ids or [])])
        total = round(sum(float(q.score or 0) for q in questions), 2)
        return ExamStartResponse(
            status=session.status,
            started_at=session.started_at,
            deadline=session.deadline,
            questions=self._sanitize(questions),
            total_score=total,
            proctor_enabled=detector.enabled,
            proctor_engine=detector.engine,
            frame_interval_seconds=max(2, settings.PROCTOR_INTERVAL_SECONDS),
        )

    async def submit(self, token: str, answers: dict[str, Any], *, client_ip: str | None = None) -> ExamResultResponse:
        invite, session = await self._get_session_for_token(token)
        now = datetime.now()
        if session.status == "submitted":
            return await self._exam_result(session)
        if session.started_at is None:
            raise BusinessRuleError("尚未开始作答")
        if session.deadline and session.deadline < now:
            session.status = "expired"
            await self.db.commit()
            raise BusinessRuleError("考试已超时，无法交卷")
        questions = await self._load_questions([int(i) for i in (session.question_ids or [])])
        normalized: dict[str, Any] = {}
        for q in questions:
            raw = answers.get(str(q.id))
            if raw is None:
                raw = answers.get(q.id)
            if raw is not None:
                normalized[str(q.id)] = normalize_answer(raw)
        score, total, detail, has_manual = grade_answers(questions, normalized)
        session.answers = normalized
        session.score = score
        session.total_score = total
        session.score_detail = detail
        session.status = "submitted"
        session.submitted_at = now
        if client_ip:
            session.candidate_ip = client_ip
        invite.status = "submitted"
        await self.db.commit()
        return await self._exam_result(session)

    async def _exam_result(self, session: ExamSession) -> ExamResultResponse:
        detail = session.score_detail or {}
        pending = any(bool(v.get("manual")) for v in detail.values() if isinstance(v, dict))
        violations = list(session.violations or [])
        return ExamResultResponse(
            status=session.status,
            score=float(session.score) if session.score is not None else None,
            total_score=float(session.total_score) if session.total_score is not None else None,
            submitted_at=session.submitted_at,
            violations_count=len(violations),
            pending_manual=pending,
        )

    async def result(self, token: str) -> ExamResultResponse:
        invite, session = await self._get_session_for_token(token)
        return await self._exam_result(session)

    # -------------------------------------------------------------- 监考帧
    async def check_frame(self, token: str, image_b64: str, *, client_ip: str | None = None) -> FrameCheckResponse:
        _invite, session = await self._get_session_for_token(token)
        detector = get_detector()
        if not detector.enabled:
            return FrameCheckResponse(
                enabled=False, engine=detector.engine, violations=[], total_violations=len(session.violations or [])
            )
        if session.started_at is None:
            raise BusinessRuleError("请先开始作答")
        # 解码 base64（容错 data: 前缀）
        raw = image_b64.split(",", 1)[-1] if "," in image_b64[:64] else image_b64
        try:
            image = base64.b64decode(raw, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise ParamError("监考帧解码失败") from exc
        if not image:
            raise ParamError("监考帧为空")
        luma = frame_luma_stats(image)
        detections = detector.detect(image)
        interval = max(2, settings.PROCTOR_INTERVAL_SECONDS)
        grace = max(2, 30 // interval)  # 连续 ~30s 无人才记违规
        new_state, new_violations = evaluate_proctor(
            session.proctor_state, detections, luma, time.time(), no_face_grace=grace
        )
        session.proctor_state = new_state
        merged = list(session.violations or []) + new_violations
        session.violations = merged[-50:]
        if client_ip and not session.candidate_ip:
            session.candidate_ip = client_ip
        await self.db.commit()
        return FrameCheckResponse(
            enabled=True,
            engine=detector.engine,
            violations=[ViolationItem(**v) for v in new_violations],
            total_violations=len(session.violations or []),
            person_like=int(new_state.get("person_like") or 0),
        )
