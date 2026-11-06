"""
模块：scripts/diagnose_api.py
职责：对已启动的真实服务做一次「接口体检」，输出可复现的问题清单。

说明：
- 真实 HTTP + 真实 MySQL，不使用 TestClient / Mock；
- 只做正常业务写入（确认画像、跑一次匹配），不删除任何数据；
- *kwq* 新增：用于交付前的接口巡检，不参与业务运行。

执行：PYTHONPATH=. <venv>/python.exe scripts/diagnose_api.py

标签：[架构组] [P2]（接口连通性诊断脚本）
"""

from __future__ import annotations

import json
import time

import httpx

BASE = "http://127.0.0.1:8010"
API = f"{BASE}/api/v1"

JD_TEXT = """岗位职责：
1. 负责公司核心业务系统的后端服务开发，参与需求评审与技术方案设计；
2. 负责接口性能优化，保障系统稳定性；
3. 参与技术文档编写与代码评审。

任职要求：
1. 本科及以上学历，计算机相关专业，3 年以上后端开发经验；
2. 熟练掌握 Python，熟悉 FastAPI 框架；
3. 熟悉 MySQL 数据库设计与优化；
4. 有 Redis 缓存使用经验者优先；
5. 薪资 25k-35k，具体面议。"""

ROWS: list[tuple[str, bool, str, str]] = []
ISSUES: list[str] = []


