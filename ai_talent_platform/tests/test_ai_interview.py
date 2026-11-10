"""
模块：tests/test_ai_interview.py
职责：AI 数字人面试验收用例（引擎纯函数 + 数据库/接口全链路）。

标签：[Agent组] [P1]（AI 面试：离线可测的规则引擎 + 真实 HTTP 链路）

覆盖：
1. 人设归一 / 问题计划结构 / 中英文计划；
2. 回答评分可解释性（空答、短答上限、证据与关键词信号）；
3. 追问触发条件与追问文本；
4. 综合评估的完成率口径（未作答不按 0 分拉低、样本不足标记）；
5. 服务层创建 -> 开始 -> 逐轮作答 -> 自动出报告；
6. 接口权限（无权限 403）、公开 token 访问、错误 token 404；
7. 链接过期校验。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from datetime import datetime, timedelta

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.security import create_access_token
from app.models.ai_interview import AiInterviewSession
from app.models.role import Role
from app.models.talent import Talent
from app.models.user import User
from app.schemas.ai_interview import AiInterviewCreateRequest
from app.services import ai_interview_engine as engine
from app.services.ai_interview_service import AiInterviewService

API = "/api/v1"

PROFILE = {
    "name": "张伟",
    "work_years": 5,
    "education": "本科",
    "current_company": "星云科技",
    "current_position": "后端工程师",
    "skills": ["Python", "FastAPI", "MySQL", "Redis"],
}
POSITION = {"title": "高级后端工程师", "level": "senior", "skills": ["Python", "FastAPI"]}


# ==================================================================== 引擎
def test_persona_resolution_and_defaults():
    assert engine.resolve_persona("tech") == "tech_lead"
    assert engine.resolve_persona("不存在的人设") == "friendly_hr"
    profile = engine.persona_profile("global")
    assert profile["key"] == "global_interviewer"
    assert profile["language"] == "en-US"
    assert profile["name"] == "Ava"


def test_build_plan_structure_and_language():
    plan = engine.build_interview_plan(PROFILE, POSITION, count=6, difficulty="senior", language="zh-CN")
    assert len(plan) == 6
    assert [item["id"] for item in plan] == [1, 2, 3, 4, 5, 6]
    assert all(item["text"] and item["dimension"] in engine.DIMENSIONS for item in plan)
    assert all(item["keywords"] for item in plan)
    assert any(item["follow_up"] for item in plan)
    assert len({item["text"] for item in plan}) == len(plan)

    en_plan = engine.build_interview_plan(PROFILE, POSITION, count=4, language="en-US")
    assert len(en_plan) == 4
    assert "Tell me" in en_plan[1]["text"] or "Tell me" in en_plan[0]["text"]

    # 数量夹到 [3, 10]
    assert len(engine.build_interview_plan(PROFILE, POSITION, count=1)) == 3
    assert len(engine.build_interview_plan(PROFILE, POSITION, count=99)) == 10


def test_score_answer_is_explainable():
    question = {
        "text": "请介绍一个你负责的项目，说明目标、行动与结果。",
        "dimension": "professional",
        "keywords": ["项目", "负责", "结果", "指标", "挑战"],
        "follow_up": "最困难的决定是什么？",
    }
    empty = engine.score_answer(question, "")
    assert empty.score == 0.0
    assert empty.signals["char_count"] == 0

    short = engine.score_answer(question, "做过。")
    assert short.score <= 35
    assert short.signals["missing_keywords"]

    detailed = engine.score_answer(
        question,
        "我负责订单系统重构，首先梳理核心链路，然后把接口拆成三个服务，"
        "最终把响应时间从 800ms 降到 220ms，错误率下降 60%，支撑了大促期间 3 倍流量。",
        duration_seconds=90,
    )
    assert detailed.score >= 60
    assert "项目" in detailed.signals["keywords_hit"] or "负责" in detailed.signals["keywords_hit"]
    assert detailed.signals["numbers"]
    assert detailed.signals["structure_hits"]
    assert "背景" in detailed.feedback or "结构" in detailed.feedback or "支撑" in detailed.feedback


def test_follow_up_policy():
    question = {"dimension": "professional", "keywords": ["项目", "结果"], "follow_up": "请补充量化结果。"}
    assert engine.should_follow_up(question, "做过一个项目。", 30) is True
    strong = (
        "我负责的项目是订单中台重构，首先拆分了读写链路，然后引入缓存与异步化，"
        "最后把平均延迟从 900ms 降到 210ms，错误率降低 65%，并支撑了三倍流量。"
    )
    assert engine.should_follow_up(question, strong, 82) is False
    assert engine.follow_up_text({"dimension": "potential", "follow_up": ""}) 
    assert engine.follow_up_text({"dimension": "communication", "follow_up": ""}, "en-US").startswith("Could you")


def test_evaluate_session_completion_and_missing_answers():
    plan = engine.build_interview_plan(PROFILE, POSITION, count=4)

    class Turn:
        def __init__(self, index, content):
            self.role = "candidate"
            self.question_index = index
            self.content = content
            self.kind = "answer"
            self.input_mode = "voice"
            self.duration_seconds = 60

    turns = [
        Turn(0, "我有五年后端经验，主要负责订单与支付系统，擅长 Python 和 FastAPI，做过高并发改造。"),
        Turn(1, "我负责订单系统重构，首先梳理链路，最后把延迟从 800ms 降到 220ms，错误率下降 60%。"),
    ]
    report = engine.evaluate_session(plan, turns)
    assert report["question_count"] == 4
    assert report["answered_count"] == 2
    assert report["completion_rate"] == 0.5
    assert report["total_score"] > 0
    assert report["per_question"][2]["score"] is None
    assert report["per_question"][2]["feedback"] == "未作答。"
    # 未作答维度返回 None，而不是伪造 0 分
    assert any(value is None for value in report["dimension_scores"].values())

    empty_report = engine.evaluate_session(plan, [])
    assert empty_report["total_score"] == 0
    assert empty_report["level"] == "未评估"
    assert empty_report["recommendation"] == "insufficient"
    assert "未作答" in empty_report["summary"]


def test_level_and_recommendation_thresholds():
    assert engine.level_for(92) == "卓越"
    assert engine.level_for(72) == "良好"
    assert engine.level_for(50) == "待提升"
    assert engine.level_for(90, completion_rate=0.3) == "样本不足"
    assert engine.recommendation_for(86) == "strongly_recommend"
    assert engine.recommendation_for(70) == "consider"
    assert engine.recommendation_for(20) == "not_recommend"


# ==================================================================== 服务 / 接口
async def _make_user(db, *, role_code: str | None = None, superuser: bool = False) -> tuple[User, str]:
    user = User(
        username=f"ai-{uuid.uuid4().hex[:10]}",
        password_hash="x",
        real_name="AI 面试测试用户",
        is_active=True,
        is_superuser=superuser,
    )
    db.add(user)
    await db.flush()
    if role_code:
        role = (await db.execute(select(Role).where(Role.code == role_code))).scalar_one()
        await db.flush()
        await db.refresh(user, attribute_names=["roles"])
        user.roles = [role]
    await db.commit()
    return user, create_access_token(user.id)


async def _make_talent(db) -> Talent:
    talent = Talent(
        name=f"候选人{uuid.uuid4().hex[:4]}",
        work_years=5,
        education="本科",
        current_company="星云科技",
        current_position="后端工程师",
        skill_tags=["Python", "FastAPI", "MySQL", "Redis"],
    )
    db.add(talent)
    await db.commit()
    return talent


STRONG_ANSWERS = [
    "你好，我有五年后端开发经验，主要在电商订单与支付方向，最擅长 Python 和 FastAPI，"
    "做过高并发系统重构，也带过三人小组，希望在更有挑战的业务里继续成长。",
    "我负责订单中台重构，首先梳理核心链路，然后拆分读写服务并引入缓存，"
    "最终把平均延迟从 800ms 降到 220ms，错误率下降 60%，支撑了大促 3 倍流量。",
    "我对 FastAPI 比较熟悉，做过统一鉴权与限流，方案是 Redis 加滑动窗口，"
    "最后把接口超时率降到 1% 以内，并沉淀了可复用的中间件。",
    "如果核心指标低于预期，我会先核对数据口径，再按链路拆解定位，"
    "用假设验证排优先级，最后复盘并补齐监控告警。",
    "未来一年我希望在架构设计和跨团队协作上成长，已经在做项目复盘和技术分享，"
    "也在系统学习领域驱动设计。",
    "我选择这个岗位，一是业务方向匹配，二是团队技术栈一致，三是我能独立负责核心模块并推动落地。",
    "如果同时有三件紧急任务，我会按影响面和不可逆程度排序，并第一时间同步相关方，"
    "优先保证核心链路稳定。",
    "我经历过一次上线延期，复盘发现是风险评估不足，之后我养成了提前做演练和回滚预案的习惯。",
]


async def _drive_to_completion(service: AiInterviewService, token: str) -> dict:
    state = await service.start(token)
    assert state["status"] == "in_progress"
    answer_index = 0
    for _ in range(40):
        if state["status"] == "completed":
            return state
        answer = STRONG_ANSWERS[min(answer_index, len(STRONG_ANSWERS) - 1)]
        answer_index += 1
        result = await service.answer(token, answer=answer, duration_seconds=80, input_mode="voice")
        state = result["state"]
        if result["completed"]:
            return state
    raise AssertionError("面试未在预期轮次内结束")


@pytest.mark.asyncio
async def test_service_full_flow(db_session):
    user, _ = await _make_user(db_session, superuser=True)
    talent = await _make_talent(db_session)
    service = AiInterviewService(db_session)
    created = await service.create(
        AiInterviewCreateRequest(
            talent_id=talent.id,
            question_count=5,
            duration_minutes=30,
            send_email=False,
        ),
        user,
    )
    assert created["status"] == "pending"
    assert created["question_count"] == 5
    assert created["url"].endswith(f"/ai-interview/{created['token']}")
    assert created["interviewer"]["name"]

    state = await service.public_state(created["token"])
    assert state["status"] == "pending"
    assert state["progress"]["total"] == 5
    assert state["current_question"]["text"]

    final_state = await _drive_to_completion(service, created["token"])
    assert final_state["status"] == "completed"
    result = final_state["result"]
    assert 0 <= result["total_score"] <= 100
    assert result["level"]
    assert result["summary"]
    assert result["answered_count"] >= 1
    assert result["per_question"]

    detail = await service.detail(created["id"])
    assert detail["status"] == "completed"
    assert len(detail["turns"]) >= 3
    assert detail["turns"][0]["kind"] == "greeting"
    assert any(turn["role"] == "candidate" for turn in detail["turns"])
    assert detail["report"]["scoring_method"].startswith("rule")

    report = await service.report(created["id"])
    assert report["session_id"] == created["id"]
    assert report["total_score"] == result["total_score"]


@pytest.mark.asyncio
async def test_service_expired_and_invalid_token(db_session):
    user, _ = await _make_user(db_session, superuser=True)
    talent = await _make_talent(db_session)
    service = AiInterviewService(db_session)
    created = await service.create(
        AiInterviewCreateRequest(talent_id=talent.id, question_count=3, send_email=False), user
    )
    row = (await db_session.execute(
        select(AiInterviewSession).where(AiInterviewSession.id == created["id"])
    )).scalar_one()
    row.expires_at = datetime.now() - timedelta(minutes=1)
    await db_session.commit()

    with pytest.raises(BusinessRuleError):
        await service.public_state(created["token"])
    with pytest.raises(NotFoundError):
        await service.public_state("not-a-real-token-123456")


@pytest.mark.asyncio
async def test_api_permissions_and_public_flow(client, db_session):
    admin, admin_token = await _make_user(db_session, superuser=True)
    _, employee_token = await _make_user(db_session, role_code="employee")
    talent = await _make_talent(db_session)

    # 无权限：employee 没有 ai_interview:read / manage
    resp = await client.get(f"{API}/ai-interviews", headers={"Authorization": f"Bearer {employee_token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 2004

    # 配置接口（需登录 + 读权限）
    resp = await client.get(f"{API}/ai-interviews/config", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert {item["key"] for item in resp.json()["data"]["personas"]} >= {"friendly_hr", "tech_lead"}

    # 创建
    resp = await client.post(
        f"{API}/ai-interviews",
        json={"talent_id": talent.id, "question_count": 4, "send_email": False, "title": "接口测试 AI 面试"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["status"] == "pending"
    token = body["token"]
    session_id = body["id"]

    # 列表 / 详情
    resp = await client.get(f"{API}/ai-interviews", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] >= 1

    resp = await client.get(f"{API}/ai-interviews/{session_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["plan"]

    # 公开：状态 -> 开始 -> 作答 -> 结束
    resp = await client.get(f"{API}/ai-interviews/session/{token}")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "pending"

    resp = await client.post(f"{API}/ai-interviews/session/{token}/start")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "in_progress"

    resp = await client.post(
        f"{API}/ai-interviews/session/{token}/answer",
        json={"answer": STRONG_ANSWERS[0], "duration_seconds": 60, "input_mode": "voice"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"] is True

    resp = await client.post(f"{API}/ai-interviews/session/{token}/finish")
    assert resp.status_code == 200
    done = resp.json()["data"]
    assert done["status"] == "completed"
    assert done["result"]["summary"]

    # 报告（host 权限）
    resp = await client.get(
        f"{API}/ai-interviews/{session_id}/report", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total_score"] == done["result"]["total_score"]

    # 员工仍然无权读取报告
    resp = await client.get(
        f"{API}/ai-interviews/{session_id}/report", headers={"Authorization": f"Bearer {employee_token}"}
    )
    assert resp.status_code == 403


def test_candidate_result_hides_hr_only_content():
    """候选人可见结果：不暴露录用建议，summary 改为第二人称，建议过滤招聘决策语句。"""
    from types import SimpleNamespace

    row = SimpleNamespace(report={
        "total_score": 88.0,
        "level": "优秀",
        "recommendation": "strongly_recommend",
        "recommendation_label": "强烈推荐：建议优先进入下一轮",
        "dimension_scores": {"professional": 90.0},
        "dimension_labels": engine.DIMENSION_LABELS,
        "answered_count": 5,
        "question_count": 5,
        "completion_rate": 1.0,
        "summary": "候选人完成 5/5 道题，综合得分 88 分（优秀）。强烈推荐：建议优先进入下一轮。",
        "strengths": ["专业基础扎实"],
        "weaknesses": [],
        "suggestions": ["整体表现良好，建议进入下一轮，并在背景调查中重点核验关键经历", "继续补充可量化的业务结果"],
        "per_question": [],
        "scoring_method": "rule",
    })
    result = AiInterviewService(None)._candidate_result(row)
    assert result is not None
    assert "recommendation" not in result and "recommendation_label" not in result
    assert "强烈推荐" not in result["summary"]
    assert result["summary"].startswith("你完成 5/5 道题")
    assert result["suggestions"] == ["继续补充可量化的业务结果"]


@pytest.mark.asyncio
async def test_interview_invitation_can_choose_ai_digital_human(client, db_session):
    """面试邀约选择 AI 数字人：同一次创建同时落邀约与 AI 会话，链接直达数字人面试页。"""
    _, admin_token = await _make_user(db_session, superuser=True)
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(
        f"{API}/invites",
        headers=headers,
        json={
            "type": "ai_interview",
            "talent_name": "邀约候选人",
            "email": "invite-ai@example.com",
            "channels": ["email"],
            "question_count": 3,
            "duration_minutes": 15,
            "expires_hours": 24,
            "interviewer_persona": "tech_lead",
            "title": "邀约候选人 · AI 数字人面试",
        },
    )
    assert resp.status_code == 200, resp.text
    invite = resp.json()["data"]
    assert invite["type"] == "ai_interview"
    assert invite["question_count"] == 3
    assert invite["duration_minutes"] == 15
    assert invite["url"].endswith(f"/ai-interview/{invite['token']}")

    row = (await db_session.execute(
        select(AiInterviewSession).where(AiInterviewSession.token == invite["token"])
    )).scalar_one()
    assert row.invitation_id == invite["id"]
    assert row.interviewer_persona == "tech_lead"
    assert row.status == "pending"

    # 候选人公开页可直接拿到 AI 面试状态，无需再走 /invite 落地页
    state = await client.get(f"{API}/ai-interviews/session/{invite['token']}")
    assert state.status_code == 200, state.text
    assert state.json()["data"]["status"] == "pending"
    assert state.json()["data"]["progress"]["total"] == 3

    # 开始/结束 AI 面试时，邀约记录状态同步流转（不是两份互不相干的数据）
    started = await client.post(f"{API}/ai-interviews/session/{invite['token']}/start")
    assert started.status_code == 200, started.text
    listing = await client.get(f"{API}/invites", headers=headers, params={"type": "ai_interview"})
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    target = next(item for item in items if item["id"] == invite["id"])
    assert "/ai-interview/" in target["url"]
    assert target["status"] == "opened"

    answered = await client.post(
        f"{API}/ai-interviews/session/{invite['token']}/answer",
        json={"answer": STRONG_ANSWERS[0], "duration_seconds": 60, "input_mode": "text"},
    )
    assert answered.status_code == 200, answered.text
    finished = await client.post(f"{API}/ai-interviews/session/{invite['token']}/finish")
    assert finished.status_code == 200, finished.text
    listing = await client.get(f"{API}/invites", headers=headers, params={"type": "ai_interview"})
    target = next(item for item in listing.json()["data"]["items"] if item["id"] == invite["id"])
    assert target["status"] == "finished"
