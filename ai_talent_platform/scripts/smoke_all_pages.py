"""全页面接口冒烟测试：模拟前端每个页面的加载与关键操作路径。

标签：[架构组] [P2]

用法：.venv/bin/python scripts/smoke_all_pages.py [--base http://127.0.0.1:8000]
输出：逐项 PASS/FAIL 清单与失败详情，退出码 = 失败数（0 表示全部通过）。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail[:300]}" if not ok else ""))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    global API
    API = f"{args.base}/api/v1"

    async with httpx.AsyncClient(timeout=60) as c:
        # ---------- 公共 ----------
        r = await c.get(f"{args.base}/health/ready")
        record("健康检查 /health/ready", r.status_code == 200, r.text)

        # ---------- 登录页 ----------
        r = await c.post(f"{API}/auth/login", json={"username": "admin", "password": "Admin@12345"})
        ok = r.status_code == 200 and r.json().get("code") == 200
        record("登录 POST /auth/login", ok, r.text)
        token = r.json()["data"]["access_token"] if ok else ""
        h = {"Authorization": f"Bearer {token}"}

        r = await c.get(f"{API}/auth/me", headers=h)
        record("个人中心 GET /auth/me", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 系统信息 ----------
        r = await c.get(f"{API}/system/info", headers=h)
        record("系统信息 GET /system/info", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 用户与权限 ----------
        r = await c.get(f"{API}/users", params={"page": 1, "page_size": 10}, headers=h)
        record("用户管理 GET /users", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 组织架构 ----------
        r = await c.get(f"{API}/organizations/tree", headers=h)
        record("组织架构 GET /organizations/tree", r.status_code == 200 and r.json().get("code") == 200, r.text)
        orgs = r.json().get("data", []) if r.status_code == 200 else []

        # ---------- 人才驾驶舱 / 人才档案 ----------
        r = await c.get(f"{API}/talents", params={"page": 1, "page_size": 10}, headers=h)
        record("人才档案 GET /talents", r.status_code == 200 and r.json().get("code") == 200, r.text)
        talents = r.json().get("data", {}).get("items", []) if r.status_code == 200 else []
        if talents:
            tid = talents[0]["id"]
            r = await c.get(f"{API}/talents/{tid}", headers=h)
            record(f"人才详情 GET /talents/{tid}", r.status_code == 200 and r.json().get("code") == 200, r.text)
            r = await c.get(f"{API}/talents/{tid}/portrait", headers=h)
            record(f"人才画像 GET /talents/{tid}/portrait", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 岗位管理 ----------
        r = await c.get(f"{API}/positions", params={"page": 1, "page_size": 10}, headers=h)
        record("岗位管理 GET /positions", r.status_code == 200 and r.json().get("code") == 200, r.text)
        positions = r.json().get("data", {}).get("items", []) if r.status_code == 200 else []

        # ---------- 简历解析 ----------
        r = await c.get(f"{API}/resumes", params={"page": 1, "page_size": 10}, headers=h)
        record("简历管理 GET /resumes", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 人岗匹配 ----------
        if positions:
            pid = positions[0]["id"]
            r = await c.post(f"{API}/matchings/run", json={"position_id": pid, "top_k": 5}, headers=h)
            record(f"人岗匹配 POST /matchings/run (position={pid})", r.status_code == 200 and r.json().get("code") == 200, r.text)
            r = await c.get(f"{API}/matchings/positions/{pid}", headers=h)
            record(f"匹配结果 GET /matchings/positions/{pid}", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 智能测评 ----------
        r = await c.get(f"{API}/assessments", params={"page": 1, "page_size": 10}, headers=h)
        record("智能测评 GET /assessments", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 培训发展 ----------
        r = await c.get(f"{API}/trainings/courses", params={"page": 1, "page_size": 10}, headers=h)
        record("培训课程 GET /trainings/courses", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/trainings/plans", params={"page": 1, "page_size": 10}, headers=h)
        record("培训计划 GET /trainings/plans", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/trainings/notifications", headers=h)
        record("培训通知 GET /trainings/notifications", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 知识库 ----------
        r = await c.get(f"{API}/knowledge/docs", params={"page": 1, "page_size": 10}, headers=h)
        record("知识库 GET /knowledge/docs", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 数据洞察 ----------
        for type_ in ("education", "age", "skill", "org"):
            r = await c.get(f"{API}/analytics/distribution", params={"type": type_}, headers=h)
            record(f"数据洞察 GET /analytics/distribution?type={type_}", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/analytics/trend", params={"start_month": "2025-05-01", "end_month": "2026-11-01", "comparison": "mom"}, headers=h)
        record("数据洞察 GET /analytics/trend", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/analytics/assessment/summary", headers=h)
        record("数据洞察 GET /analytics/assessment/summary", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/analytics/reports", params={"page": 1, "page_size": 10}, headers=h)
        record("数据洞察 GET /analytics/reports", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- AI 智能助手 ----------
        r = await c.get(f"{API}/agents/conversations", params={"page": 1, "page_size": 10}, headers=h)
        record("AI 助手 GET /agents/conversations", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.post(f"{API}/agents/chat", json={"agent_type": "assistant", "message": "你好，请介绍一下平台"}, headers=h)
        record("AI 助手 POST /agents/chat", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 审计 ----------
        r = await c.get(f"{API}/audit/logs", params={"page": 1, "page_size": 10}, headers=h)
        record("审计日志 GET /audit/logs", r.status_code == 200 and r.json().get("code") == 200, r.text)
        r = await c.get(f"{API}/audit/stats", headers=h)
        record("审计统计 GET /audit/stats", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 邀约 ----------
        r = await c.get(f"{API}/invites", params={"page": 1, "page_size": 10}, headers=h)
        record("人才邀约 GET /invites", r.status_code == 200 and r.json().get("code") == 200, r.text)

        # ---------- 文件中心（上传一个 txt 测试，category 与前端 KnowledgePage 一致） ----------
        files = {"file": ("测试文档.txt", io.BytesIO("AI 人才平台知识库测试文档。\n用于验证文件上传与索引链路。".encode("utf-8")), "text/plain")}
        r = await c.post(f"{API}/files/upload", data={"category": "policy"}, files=files, headers=h)
        record("文件中心 POST /files/upload", r.status_code == 200 and r.json().get("code") == 200, r.text)

    failed = [x for x in results if not x[1]]
    print("=" * 60)
    print(f"总计 {len(results)} 项：通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print(f"  - {name}: {detail[:500]}")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
