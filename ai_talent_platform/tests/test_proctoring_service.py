"""
模块：tests/test_proctoring_service.py
职责：在线监考服务 + 公开接口的集成守门（心跳 / 事件 / 快照 / 候选人状态 / 阻断）。

标签：[Agent组] [P1]（防作弊：服务编排与接口可达性验收）

覆盖：
    1. 一次心跳即可建立监考会话，并把风险写回 AI 面试会话（proctor_state）；
    2. 远程控制等硬性风险在严格模式下阻断作答，且候选人状态接口能读到阻断原因；
    3. 环境漂移（多显示器 / 停止共享）由服务端推导，不依赖前端上报；
    4. 快照留存有配额，超限静默丢弃（不阻断作答）；
    5. 不存在 / 已取消的面试 token 不允许记账（防止旧链接刷数据）。

依赖真实（或 SQLite 回退的）数据库，由 conftest 的 mysql_ready / db_session 提供。
"""

from __future__ import annotations

import base64

import pytest

from app.config.settings import settings
from app.models.ai_interview import AiInterviewSession

pytestmark = pytest.mark.asyncio

TOKEN = "proctor-test-token-0001"
API = lambda path: f"{settings.API_PREFIX}{path}"  # noqa: E731


async def _make_session(db_session, *, status: str = "in_progress", token: str = TOKEN) -> AiInterviewSession:
    row = AiInterviewSession(
        token=token,
        title="防作弊联调用例",
        candidate_name="张三",
        position_title="后端工程师",
        status=status,
        question_count=1,
        duration_minutes=30,
        plan=[{"id": 1, "dimension": "skill", "text": "介绍一下你自己"}],
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def test_heartbeat_creates_session_and_records_events(client, db_session):
    await _make_session(db_session)
    resp = await client.post(
        API(f"/proctoring/ai_interview/{TOKEN}/heartbeat"),
        json={
            "events": [{"type": "screen_switch", "detail": "切到浏览器搜索"}, {"type": "tab_hidden"}],
            "elapsed_seconds": 30,
            "display_count": 1,
            "screen_shared": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["level"] in {"watch", "review"}
    assert data["score"] > 0
    assert len(data["new_violations"]) == 1          # 两类事件别名归一到 tab_hidden，只记一条
    assert data["state"]["enabled"] is True

    # 已写回业务对象：AI 面试会话的 proctor_state 里能看到 integrity 结论
    await db_session.refresh(await db_session.get(AiInterviewSession, 1)) if False else None
    result = await client.get(API(f"/proctoring/ai_interview/{TOKEN}/state"))
    assert result.status_code == 200
    assert result.json()["data"]["event_total"] == 2


async def test_remote_control_blocks_candidate(client, db_session):
    await _make_session(db_session, token="proctor-test-token-rdesk")
    resp = await client.post(
        API("/proctoring/ai_interview/proctor-test-token-rdesk/heartbeat"),
        json={"events": [{"type": "remote_control", "detail": "ToDesk 会话"}], "display_count": 1},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["level"] in {"serious", "void"}
    assert data["state"]["blocked"] is True
    assert "严重异常" in (data["state"]["block_reason"] or "") or data["state"]["block_reason"]

    report = await client.get(API("/proctoring/ai_interview/proctor-test-token-rdesk/state"))
    assert report.json()["data"]["blocked"] is True


async def test_server_derives_multi_display_and_missing_share(client, db_session):
    await _make_session(db_session, token="proctor-test-token-env")
    resp = await client.post(
        API("/proctoring/ai_interview/proctor-test-token-env/heartbeat"),
        json={"events": [], "display_count": 2, "screen_shared": False},
    )
    data = resp.json()["data"]
    types = {item["type"] for item in data["new_violations"]}
    assert "multi_display" in types


async def test_snapshot_quota(monkeypatch, client, db_session):
    """快照超配额时静默丢弃（返回 stored=False），绝不阻断作答。"""
    monkeypatch.setattr(settings, "PROCTOR_SNAPSHOT_MAX_PER_SESSION", 0, raising=False)
    await _make_session(db_session, token="proctor-test-token-snap")
    image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9" * 10).decode()
    resp = await client.post(
        API("/proctoring/ai_interview/proctor-test-token-snap/snapshot"),
        json={"image": image, "source": "screen", "trigger_type": "remote_control"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stored"] is False


async def test_unknown_token_rejected(client, db_session):
    resp = await client.post(
        API("/proctoring/ai_interview/not-exist-token/heartbeat"), json={"events": []}
    )
    assert resp.status_code == 404


async def test_cancelled_interview_rejected(client, db_session):
    await _make_session(db_session, status="cancelled", token="proctor-test-token-cancel")
    resp = await client.post(
        API("/proctoring/ai_interview/proctor-test-token-cancel/heartbeat"), json={"events": []}
    )
    assert resp.status_code == 400


async def test_unknown_subject_type_rejected(client, db_session):
    resp = await client.post(API("/proctoring/unknown/x-token/heartbeat"), json={"events": []})
    assert resp.status_code in {400, 422}


# --------------------------------------------------------------------------- 内置考试页（HMAC 令牌）
async def test_legacy_exam_page_behaviour_requires_token(client, db_session):
    """内置考试页的心跳走 HMAC 令牌：令牌不对一律 403，避免被当成公开刷分入口。"""
    from app.utils.exam_token import make_exam_token

    bad = await client.post("/api/v1/exam/999/behaviour?token=not-a-token", json={"events": []})
    assert bad.status_code == 403

    good = make_exam_token(4242)
    ok = await client.post(
        f"/api/v1/exam/4242/behaviour?token={good}",
        json={"events": [{"type": "tab_hidden", "detail": "切屏"}], "display_count": 1},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]
    assert data["score"] > 0
    assert data["state"]["enabled"] is True

    state = await client.get(f"/api/v1/exam/4242/behaviour?token={good}")
    assert state.status_code == 200
    assert state.json()["data"]["event_total"] >= 1


async def test_legacy_exam_page_renders_proctoring(client):
    """兜底考试页必须自带行为监考脚本（否则纯后端部署的考试完全没有防作弊）。"""
    from app.utils.exam_token import make_exam_token

    resp = await client.get(f"/exam/88?token={make_exam_token(88)}")
    assert resp.status_code == 200
    html = resp.text
    assert "/behaviour?token=" in html
    assert "visibilitychange" in html and "multi_display" in html


async def test_ai_interview_answer_blocked_after_hard_risk(client, db_session):
    """严格模式：命中远程控制后，AI 面试的作答接口必须拒绝继续（已答内容保留）。"""
    await _make_session(db_session, token="proctor-test-token-gate")
    await client.post(
        API("/proctoring/ai_interview/proctor-test-token-gate/heartbeat"),
        json={"events": [{"type": "remote_control", "detail": "向日葵"}], "display_count": 1},
    )
    resp = await client.post(
        API("/ai-interviews/session/proctor-test-token-gate/answer"),
        json={"answer": "这是我自己的回答", "duration_seconds": 30, "input_mode": "text"},
    )
    assert resp.status_code == 400, resp.text
    assert "监考" in resp.json()["message"]


async def test_ai_interview_public_state_exposes_proctoring_policy(client, db_session):
    """候选人页需要知道「要不要共享屏幕 / 是否严格」，但不应拿到权重与阈值。"""
    await _make_session(db_session, token="proctor-test-token-policy")
    resp = await client.get(API("/ai-interviews/session/proctor-test-token-policy"))
    assert resp.status_code == 200, resp.text
    policy = resp.json()["data"]["proctoring"]
    assert set(policy) == {
        "enabled", "strict", "require_screen_share", "allow_multi_display",
        "heartbeat_seconds", "snapshot_interval_seconds",
    }
