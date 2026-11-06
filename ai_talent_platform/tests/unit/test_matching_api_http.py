"""
模块：tests/unit/test_matching_api_http.py
职责：Matching Agent 新增接口的 HTTP 层契约测试（ASGI 直连，不连数据库、不连外部服务）。

为什么单独一份而不是塞进 tests/integration：
    integration 目录的夹具依赖真实 MySQL（建库建表、写角色），本地/CI 未必具备；
    本文件用 dependency_overrides 替换会话与鉴权，直接走 FastAPI 路由 → 权限依赖
    → 参数校验 → 业务服务 → 统一异常处理 的完整链路，可在无数据库环境执行。

覆盖范围（五个新增接口）：
    POST /matchings/candidates/compare
    POST /matchings/batches/{batch_id}/talents/{talent_id}/review
    GET  /matchings/reviews
    GET  /matchings/reviews/{review_id}/events
    GET  /matchings/positions/{position_id}/funnel

状态码按「接口真实行为」断言，不强行凑全：
    - 401：未携带 Token；
    - 403：employee 无 matching:read（对比/列表/漏斗），manager 有读无 review（写决策）；
    - 404：批次不存在、人才不在批次结果内；
    - 409：复核决策乐观锁版本冲突；
    - 422：请求体字段非法（talent_ids 少于 2 人、状态值不在枚举内、limit 越界）；
    - 400：所选人才全部不在批次结果中。

标签：*kwq* 新增（QA-003 接口质量收尾）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.application import create_application
from app.core.dependencies import get_current_user
from app.core.exceptions import DataConflictError, NotFoundError
from app.core.response import PageResult
from app.infrastructure.database.session import get_db
from app.routers import matching_router as mr
from app.schemas.matching_review import (
    MatchingReviewDecideResponse,
    MatchingReviewItem,
    ReviewFunnelResponse,
)
from app.services.matching_service import MatchingService

BATCH_ID = "0123456789abcdef0123456789abcdef"
OTHER_BATCH = "f" * 32
API = "/api/v1/matchings"


# --------------------------------------------------------------------------- 夹具
@pytest.fixture()
def app():
    """每个用例一个干净 app：依赖覆盖互不串味（中间件状态也重置）。"""
    application = create_application()

    async def _fake_db():
        # 真正的数据库不会被触碰：鉴权与业务服务都已被替换
        yield None

    application.dependency_overrides[get_db] = _fake_db
    yield application
    application.dependency_overrides.clear()


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def login(app, roles: list[str], *, user_id: int = 101) -> SimpleNamespace:
    """模拟已登录用户（只替换 get_current_user，权限依赖照常执行）。"""
    user = SimpleNamespace(id=user_id, role_codes=roles, is_superuser=False, is_active=True)

    async def _current():
        return user

    app.dependency_overrides[get_current_user] = _current
    return user


# ----------------------------------------------------------------- 匹配快照替身
def _item(talent_id: int, rank: int, score: float, *, gaps: list[dict] | None = None) -> dict:
    return {
        "talent_id": talent_id,
        "talent_name": f"候选人{talent_id}",
        "rank": rank,
        "decision": "PASS",
        "score": score,
        "dimension_scores": {"skill": score, "experience": 70.0, "education": 80.0, "quality": 60.0},
        "defaulted_dimensions": [],
        "data_completeness": 0.8,
        "matched_skills": ["Python"],
        "highlights": ["技能匹配"],
        "gaps": ["缺少 K8s"],
        "hard_results": [],
        "suggestion": "建议初面",
        "explanation_source": "rule_fallback",
        "gap_matrix": gaps or [],
    }


def _snapshot(items: list[dict], *, strategy: str = "hybrid", batch_id: str = BATCH_ID):
    return SimpleNamespace(
        batch_id=batch_id,
        position_id=7,
        request_scope={"strategy": strategy, "top_k": 20, "min_score": 0},
        response={
            "batch_id": batch_id,
            "position_id": 7,
            "position_title": "高级后端工程师",
            "requirement_version": 3,
            "recall_mode": "sql_fallback",
            "matched_at": "2026-11-05T10:00:00",
            "stats": {
                "candidates": len(items), "excluded_by_hard": 0,
                "unknown": 0, "needs_review": 0, "output": len(items),
            },
            "items": items,
            "excluded": [],
        },
    )


class _SnapshotRepo:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    async def snapshot_by_id(self, db, batch_id: str):
        if self._snapshot is None or self._snapshot.batch_id != batch_id:
            return None
        return self._snapshot


class _ReviewRepoStub:
    """复核仓储替身：严格按 batch_id 过滤，用来验证批次隔离。"""

    def __init__(self, records: list | None = None):
        self._records = records or []

    async def list_by_batch_talents(self, db, *, batch_id: str, talent_ids: list[int]):
        keep = {int(t) for t in talent_ids}
        return [
            r for r in self._records
            if int(r.talent_id) in keep and r.batch_id == batch_id
        ]


def _record(talent_id: int, *, batch_id: str = BATCH_ID, status: str = "interviewing"):
    return SimpleNamespace(
        id=900 + talent_id, batch_id=batch_id, position_id=7, talent_id=talent_id,
        review_status=status, reason_code=None, note="初面通过", decided_by=101,
        decided_at=None, version=3,
    )


def use_compare_service(app, snapshot, reviews: list | None = None) -> None:
    """把 /candidates/compare 的服务替换为「真服务 + 假仓储」，保留真实业务判定。"""
    service = MatchingService(
        db=None,
        repo=_SnapshotRepo(snapshot),
        review_repo=_ReviewRepoStub(reviews),
    )

    async def _get_service():
        return service

    app.dependency_overrides[mr.get_service] = _get_service


def use_review_service(app, service) -> None:
    async def _get_service():
        return service

    app.dependency_overrides[mr.get_review_service] = _get_service


def _review_item(status: str = "shortlisted") -> MatchingReviewItem:
    return MatchingReviewItem(
        id=9, batch_id=BATCH_ID, position_id=7, talent_id=11,
        review_status=status, status_label="入围", version=2,
    )


class _FakeReviewService:
    """只用于验证 HTTP 契约（业务规则由 test_matching_review.py 的 30 例覆盖）。"""

    def __init__(self, *, error: Exception | None = None, changed: bool = True):
        self.error = error
        self.changed = changed
        self.calls: list[dict] = []

    async def decide(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return MatchingReviewDecideResponse(
            review=_review_item(), event=None, changed=self.changed, message="已更新"
        )

    async def page_reviews(self, page, **kwargs):
        self.calls.append({"page": page, **kwargs})
        return PageResult(items=[], total=0, page=1, page_size=20, pages=0)

    async def list_events(self, **kwargs):
        self.calls.append(kwargs)
        return []

    async def funnel(self, **kwargs):
        self.calls.append(kwargs)
        return ReviewFunnelResponse(
            position_id=int(kwargs["position_id"]), total=0, stages=[],
            converted=0, hired_rate=0.0, caliber_note="统计当前状态，非历史累计",
        )


# ============================================================================
# 一、POST /candidates/compare
# ============================================================================
async def test_compare_ok_returns_snapshot_values(client, app):
    """正常请求：数值原样来自快照，不做二次计算。"""
    login(app, ["hr"])
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    use_compare_service(app, _snapshot(items))

    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert [row["talent_id"] for row in body["rows"]] == [11, 12]
    assert [row["score"] for row in body["rows"]] == [91.5, 88.0]
    assert body["rows"][0]["dimension_scores"]["skill"] == 91.5
    # 未建档的候选人 review 必须为 null，不能伪造成「待复核」
    assert body["rows"][0]["review"] is None


async def test_compare_unauthenticated_returns_401(client, app):
    use_compare_service(app, _snapshot([_item(11, 1, 90.0), _item(12, 2, 80.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


async def test_compare_employee_without_permission_returns_403(client, app):
    """employee 没有 matching:read，连读对比都不允许。"""
    login(app, ["employee"])
    use_compare_service(app, _snapshot([_item(11, 1, 90.0), _item(12, 2, 80.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    assert resp.status_code == 403
    assert resp.json()["code"] == 2004


async def test_compare_unknown_batch_returns_404(client, app):
    login(app, ["hr"])
    use_compare_service(app, None)
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    assert resp.status_code == 404
    assert "批次不存在" in resp.json()["message"]


async def test_compare_single_talent_rejected_with_422(client, app):
    """对比至少两人：1 人属于参数错误，应在入口拦下而不是等到服务层。"""
    login(app, ["hr"])
    use_compare_service(app, _snapshot([_item(11, 1, 90.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11]})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


async def test_compare_nine_talents_rejected_with_422(client, app):
    login(app, ["hr"])
    use_compare_service(app, _snapshot([_item(i, i, 90.0) for i in range(1, 10)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": list(range(1, 10))})
    assert resp.status_code == 422


async def test_compare_all_talents_missing_returns_400(client, app):
    """所选人才全不在批次内 → 业务规则错误（400），而不是 500 或空列表。"""
    login(app, ["hr"])
    use_compare_service(app, _snapshot([_item(11, 1, 90.0), _item(12, 2, 80.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [91, 92]})
    assert resp.status_code == 400
    assert "均不在该批次结果中" in resp.json()["message"]


async def test_compare_missing_talent_listed_in_notes(client, app):
    """部分缺失不报错：能比的人照常返回，缺的人进 notes 点名。"""
    login(app, ["hr"])
    use_compare_service(app, _snapshot([_item(11, 1, 90.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 99]})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert [row["talent_id"] for row in body["rows"]] == [11]
    assert any("99" in note for note in body["notes"])


async def test_compare_review_isolated_by_batch(client, app):
    """复核状态按批次隔离：别的批次的决策不会串到本次对比。"""
    login(app, ["hr"])
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    foreign = _record(11, batch_id=OTHER_BATCH, status="hired")
    use_compare_service(app, _snapshot(items), reviews=[foreign])

    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    assert resp.status_code == 200
    assert all(row["review"] is None for row in resp.json()["data"]["rows"])


async def test_compare_returns_review_status_of_same_batch(client, app):
    """同批次复核状态随行返回，且状态中文名来自统一映射。"""
    login(app, ["hr"])
    items = [_item(11, 1, 91.5), _item(12, 2, 88.0)]
    use_compare_service(app, _snapshot(items), reviews=[_record(11)])

    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    rows = {row["talent_id"]: row for row in resp.json()["data"]["rows"]}
    assert rows[11]["review"]["review_status"] == "interviewing"
    assert rows[11]["review"]["status_label"] == "面试中"
    assert rows[12]["review"] is None


async def test_compare_global_manager_can_read(client, app):
    """manager 有 matching:read：可以看对比，但不能做复核决策（下一个用例验证）。"""
    login(app, ["manager"])
    use_compare_service(app, _snapshot([_item(11, 1, 90.0), _item(12, 2, 80.0)]))
    resp = await client.post(f"{API}/candidates/compare",
                             json={"batch_id": BATCH_ID, "talent_ids": [11, 12]})
    assert resp.status_code == 200


# ============================================================================
# 二、POST /batches/{batch_id}/talents/{talent_id}/review
# ============================================================================
REVIEW_URL = f"{API}/batches/{BATCH_ID}/talents/11/review"


async def test_review_decide_ok_as_hr(client, app):
    login(app, ["hr"], user_id=77)
    service = _FakeReviewService()
    use_review_service(app, service)

    resp = await client.post(REVIEW_URL, json={"status": "shortlisted"})

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["changed"] is True
    assert body["review"]["review_status"] == "shortlisted"
    # 操作人取自登录态，不能由前端传入
    assert service.calls[0]["operator_id"] == 77


async def test_review_unauthenticated_returns_401(client, app):
    use_review_service(app, _FakeReviewService())
    resp = await client.post(REVIEW_URL, json={"status": "shortlisted"})
    assert resp.status_code == 401


async def test_review_manager_denied_403(client, app):
    """manager 只有 matching:read：能看结果，无权决定谁被淘汰/录用。"""
    login(app, ["manager"])
    use_review_service(app, _FakeReviewService())
    resp = await client.post(REVIEW_URL, json={"status": "shortlisted"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 2004


async def test_review_employee_denied_403(client, app):
    login(app, ["employee"])
    use_review_service(app, _FakeReviewService())
    resp = await client.post(REVIEW_URL, json={"status": "shortlisted"})
    assert resp.status_code == 403


async def test_review_version_conflict_returns_409(client, app):
    """两人同时操作：后提交者拿到 409，而不是静默覆盖。"""
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService(error=DataConflictError("版本冲突")))
    resp = await client.post(REVIEW_URL,
                             json={"status": "shortlisted", "expected_version": 1})
    assert resp.status_code == 409
    assert resp.json()["code"] == 3003


async def test_review_talent_not_in_batch_returns_404(client, app):
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService(error=NotFoundError("人才不在批次中")))
    resp = await client.post(REVIEW_URL, json={"status": "shortlisted"})
    assert resp.status_code == 404


async def test_review_invalid_status_returns_422(client, app):
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService())
    resp = await client.post(REVIEW_URL, json={"status": "approved"})
    assert resp.status_code == 422
    assert resp.json()["data"]["errors"][0]["field"] == "status"


async def test_review_expected_version_must_be_positive(client, app):
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService())
    resp = await client.post(REVIEW_URL,
                             json={"status": "shortlisted", "expected_version": 0})
    assert resp.status_code == 422


# ============================================================================
# 三、GET /reviews
# ============================================================================
async def test_list_reviews_ok_and_filters_passed(client, app):
    login(app, ["hr"])
    service = _FakeReviewService()
    use_review_service(app, service)

    resp = await client.get(f"{API}/reviews", params={"review_status": "hired", "page": 2})

    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 0
    assert service.calls[0]["review_status"] == "hired"
    assert service.calls[0]["page"].page == 2


async def test_list_reviews_invalid_status_returns_422(client, app):
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService())
    resp = await client.get(f"{API}/reviews", params={"review_status": "unknown"})
    assert resp.status_code == 422


async def test_list_reviews_employee_denied_403(client, app):
    login(app, ["employee"])
    use_review_service(app, _FakeReviewService())
    resp = await client.get(f"{API}/reviews")
    assert resp.status_code == 403


# ============================================================================
# 四、GET /reviews/{review_id}/events
# ============================================================================
async def test_list_events_ok(client, app):
    login(app, ["hr"])
    service = _FakeReviewService()
    use_review_service(app, service)
    resp = await client.get(f"{API}/reviews/9/events", params={"limit": 50})
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert service.calls[0]["review_id"] == 9 and service.calls[0]["limit"] == 50


async def test_list_events_limit_out_of_range_returns_422(client, app):
    login(app, ["hr"])
    use_review_service(app, _FakeReviewService())
    resp = await client.get(f"{API}/reviews/9/events", params={"limit": 500})
    assert resp.status_code == 422


async def test_list_events_manager_can_read_403_for_employee(client, app):
    """流水是只读审计：manager 可读；employee 无 matching:read。"""
    login(app, ["manager"])
    use_review_service(app, _FakeReviewService())
    assert (await client.get(f"{API}/reviews/9/events")).status_code == 200
    login(app, ["employee"])
    assert (await client.get(f"{API}/reviews/9/events")).status_code == 403


# ============================================================================
# 五、GET /positions/{position_id}/funnel
# ============================================================================
async def test_funnel_ok_with_caliber_note(client, app):
    login(app, ["hr"])
    service = _FakeReviewService()
    use_review_service(app, service)
    resp = await client.get(f"{API}/positions/7/funnel")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["position_id"] == 7
    assert body["caliber_note"]
    assert service.calls[0]["position_id"] == 7


async def test_funnel_employee_denied_403(client, app):
    login(app, ["employee"])
    use_review_service(app, _FakeReviewService())
    resp = await client.get(f"{API}/positions/7/funnel")
    assert resp.status_code == 403


async def test_funnel_unauthenticated_returns_401(client, app):
    use_review_service(app, _FakeReviewService())
    resp = await client.get(f"{API}/positions/7/funnel")
    assert resp.status_code == 401