def note(step: str, ok: bool, status, detail: str) -> None:
    ROWS.append((step, ok, str(status), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step.ljust(34)} HTTP {str(status).ljust(4)} {detail}")


def issue(text: str) -> None:
    ISSUES.append(text)
    print(f"       ↳ 问题：{text}")


def brief(data, keys: list[str], width: int = 220) -> str:
    if not isinstance(data, dict):
        return str(data)[:width]
    picked = {k: data.get(k) for k in keys if k in data}
    return json.dumps(picked, ensure_ascii=False)[:width]


def overall(hard: list[dict]) -> str:
    """由逐项四态聚合出整体结论（与业务规则一致：FAIL > NEEDS_REVIEW > UNKNOWN > PASS）。"""
    states = {h.get("status") for h in hard}
    for s in ("FAIL", "NEEDS_REVIEW", "UNKNOWN"):
        if s in states:
            return s
    return "PASS"


def main() -> int:
    with httpx.Client(timeout=60.0) as c:
        # ---------- 1. 健康检查 ----------
        r = c.get(f"{BASE}/health")
        note("GET /health", r.status_code == 200, r.status_code, brief(r.json().get("data"), ["status", "version"]))

        r = c.get(f"{BASE}/health/ready")
        ok = r.status_code == 200
        note("GET /health/ready", ok, r.status_code, brief(r.json().get("data"), ["status", "checks"]))
        if not ok:
            issue(f"/health/ready 非 200：{r.text[:160]}")

        # ---------- 2. 未认证访问（应 401） ----------
        r = c.post(f"{API}/matchings/run", json={"position_id": 1})
        ok = r.status_code == 401
        note("POST /matchings/run 无 token", ok, r.status_code, r.json().get("message", "")[:60])
        if not ok:
            issue(f"未认证访问返回 {r.status_code}，应为 401")

        # ---------- 3. 登录 ----------
        r = c.post(f"{API}/auth/login", json={"username": "admin", "password": "Admin@12345"})
        if r.status_code != 200:
            note("POST /auth/login", False, r.status_code, r.text[:160])
            print("\n登录失败，后续步骤终止。")
            return 1
        token = r.json()["data"]["access_token"]
        H = {"Authorization": f"Bearer {token}"}
        note("POST /auth/login", True, r.status_code, f"token 长度={len(token)}")

        r = c.get(f"{API}/auth/me", headers=H)
        note("GET /auth/me", r.status_code == 200, r.status_code, brief(r.json().get("data"), ["id", "username", "roles"]))

        # ---------- 4. 系统信息 ----------
        r = c.get(f"{API}/system/info", headers=H)
        note("GET /system/info", r.status_code == 200, r.status_code, brief(r.json().get("data"), ["implemented_modules"], 260))

        # ---------- 5. JD 解析 ----------
        r = c.post(f"{API}/positions/1/jd/parse", headers=H, json={"jd_text": JD_TEXT})
        parsed = r.json().get("data") or {}
        profile = parsed.get("profile") or {}
        note(
            "POST /positions/1/jd/parse",
            r.status_code == 200,
            r.status_code,
            f"channel={parsed.get('channel')} 必备={profile.get('required_skills')} 优先={profile.get('preferred_skills')}",
        )
        if r.status_code != 200:
            issue(f"JD 解析失败：{r.text[:200]}")

        # 负向：文本过短（应 422）
        r = c.post(f"{API}/positions/1/jd/parse", headers=H, json={"jd_text": "招后端"})
        ok = r.status_code == 422
        note("POST /jd/parse 过短文本", ok, r.status_code, r.json().get("message", "")[:60])
        if not ok:
            issue(f"JD 文本过短应 422，实际 {r.status_code}")

        # 负向：未知岗位（应 404）
        r = c.post(f"{API}/positions/9999/jd/parse", headers=H, json={"jd_text": JD_TEXT})
        ok = r.status_code == 404
        note("POST /positions/9999/jd/parse", ok, r.status_code, r.json().get("message", "")[:60])
        if not ok:
            issue(f"未知岗位 JD 解析应 404，实际 {r.status_code}")

        # ---------- 6. 画像查询 ----------
        r = c.get(f"{API}/positions/1/profile", headers=H)
        view = r.json().get("data") or {}
        inner = view.get("profile") or {}
        base_version = view.get("version")
        note(
            "GET /positions/1/profile",
            r.status_code == 200,
            r.status_code,
            f"version={base_version} confirmed={view.get('confirmed')} 必备={inner.get('required_skills')}",
        )
        if r.status_code == 200 and not view.get("confirmed"):
            issue("画像已确认但查询返回 confirmed=false")

        # ---------- 7. 确认画像（版本 +1） ----------
        if profile:
            r = c.post(
                f"{API}/positions/1/profile/confirm",
                headers=H,
                json={"profile": profile, "expected_version": base_version, "note": "接口体检-自动确认"},
            )
            data = r.json().get("data") or {}
            ok = r.status_code == 200 and data.get("version") == (base_version or 0) + 1
            note("POST /profile/confirm", ok, r.status_code, f"version={data.get('version')} 期望={(base_version or 0) + 1}")
            if not ok:
                issue(f"画像确认版本未按预期 +1：HTTP {r.status_code} {r.text[:160]}")
        else:
            issue("JD 解析未返回 profile，跳过确认步骤")

        # 负向：乐观并发版本冲突（应 409 / 400）
        if profile:
            r = c.post(
                f"{API}/positions/1/profile/confirm",
                headers=H,
                json={"profile": profile, "expected_version": 1, "note": "故意制造版本冲突"},
            )
            ok = r.status_code in (400, 409)
            note("POST /profile/confirm 版本冲突", ok, r.status_code, r.json().get("message", "")[:70])
            if not ok:
                issue(f"expected_version 过期时应拒绝，实际 {r.status_code}")

        # ---------- 8. 执行匹配 ----------
        r = c.post(
            f"{API}/matchings/run",
            headers=H,
            json={"position_id": 1, "top_k": 3, "with_explanation": True},
        )
        run = r.json().get("data") or {}
        items = run.get("items") or []
        note(
            "POST /matchings/run",
            r.status_code == 200,
            r.status_code,
            f"batch={str(run.get('batch_id'))[:8]} recall={run.get('recall_mode')} stats={json.dumps(run.get('stats'), ensure_ascii=False)[:150]}",
        )
        if r.status_code != 200:
            issue(f"执行匹配失败：{r.text[:200]}")
        else:
            if len(items) > 3:
                issue(f"top_k=3 却返回 {len(items)} 条（top_k 未生效）")
            if not run.get("excluded"):
                issue("excluded 为空：无法展示「谁被淘汰、为什么」")
            if items and "rank" not in items[0]:
                issue("MatchItem 未返回 rank：前端只能靠数组下标排序，分页/过滤后会错位")
            if items and "decision" not in items[0]:
                issue("MatchItem 未返回整体判定 decision：前端须自行聚合四态明细")
            for it in items:
                hard = it.get("hard_results") or []
                states = {h.get("item"): h.get("status") for h in hard}
                print(
                    f"       #{it.get('rank', '?')} {it.get('talent_name')} {it.get('score')} "
                    f"整体={it.get('decision') or overall(hard)} "
                    f"完整度={round(float(it.get('data_completeness') or 0) * 100)}% "
                    f"学历={states.get('min_education')} 年限={states.get('min_work_years')} 技能={states.get('required_skills')}"
                )

        # ---------- 9. 回读一致性 ----------
        r = c.get(f"{API}/matchings/positions/1", headers=H)
        latest = r.json().get("data") or {}
        same = str(latest.get("batch_id")) == str(run.get("batch_id"))
        note("GET /matchings/positions/1", r.status_code == 200 and same, r.status_code, f"batch 一致={same} 条数={len(latest.get('items') or [])} 淘汰={len(latest.get('excluded') or [])}")
        if not same:
            issue("回读批次与刚执行批次不一致")
        if not latest.get("excluded"):
            issue("回读后淘汰明细丢失")

        # ---------- 10. 分页 ----------
        r = c.get(f"{API}/matchings", headers=H, params={"page": 1, "page_size": 2})
        page = r.json().get("data") or {}
        note(
            "GET /matchings?page=1&page_size=2",
            r.status_code == 200,
            r.status_code,
            f"total={page.get('total')} pages={page.get('pages')} 本页={len(page.get('items') or [])}",
        )
        if r.status_code == 200 and len(page.get("items") or []) > 2:
            issue("page_size 未生效")
        if r.status_code == 200 and any(not i.get("talent_name") for i in (page.get("items") or [])):
            issue("列表存在 talent_name 为空（HR 无法辨认记录）")

        # ---------- 11. 人才历史 ----------
        r = c.get(f"{API}/matchings/talents/1", headers=H)
        hist = r.json().get("data")
        n = len(hist) if isinstance(hist, list) else len((hist or {}).get("items") or [])
        note("GET /matchings/talents/1", r.status_code == 200 and n > 0, r.status_code, f"记录数={n}")

        # ---------- 12. 单条解释 ----------
        r = c.post(f"{API}/matchings/explain", headers=H, json={"position_id": 1, "talent_id": 1})
        exp = r.json().get("data") or {}
        claims = (exp.get("highlights") or []) + (exp.get("gaps") or [])
        all_cited = all(c.get("evidence_ids") for c in claims)
        note(
            "POST /matchings/explain",
            r.status_code == 200 and all_cited and bool(claims),
            r.status_code,
            f"source={exp.get('source')} 结论={len(claims)} 全部引用证据={all_cited} 被剔除={len(exp.get('rejected_claims') or [])}",
        )
        if not claims:
            issue("解释未产出任何结论（功能实质不可用）")
        if not all_cited:
            issue("存在未引用 evidence_ids 的结论")
        if exp.get("source") not in ("dify", "llm", "rule_fallback"):
            issue(f"解释来源未如实标注：{exp.get('source')}")

        # ---------- 13. 策略口径一致性 ----------
        r = c.post(
            f"{API}/matchings/run",
            headers=H,
            json={"position_id": 1, "strategy": "soft", "with_explanation": False},
        )
        soft = r.json().get("data") or {}
        soft_stats = soft.get("stats") or {}
        ok = r.status_code == 200 and int(soft_stats.get("unknown") or 0) > 0
        note(
            "POST /matchings/run strategy=soft",
            ok,
            r.status_code,
            f"items={len(soft.get('items') or [])} unknown={soft_stats.get('unknown')} needs_review={soft_stats.get('needs_review')} excluded={len(soft.get('excluded') or [])}",
        )
        if not ok:
            issue("soft 策略下 stats.unknown=0，但候选池中存在档案缺失者（「没算」被显示成「没有」）")
        if soft.get("excluded"):
            issue("soft 策略不应淘汰任何人（语义是只排序不筛人）")

        # ---------- 14. 参数边界 ----------
        r = c.post(f"{API}/matchings/run", headers=H, json={"position_id": 1, "top_k": 0})
        note("POST /matchings/run top_k=0", r.status_code == 422, r.status_code, r.json().get("message", "")[:60])

        r = c.post(f"{API}/matchings/run", headers=H, json={"position_id": 1, "min_score": 999})
        ok = r.status_code in (200, 422)
        note("POST /matchings/run min_score=999", ok, r.status_code, f"返回条数={len((r.json().get('data') or {}).get('items') or [])}")

        r = c.post(f"{API}/matchings/run", headers=H, json={"position_id": 1, "requirement_version": 999})
        ok = r.status_code == 404
        note("POST /matchings/run 版本=999", ok, r.status_code, r.json().get("message", "")[:70])
        if not ok:
            issue(f"指定不存在的画像版本应 404，实际 {r.status_code}")

        r = c.post(f"{API}/matchings/run", headers=H, json={"position_id": 9999})
        note("POST /matchings/run 岗位=9999", r.status_code == 404, r.status_code, r.json().get("message", "")[:60])

        # ---------- 14. 占位模块 ----------
        for mod in ("talents", "resumes", "assessments"):
            r = c.get(f"{API}/{mod}", headers=H)
            print(f"       [占位] GET /{mod} -> {r.status_code} {r.json().get('message', '')[:40]}")

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _, _ in ROWS if ok)
    print("\n" + "=" * 72)
    print(f"接口体检：{passed}/{len(ROWS)} 通过")
    if ISSUES:
        print(f"发现问题 {len(ISSUES)} 项：")
        for i, text in enumerate(ISSUES, 1):
            print(f"  {i}. {text}")
    else:
        print("未发现接口层问题。")
    print("=" * 72)
    return 0 if not ISSUES else 2


if __name__ == "__main__":
    raise SystemExit(main())
