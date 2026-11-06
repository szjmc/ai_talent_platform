"""人才邀约 / 笔试 / 面试间测试：题量与阅卷、监考状态机、信令房间、路由与降级。

标签：[Agent组] [P1]

全部用例离线运行：不依赖 MySQL / YOLO 权重 / SMTP / 短信网关（相关链路 mock）。
验收命令：pytest tests/test_invites.py -q
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.proctoring import Detection, evaluate_proctor, frame_luma_stats, get_detector
from app.ai.proctoring.detector import NullDetector
from app.core.application import create_application
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.security import generate_random_token
from app.infrastructure.database.session import get_db
from app.models.assessment import AssessmentQuestion
from app.repositories.invitation_repository import TalentInvitationRepository
from app.schemas.invitation import InviteCreateRequest
from app.services.invitation_service import (
    InvitationService,
    build_invitation_link,
    derive_question_count,
    grade_answers,
    normalize_answer,
)
from app.services.interview_room import InterviewRoomManager, peer_role
from app.services.invite_notify import build_invite_payload, send_email, send_sms


# ------------------------------------------------------------------ 纯函数：题量与阅卷
class TestDeriveQuestionCount:
    def test_defaults_to_six(self) -> None:
        assert derive_question_count(None, None) == 6

    def test_grows_with_experience_and_skills(self) -> None:
        base = derive_question_count(0, [])
        more_years = derive_question_count(8, [])
        more_skills = derive_question_count(0, [f"s{i}" for i in range(8)])
        assert more_years > base
        assert more_skills > base

    def test_clamped_to_bounds(self) -> None:
        assert derive_question_count(-3, None) >= 5
        big = derive_question_count(60, [f"skill-{i}" for i in range(50)])
        assert 5 <= big <= 20

    def test_non_list_skills_ignored(self) -> None:
        assert derive_question_count(5, "Python,Go") == 6 + min(5, 4) + 0


class TestNormalizeAnswer:
    def test_dict_key_extracted(self) -> None:
        assert normalize_answer({"key": "A"}) == "A"

    def test_list_of_dicts(self) -> None:
        assert normalize_answer([{"key": "A"}, {"key": "B"}]) == ["A", "B"]

    def test_list_dedup_preserves_order(self) -> None:
        assert normalize_answer(["B", "A", "B"]) == ["B", "A"]

    def test_none_stays_none(self) -> None:
        assert normalize_answer(None) is None

    def test_scalar_string(self) -> None:
        assert normalize_answer("  C ") == "C"


def _q(qid: int, type_: str, answer, score: int = 10, options=None) -> AssessmentQuestion:
    return AssessmentQuestion(
        id=qid, type=type_, content=f"题{qid}", options=options, answer=answer, score=score
    )


class TestGradeAnswers:
    def test_single_exact_match(self) -> None:
        score, total, detail, manual = grade_answers([_q(1, "single", "B")], {"1": "B"})
        assert (score, total, manual) == (10.0, 10.0, False)
        assert detail["1"]["correct"] is True

    def test_single_wrong(self) -> None:
        score, _, detail, _ = grade_answers([_q(1, "single", "B")], {"1": "C"})
        assert score == 0.0
        assert detail["1"]["correct"] is False

    def test_single_dict_answer(self) -> None:
        score, _, _, _ = grade_answers([_q(1, "single", {"key": "A"})], {"1": {"key": "A"}})
        assert score == 10.0

    def test_multiple_set_equality_ignores_order(self) -> None:
        q = _q(2, "multiple", ["A", "B", "C"])
        score, _, _, _ = grade_answers([q], {"2": ["C", "A", "B"]})
        assert score == 10.0

    def test_multiple_partial_is_zero(self) -> None:
        q = _q(2, "multiple", ["A", "B", "C"])
        score, _, detail, _ = grade_answers([q], {"2": ["A", "B"]})
        assert score == 0.0
        assert detail["2"]["correct"] is False

    def test_text_pending_manual(self) -> None:
        q = _q(3, "text", "")
        score, total, detail, manual = grade_answers([q], {"3": "我的回答"})
        assert manual is True
        assert score == 0.0
        assert total == 10.0
        assert detail["3"]["manual"] is True

    def test_unanswered_counts_total_not_score(self) -> None:
        questions = [_q(1, "single", "A"), _q(2, "single", "B")]
        score, total, detail, _ = grade_answers(questions, {"1": "A"})
        assert (score, total) == (10.0, 20.0)
        assert detail["2"]["correct"] is False

    def test_total_score_sums_question_scores(self) -> None:
        questions = [_q(i, "single", "A", score=5) for i in range(1, 5)]
        _, total, _, _ = grade_answers(questions, {})
        assert total == 20.0


class TestSanitizeQuestions:
    def test_options_normalized_and_answer_never_exposed(self) -> None:
        q = _q(
            7,
            "single",
            "A",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        )
        dump = InvitationService._sanitize([q])[0].model_dump()
        assert dump["options"] == [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}]
        assert "answer" not in dump

    def test_plain_string_options_get_positional_keys(self) -> None:
        q = _q(8, "single", "A", options=["甲", "乙"])
        opts = InvitationService._sanitize([q])[0].options
        assert opts == [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}]

    def test_empty_options_are_none(self) -> None:
        q = _q(9, "text", "", options=[])
        assert InvitationService._sanitize([q])[0].options is None

    def test_seq_numbering(self) -> None:
        qs = [_q(1, "single", "A"), _q(2, "single", "B")]
        seqs = [q.seq for q in InvitationService._sanitize(qs)]
        assert seqs == [1, 2]


# ------------------------------------------------------------------ 通知降级
class TestNotify:
    def test_payload_contains_link_and_time(self) -> None:
        subject, body = build_invite_payload(
            type_="interview", title="面试", candidate_name="张三",
            link="https://x/invite/abc", interview_time="2026-11-07 10:00",
        )
        assert "面试" in subject
        assert "https://x/invite/abc" in body
        assert "2026-11-07 10:00" in body

    def test_payload_written_test_has_camera_rule(self) -> None:
        _, body = build_invite_payload(
            type_="written_test", title="笔试", candidate_name="李四",
            link="https://x/invite/t", interview_time=None,
        )
        assert "摄像头" in body
        assert "2026" not in body

    async def test_email_mock_when_smtp_unconfigured(self) -> None:
        result = await send_email("a@b.com", "标题", "正文")
        assert result == {"channel": "email", "ok": True, "mode": "mock", "error": None}

    async def test_email_requires_address(self) -> None:
        result = await send_email("", "标题", "正文")
        assert result["ok"] is False

    async def test_sms_mock(self) -> None:
        result = await send_sms("13800000000", "内容")
        assert result["ok"] is True and result["mode"] == "mock"

    def test_link_builder_strips_trailing_slash(self, monkeypatch) -> None:
        from app.config.settings import settings

        monkeypatch.setattr(settings, "INVITE_BASE_URL", "https://host/app/")
        assert build_invitation_link("tok") == "https://host/app/invite/tok"


# ------------------------------------------------------------------ 邀约请求校验
class TestInviteSchema:
    def test_interview_requires_time(self) -> None:
        with pytest.raises(ValidationError):
            InviteCreateRequest(type="interview", channels=["sms"], mobile="138")

    def test_interview_ok(self) -> None:
        body = InviteCreateRequest(
            type="interview", channels=["sms"], mobile="138",
            interview_time="2026-11-07T10:00:00",
        )
        assert body.type == "interview"

    def test_duplicate_channels_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InviteCreateRequest(channels=["sms", "sms"], mobile="138")

    def test_empty_channels_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InviteCreateRequest(channels=[], mobile="138")

    def test_sms_without_mobile_requires_talent(self) -> None:
        with pytest.raises(ValidationError):
            InviteCreateRequest(channels=["sms"])

    def test_sms_with_talent_id_skips_local_contact_check(self) -> None:
        body = InviteCreateRequest(channels=["sms"], talent_id=7)
        assert body.talent_id == 7


class TestServiceGuards:
    async def test_create_without_db_raises(self) -> None:
        svc = InvitationService(None)
        body = InviteCreateRequest(channels=["sms"], mobile="138", question_count=5)
        with pytest.raises(BusinessRuleError):
            await svc.create(body, current_user=SimpleNamespace(id=1))

    async def test_visit_without_db_is_not_found(self) -> None:
        svc = InvitationService(None)
        with pytest.raises(NotFoundError):
            await svc.visit("no-such-token")

    async def test_question_count_capped_by_pool_would_happen(self) -> None:
        # 题量计算纯函数路径（create 的 pool cap 逻辑依赖 DB，离线仅验证公式边界）
        assert derive_question_count(10, ["a", "b", "c"]) <= 20


# ------------------------------------------------------------------ 监考状态机
class TestProctorState:
    def _run(self, state, dets, luma=(120.0, 40.0), ts=1.0, grace=3):
        return evaluate_proctor(state, dets, luma, ts, no_face_grace=grace)

    def test_no_face_triggers_once_after_grace(self) -> None:
        state, v1 = self._run(None, [])
        state, v2 = self._run(state, [])
        assert v1 == [] and v2 == []
        state, v3 = self._run(state, [])
        assert [x["type"] for x in v3] == ["no_face"]
        state, v4 = self._run(state, [])
        assert v4 == []  # flagged 位防刷屏

    def test_no_face_refires_after_recovery(self) -> None:
        state, _ = self._run(None, [])
        state, _ = self._run(state, [])
        _, v3 = self._run(state, [])
        assert v3
        state, _ = self._run(state, [Detection("face", 1.0, (0, 0, 1, 1))])  # 恢复清 flagged
        state, _ = self._run(state, [])
        state, _ = self._run(state, [])
        _, v = self._run(state, [])  # 再次连续 3 帧
        assert [x["type"] for x in v] == ["no_face"]

    def test_multi_person_once_until_clear(self) -> None:
        two = [Detection("person", 0.9, (0, 0, 1, 1)), Detection("face", 0.9, (5, 5, 9, 9))]
        state, v1 = self._run(None, two)
        state, v2 = self._run(state, two)
        assert [x["type"] for x in v1] == ["multi_person"]
        assert v2 == []
        state, _ = self._run(state, [Detection("person", 0.9, (0, 0, 1, 1))])
        _, v = self._run(state, two)
        assert [x["type"] for x in v] == ["multi_person"]

    def test_camera_blocked_by_dark_frame(self) -> None:
        state, v1 = self._run(None, [Detection("person", 1.0, (0, 0, 1, 1))], luma=(5.0, 1.0))
        state, v2 = self._run(state, [Detection("person", 1.0, (0, 0, 1, 1))], luma=(5.0, 1.0))
        assert [x["type"] for x in v1] == ["camera_blocked"]
        assert v2 == []
        _, v3 = self._run(state, [Detection("person", 1.0, (0, 0, 1, 1))], luma=(120.0, 40.0))
        assert v3 == []

    def test_flat_white_frame_blocked(self) -> None:
        _, v = self._run(None, [Detection("person", 1.0, (0, 0, 1, 1))], luma=(250.0, 1.0))
        assert [x["type"] for x in v] == ["camera_blocked"]

    def test_phone_detection_only_from_yolo_labels(self) -> None:
        dets = [
            Detection("person", 0.9, (0, 0, 10, 10)),
            Detection("cell phone", 0.8, (1, 1, 2, 2)),
        ]
        state, v1 = self._run(None, dets)
        assert "phone" in [x["type"] for x in v1]
        state, v2 = self._run(state, dets)
        assert v2 == []
        state, _ = self._run(state, [Detection("person", 0.9, (0, 0, 10, 10))])
        _, v = self._run(state, dets)
        assert "phone" in [x["type"] for x in v]

    def test_frames_counter_increments(self) -> None:
        state, _ = self._run(None, [])
        state, _ = self._run(state, [])
        assert state["frames"] == 2
        assert state["no_face_streak"] == 2

    def test_person_face_counts_as_present(self) -> None:
        state, v = self._run(None, [Detection("face", 1.0, (0, 0, 5, 5))])
        assert state["person_like"] == 1
        assert v == [] or all(x["type"] != "no_face" for x in v)


class TestFrameStatsAndDetector:
    def test_black_image_stats(self) -> None:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (0, 0, 0)).save(buf, format="JPEG")
        mean, std = frame_luma_stats(buf.getvalue())
        assert mean < 18.0

    def test_noise_image_stats(self) -> None:
        from PIL import Image

        buf = io.BytesIO()
        Image.effect_noise((64, 64), 64).convert("RGB").save(buf, format="JPEG")
        mean, std = frame_luma_stats(buf.getvalue())
        assert std > 5.0

    def test_get_detector_returns_known_engine_and_caches(self) -> None:
        d1 = get_detector()
        d2 = get_detector()
        assert d1 is d2
        assert d1.engine in ("yolo_onnx", "haar_face", "disabled")

    def test_null_detector_never_raises(self) -> None:
        d = NullDetector()
        assert d.enabled is False
        assert d.detect(b"whatever") == []


# ------------------------------------------------------------------ 面试间信令房间
class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def receive_json(self) -> dict:  # pragma: no cover - 房间测试不消费
        raise RuntimeError("not used")


class TestInterviewRoom:
    async def test_join_both_roles(self) -> None:
        room = InterviewRoomManager()
        ok1, err1 = await room.join("t1", "candidate", FakeWebSocket())
        ok2, err2 = await room.join("t1", "interviewer", FakeWebSocket())
        assert (ok1, ok2) == (True, True) and err1 is None and err2 is None
        assert room.roles("t1") == {"candidate", "interviewer"}

    async def test_duplicate_role_rejected(self) -> None:
        room = InterviewRoomManager()
        await room.join("t1", "candidate", FakeWebSocket())
        ok, reason = await room.join("t1", "candidate", FakeWebSocket())
        assert ok is False
        assert reason and "已在" in reason

    async def test_relay_injects_from_and_reaches_peer(self) -> None:
        room = InterviewRoomManager()
        cand, inter = FakeWebSocket(), FakeWebSocket()
        await room.join("t1", "candidate", cand)
        await room.join("t1", "interviewer", inter)
        delivered = await room.relay("t1", "candidate", {"type": "offer", "sdp": "x"})
        assert delivered is True
        assert inter.sent[-1] == {"type": "offer", "sdp": "x", "from": "candidate"}

    async def test_relay_without_peer_returns_false(self) -> None:
        room = InterviewRoomManager()
        await room.join("t1", "candidate", FakeWebSocket())
        assert await room.relay("t1", "candidate", {"type": "ice"}) is False

    async def test_leave_returns_peer_and_cleans_room(self) -> None:
        room = InterviewRoomManager()
        cand, inter = FakeWebSocket(), FakeWebSocket()
        await room.join("t1", "candidate", cand)
        await room.join("t1", "interviewer", inter)
        peer = room.leave("t1", "candidate", cand)
        assert peer is inter
        assert room.roles("t1") == {"interviewer"}
        room.leave("t1", "interviewer", inter)
        assert room.roles("t1") == set()

    async def test_stale_leave_does_not_kick_current(self) -> None:
        room = InterviewRoomManager()
        current = FakeWebSocket()
        await room.join("t1", "candidate", current)
        stale = FakeWebSocket()  # 未注册的旧实例
        peer = room.leave("t1", "candidate", stale)
        assert peer is current  # 返回当前在房者，且未被踢出
        assert "candidate" in room.roles("t1")

    async def test_notify_peer(self) -> None:
        room = InterviewRoomManager()
        cand, inter = FakeWebSocket(), FakeWebSocket()
        await room.join("t1", "candidate", cand)
        await room.join("t1", "interviewer", inter)
        await room.notify_peer("t1", "candidate", {"type": "peer-joined", "role": "candidate"})
        assert inter.sent[-1]["type"] == "peer-joined"

    async def test_mark_completed_once(self) -> None:
        room = InterviewRoomManager()
        assert room.mark_completed("t1") is True
        assert room.mark_completed("t1") is False

    def test_peer_role(self) -> None:
        assert peer_role("candidate") == "interviewer"
        assert peer_role("interviewer") == "candidate"


# ------------------------------------------------------------------ 路由与公开接口降级
class TestInviteRoutes:
    @pytest.fixture()
    def client(self):
        app = create_application()

        async def _no_db():
            yield None

        app.dependency_overrides[get_db] = _no_db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def test_create_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/v1/invites", json={"type": "written_test", "channels": ["sms"], "mobile": "138"})
        assert resp.status_code == 401
        assert resp.json()["code"] == 2001

    def test_list_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invites")
        assert resp.status_code == 401

    def test_detail_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invites/1")
        assert resp.status_code == 401

    def test_public_visit_unknown_token_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invites/visit/no-such-token")
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_public_exam_unknown_token_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/invites/exam/no-such-token")
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_swagger_lists_invites(self, client: TestClient) -> None:
        spec = client.get("/openapi.json").json()
        paths = [p for p in spec["paths"] if p.startswith("/api/v1/invites")]
        assert "/api/v1/invites" in paths
        assert "/api/v1/invites/visit/{token}" in paths
        assert "/api/v1/invites/exam/{token}" in paths


class TestInviteWebSocket:
    async def test_invalid_role_rejected(self) -> None:
        from app.routers.invite_router import invite_ws

        ws = FakeWebSocket()
        await invite_ws(ws, "tok", role="hacker")
        assert ws.accepted is True
        assert ws.sent[0]["code"] == 4400
        assert ws.close_code == 4400

    async def test_db_unavailable_sends_error_then_closes(self, monkeypatch) -> None:
        from app.routers import invite_router

        @asynccontextmanager
        async def _fake_scope():
            raise RuntimeError("mysql down (offline test)")
            yield None  # pragma: no cover

        monkeypatch.setattr(invite_router, "session_scope", _fake_scope)
        ws = FakeWebSocket()
        await invite_router.invite_ws(ws, "tok", role="candidate")
        assert ws.accepted is True
        assert ws.sent and ws.sent[0]["type"] == "error"
        assert ws.sent[0]["code"] == 5000
        assert ws.close_code == 1011

    async def test_join_relay_peer_events_and_leave(self, monkeypatch) -> None:
        """双人进房 -> joined/peer-joined -> offer 中继 -> 取消任务离房（全离线）。"""
        import asyncio
        from contextlib import suppress

        from app.models.invitation import TalentInvitation
        from app.routers import invite_router

        invite = TalentInvitation(
            id=1, type="interview", token="tok-room", title="面试",
            status="opened", interview_time=datetime.now(), duration_minutes=30,
            expires_at=datetime.now() + timedelta(hours=2),
        )

        class _Svc:
            def __init__(self, db):
                pass

            async def _get_valid_invite(self, token):
                return invite

        @asynccontextmanager
        async def _fake_scope():
            yield object()

        async def _blocking():
            await asyncio.Event().wait()

        monkeypatch.setattr(invite_router, "session_scope", _fake_scope)
        monkeypatch.setattr(invite_router, "InvitationService", _Svc)
        fresh = InterviewRoomManager()
        monkeypatch.setattr(invite_router, "room_manager", fresh)

        cand, inter = FakeWebSocket(), FakeWebSocket()
        monkeypatch.setattr(cand, "receive_json", _blocking)
        monkeypatch.setattr(inter, "receive_json", _blocking)

        cand_task = asyncio.create_task(invite_router.invite_ws(cand, "tok-room", role="candidate"))
        await asyncio.sleep(0.02)
        assert cand.sent and cand.sent[0]["type"] == "joined"
        assert cand.sent[0]["peer_connected"] is False

        inter_task = asyncio.create_task(invite_router.invite_ws(inter, "tok-room", role="interviewer"))
        await asyncio.sleep(0.02)
        assert inter.sent and inter.sent[0]["type"] == "joined"
        # 面试官进房即知对端已在（peer_connected）；先到的候选人收到 peer-joined 事件
        assert inter.sent[0]["peer_connected"] is True
        assert any(m["type"] == "peer-joined" for m in cand.sent)

        # 候选人发送 offer -> 面试官收到（注入 from）
        await fresh.relay("tok-room", "candidate", {"type": "offer", "sdp": "v=0"})
        assert any(m.get("type") == "offer" and m.get("from") == "candidate" for m in inter.sent)

        # 收尾：取消两连接（CancelledError -> finally 清房）
        cand_task.cancel()
        inter_task.cancel()
        with suppress(Exception):
            await asyncio.gather(cand_task, inter_task, return_exceptions=True)
        assert fresh.roles("tok-room") == set()


# ------------------------------------------------------------------ token 工具
def test_random_token_url_safe() -> None:
    token = generate_random_token(24)
    assert len(token) >= 24
    assert all(c.isalnum() or c in "-_" for c in token)


# ------------------------------------------------------------------ token 查重（回归）
class _FakeScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeMintDb:
    """按调用序返回查重结果（None=该 token 空闲）。"""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt):
        value = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return _FakeScalarResult(value)


class TestMintToken:
    """回归：exists(filters={"token"}) 因 filterable 白名单缺失被静默丢弃，
    导致表非空后必然『token 生成冲突』。查重必须直查 token 列。"""

    async def test_fresh_token_first_try(self) -> None:
        db = _FakeMintDb([None])
        token = await TalentInvitationRepository().mint_token(db)
        assert token
        assert db.calls == 1

    async def test_retries_after_collision(self) -> None:
        db = _FakeMintDb([1, None])
        token = await TalentInvitationRepository().mint_token(db)
        assert token
        assert db.calls == 2

    async def test_raises_after_exhaustion(self) -> None:
        db = _FakeMintDb([1])
        with pytest.raises(RuntimeError, match="token 生成冲突"):
            await TalentInvitationRepository().mint_token(db)
        assert db.calls == 6

    def test_token_field_is_filterable(self) -> None:
        assert "token" in TalentInvitationRepository.filterable_fields
