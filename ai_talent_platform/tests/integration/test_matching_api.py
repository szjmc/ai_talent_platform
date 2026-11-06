"""
文件：tests/integration/test_matching_api.py
职责：匹配接口层测试（真实 HTTP 栈：路由 + 依赖 + 权限 + 异常处理 + 持久化）。

覆盖（对应任务书「API 测试」要求）：
1. 正常请求：执行匹配 -> 返回 batch_id -> 查询最新结果；
2. 非法请求 / 缺失字段：422；
3. 权限异常：未登录 401、无权限角色 403；
4. 业务异常：岗位不存在 404、画像未确认 400；
5. 返回结构：统一 {code,message,data,trace_id}；
6. 持久化结果：接口返回的批次能在库中查到。

标签：[Agent组] [P2]（匹配接口集成用例）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token
from app.models.matching import MatchingRecord
from app.models.role import Role
from app.models.user import User

API = "/api/v1"


async def _make_user(db, *, role_code: str | None = None, superuser: bool = False) -> tuple[User, str]:
    """创建真实用户并签发访问令牌（与线上鉴权链路一致）。"""
    user = User(
        username=f"u-{uuid.uuid4().hex[:10]}",
        password_hash="x",
        real_name="接口测试用户",
        is_active=True,
        is_superuser=superuser,
    )
    db.add(user)
    await db.flush()
    if role_code:
        role = (await db.execute(select(Role).where(Role.code == role_code))).scalar_one()
        # 先 flush 拿到主键，再显式 refresh 加载空角色集合，最后整体赋值。
        # 依据：User.roles 是 lazy="selectin"，异步会话里直接 append/赋值会触发隐式懒加载
        #      并抛 MissingGreenlet（UserRepository.set_roles 的同一处理方式）。
        await db.flush()
        await db.refresh(user, attribute_names=["roles"])
        user.roles = [role]
    await db.commit()
    return user, create_access_token(user.id)


@pytest.fixture
async def admin_token(db_session) -> str:
    _, token = await _make_user(db_session, superuser=True)
    return token


@pytest.fixture
async def employee_token(db_session) -> str:
    _, token = await _make_user(db_session, role_code="employee")
    return token


class TestAuthAndPermission:
    async def test_run_without_token_returns_401(self, client, seed_data):
        resp = await client.post(f"{API}/matchings/run", json={"position_id": seed_data.position_id})

        assert resp.status_code == 401
        assert resp.json()["code"] == 2001

    async def test_run_without_permission_returns_403(self, client, seed_data, employee_token):
        resp = await client.post(
            f"{API}/matchings/run",
            json={"position_id": seed_data.position_id},
            headers={"Authorization": f"Bearer {employee_token}"},
        )

        assert resp.status_code == 403
        assert resp.json()["code"] == 2004


class TestRunEndpoint:
    async def test_run_success_and_persisted(self, client, db_session, seed_data, admin_token):
        payload = {
            "position_id": seed_data.position_id,
            "talent_ids": list(seed_data.talent_ids.values()),
            "top_k": 5,
            "min_score": 0,
            "strategy": "hybrid",
            "with_explanation": True,
        }
        resp = await client.post(
            f"{API}/matchings/run", json=payload, headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200
        assert body["trace_id"]
        data = body["data"]
        assert data["batch_id"]
        assert data["position_id"] == seed_data.position_id
        assert data["position_title"] == "后端开发工程师"
        assert data["requirement_version"] == seed_data.profile_version
        assert data["recall_mode"] == "sql_fallback"
        assert data["stats"]["candidates"] == 5
        assert data["items"]

        first = data["items"][0]
        assert 0 <= first["score"] <= 100
        assert first["hard_results"]
        assert first["explanation_source"] in {"dify", "llm_fallback", "rule_fallback"}

        # 持久化：接口返回的批次真实落库
        stmt = (
            select(func.count())
            .select_from(MatchingRecord)
            .where(MatchingRecord.batch_id == data["batch_id"])
        )
        assert int((await db_session.execute(stmt)).scalar() or 0) == len(data["items"])

    async def test_invalid_payload_returns_422(self, client, admin_token):
        resp = await client.post(
            f"{API}/matchings/run",
            json={"position_id": 0, "top_k": 9999},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    async def test_missing_field_returns_422(self, client, admin_token):
        resp = await client.post(
            f"{API}/matchings/run", json={}, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert resp.status_code == 422

    async def test_unknown_position_returns_404(self, client, admin_token):
        resp = await client.post(
            f"{API}/matchings/run",
            json={"position_id": 987654321},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    async def test_unconfirmed_profile_returns_400(self, client, admin_token, unconfirmed_position):
        resp = await client.post(
            f"{API}/matchings/run",
            json={"position_id": unconfirmed_position},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400
        assert "画像" in resp.json()["message"]


class TestQueryEndpoints:
    async def test_latest_by_position(self, client, seed_data, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        run = await client.post(
            f"{API}/matchings/run",
            json={"position_id": seed_data.position_id, "talent_ids": list(seed_data.talent_ids.values())},
            headers=headers,
        )
        batch_id = run.json()["data"]["batch_id"]

        resp = await client.get(f"{API}/matchings/positions/{seed_data.position_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["batch_id"] == batch_id
        assert len(data["items"]) == len(run.json()["data"]["items"])

    async def test_latest_without_records_returns_404(self, client, seed_data, admin_token):
        resp = await client.get(
            f"{API}/matchings/positions/{seed_data.position_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    async def test_history_by_talent(self, client, seed_data, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        run = await client.post(
            f"{API}/matchings/run",
            json={"position_id": seed_data.position_id, "talent_ids": list(seed_data.talent_ids.values())},
            headers=headers,
        )
        talent_id = run.json()["data"]["items"][0]["talent_id"]

        resp = await client.get(f"{API}/matchings/talents/{talent_id}", headers=headers)
        assert resp.status_code == 200
        history = resp.json()["data"]
        assert history
        assert history[0]["position_title"] == "后端开发工程师"

    async def test_list_matchings_page_structure(self, client, seed_data, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        await client.post(
            f"{API}/matchings/run",
            json={"position_id": seed_data.position_id, "talent_ids": list(seed_data.talent_ids.values())},
            headers=headers,
        )

        resp = await client.get(f"{API}/matchings?page=1&page_size=5", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert set(data) >= {"items", "total", "page", "page_size", "pages"}
        assert data["total"] >= 1
        assert len(data["items"]) <= 5
        # *kwq* 新增（MATCH-016）：列表必须带姓名字段，否则 HR 无法区分同一岗位的多条记录
        assert all(item.get("talent_name") for item in data["items"])
        assert all(item.get("talent_id") for item in data["items"])

    # *kwq* 新增（MATCH-021）：过滤参数必须真的生效。
    # 原因：原接口未声明 Query 参数，`?talent_id=999` 会被静默忽略并返回全表，
    #      调用方以为筛过了 —— 这类「不报错但结果错」比 500 更难发现。
    async def test_list_filters_are_effective(self, client, seed_data, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        run = await client.post(
            f"{API}/matchings/run",
            json={"position_id": seed_data.position_id, "talent_ids": list(seed_data.talent_ids.values())},
            headers=headers,
        )
        talent_id = run.json()["data"]["items"][0]["talent_id"]

        only_this_talent = await client.get(
            f"{API}/matchings?talent_id={talent_id}&page_size=50", headers=headers
        )
        assert only_this_talent.status_code == 200
        rows = only_this_talent.json()["data"]["items"]
        assert rows, "该人才刚跑过匹配，应至少有一条记录"
        assert all(row["talent_id"] == talent_id for row in rows)

        # 不存在的岗位：必须是 0 条，而不是把全表返回
        empty = await client.get(f"{API}/matchings?position_id=987654&page_size=50", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["data"]["total"] == 0

        # min_score 过滤：高于满分不可能有结果
        high = await client.get(f"{API}/matchings?min_score=99.99&page_size=50", headers=headers)
        assert high.json()["data"]["total"] <= only_this_talent.json()["data"]["total"]


class TestExplainEndpoint:
    async def test_explain_success(self, client, seed_data, admin_token):
        resp = await client.post(
            f"{API}/matchings/explain",
            json={"position_id": seed_data.position_id, "talent_id": seed_data.talent_ids["张伟"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["summary"]
        assert data["source"] in {"dify", "llm_fallback", "rule_fallback"}
        assert "rejected_claims" in data

    async def test_explain_unknown_talent_returns_404(self, client, seed_data, admin_token):
        resp = await client.post(
            f"{API}/matchings/explain",
            json={"position_id": seed_data.position_id, "talent_id": 987654321},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404


class TestProfileEndpoints:
    async def test_get_profile_after_confirm(self, client, seed_data, admin_token):
        resp = await client.get(
            f"{API}/positions/{seed_data.position_id}/profile",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["confirmed"] is True
        assert data["version"] == seed_data.profile_version
        assert data["profile"]["required_skills"]

    async def test_parse_jd_returns_draft_with_channel(self, client, seed_data, admin_token):
        jd = (
            "岗位名称：后端开发工程师\n"
            "岗位职责：\n1. 负责平台后端开发；\n"
            "任职要求：\n1. 本科及以上学历；\n2. 3 年以上后端开发经验；\n"
            "3. 熟悉 Python、FastAPI、MySQL。\n"
        )
        resp = await client.post(
            f"{API}/positions/{seed_data.position_id}/jd/parse",
            json={"jd_text": jd},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["channel"] in {"dify", "rule_fallback"}
        assert data["profile"]["job_title"]
        assert data["profile"]["required_skills"]
        # 解析只是草稿：不得自动确认
        assert data["profile"]["review_required"] is True

    async def test_confirm_generates_new_version(self, client, seed_data, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        current = await client.get(f"{API}/positions/{seed_data.position_id}/profile", headers=headers)
        profile = current.json()["data"]["profile"]

        resp = await client.post(
            f"{API}/positions/{seed_data.position_id}/profile/confirm",
            json={"profile": profile, "expected_version": seed_data.profile_version, "note": "二次确认"},
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == seed_data.profile_version + 1
        assert data["confirmed"] is True
