"""
模块：scripts/e2e_matching_check.py
职责：人岗匹配链路「真实端到端」验收脚本（真实 uvicorn + 真实 MySQL，不使用 Mock）。

============================ 使用说明 ============================
前置条件：
    1) 已启动服务（真实进程，非 TestClient）：
         uvicorn main:app --host 127.0.0.1 --port 8010
    2) 目标库已有岗位 1「后端开发工程师」与若干人才（scripts/seed_data.py 产物）；
    3) 管理员账号可用（scripts/create_admin.py 产物，默认 admin / Admin@12345）。

执行：
    PYTHONPATH=. <venv>/python.exe scripts/e2e_matching_check.py

覆盖的主链路（与业务验收 L4 对应）：
    登录 → 查询画像版本 → JD 解析（草稿） → 确认画像（版本 +1）
         → 执行匹配（落库） → 回读最新结果（一致性校验）
         → 分页查询 → 人才历史 → 单条解释（证据引用校验）
         → 负向：无 token（401）/ 未知岗位（404）

说明：
- 每步都打印真实 HTTP 状态码与关键字段，输出可直接作为验收证据留存；
- 脚本只读 + 只追加匹配记录，不删除任何业务数据；
- *kwq* 新增：本脚本为交付验收证据工具，不参与业务运行。
=================================================================

标签：[Agent组] [P2]（匹配端到端联调脚本）
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8010"
API = f"{BASE}/api/v1"

# 与前端「JD 粘贴」场景一致的真实 JD 文本（含必备/优先、薪资干扰项）
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


def line(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def brief(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def main() -> int:
    client = httpx.Client(timeout=30.0)
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)
        if not cond:
            failures.append(msg)

    # ---------------------------------------------------------------- 0. 服务健康
    line("0. 服务健康检查（真实进程）")
    r = client.get(f"{BASE}/health")
    print(f"GET /health -> {r.status_code}")
    print(brief(r.json().get("data", {})))
    check(r.status_code == 200, "health 返回 200")

    r = client.get(f"{API}/system/info")
    info = r.json().get("data", {})
    print(f"GET /system/info -> {r.status_code} implemented_modules={info.get('implemented_modules')}")
    check("matchings" in (info.get("implemented_modules") or []), "matchings 已注册为真实模块")

    # ---------------------------------------------------------------- 1. 登录
    line("1. 管理员登录（真实 JWT）")
    r = client.post(f"{API}/auth/login", json={"username": "admin", "password": "Admin@12345"})
    print(f"POST /auth/login -> {r.status_code}")
    if r.status_code != 200:
        print(brief(r.json()))
        return 1
    token = r.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"  token 长度={len(token)} 前 20 位={token[:20]}...")
    check(True, "登录拿到 access_token")

    # ---------------------------------------------------------------- 2. 画像当前版本
    line("2. 查询岗位画像（版本与确认状态）")
    r = client.get(f"{API}/positions/1/profile", headers=headers)
    view = r.json().get("data", {})
    base_version = view.get("version", 0)
    base_profile = view.get("profile") or {}
    print(
        f"GET /positions/1/profile -> {r.status_code} version={base_version} "
        f"confirmed={view.get('confirmed')} confirmed_by={view.get('confirmed_by')}"
    )
    print(
        f"  必备技能={base_profile.get('required_skills')} "
        f"优先技能={base_profile.get('preferred_skills')} "
        f"学历={base_profile.get('min_education')} 年限={base_profile.get('min_work_years')}"
    )
    check(r.status_code == 200, "画像查询返回 200")

    # ---------------------------------------------------------------- 3. JD 解析
    line("3. JD 智能解析（草稿，不落库）")
    r = client.post(
        f"{API}/positions/1/jd/parse",
        headers=headers,
        json={"jd_text": JD_TEXT, "job_title": "后端开发工程师"},
    )
    parsed = r.json().get("data", {})
    profile = parsed.get("profile", {})
    print(f"POST /positions/1/jd/parse -> {r.status_code} channel={parsed.get('channel')}")
    print(f"  学历要求={profile.get('min_education')} 年限要求={profile.get('min_work_years')}")
    print(f"  必备技能={profile.get('required_skills')}")
    print(f"  优先技能={profile.get('preferred_skills')}")
    print(f"  待复核条件={[c.get('text') for c in (profile.get('required_conditions') or [])]}")
    print(f"  缺失提醒={parsed.get('message')}")
    check(r.status_code == 200, "JD 解析返回 200")
    check(bool(profile.get("required_skills")), "解析出必备技能（非空）")
    skill_pool = set(profile.get("required_skills") or []) | set(profile.get("preferred_skills") or [])
    check("25k-35k" not in skill_pool and "k-35k" not in skill_pool, "薪资片段未被当成技能")
    check("性能优化" not in skill_pool, "职责描述词未被当成技能")

    # ---------------------------------------------------------------- 4. 确认画像
    line("4. 确认岗位画像（生成新版本 → 才可用于匹配）")
    r = client.post(
        f"{API}/positions/1/profile/confirm",
        headers=headers,
        json={"profile": profile, "expected_version": base_version, "note": "e2e 验收脚本确认"},
    )
    confirmed = r.json().get("data", {})
    print(f"POST /positions/1/profile/confirm -> {r.status_code} version={confirmed.get('version')}")
    check(r.status_code == 200, "画像确认返回 200")
    check(confirmed.get("version") == base_version + 1, f"版本号 +1（{base_version} → {confirmed.get('version')}）")

    # 版本冲突（乐观锁）验证：用旧版本号再确认一次，应被拒绝
    r2 = client.post(
        f"{API}/positions/1/profile/confirm",
        headers=headers,
        json={"profile": profile, "expected_version": base_version, "note": "旧版本号重复确认"},
    )
    print(f"  重复用旧版本号确认 -> {r2.status_code} {r2.json().get('message')}")
    check(r2.status_code in (400, 409, 422), "旧版本号被拦截（乐观锁生效）")

    # ---------------------------------------------------------------- 5. 执行匹配
    line("5. 执行人岗匹配（真实读库 + 落库）")
    t0 = time.time()
    r = client.post(
        f"{API}/matchings/run",
        headers=headers,
        json={"position_id": 1, "talent_ids": [1, 2, 3, 4, 5], "top_k": 5, "with_explanation": True},
    )
    cost = time.time() - t0
    run = r.json().get("data", {})
    print(f"POST /matchings/run -> {r.status_code} 耗时={cost:.2f}s batch_id={run.get('batch_id')}")
    print(f"  recall_mode={run.get('recall_mode')} requirement_version={run.get('requirement_version')}")
    print(f"  stats={brief(run.get('stats', {}))}")
    print("  候选人明细：")
    for idx, it in enumerate(run.get("items", []), start=1):
        states = " ".join(
            f"{h.get('item')}={h.get('status')}" for h in (it.get("hard_results") or [])
        )
        print(
            f"    #{idx} {it.get('talent_name')} 分数={it.get('score')} "
            f"完整度={round(float(it.get('data_completeness') or 0) * 100)}% "
            f"解释来源={it.get('explanation_source')} | {states}"
        )
    run_excluded = {e.get("talent_id"): e for e in run.get("excluded", [])}
    print("  淘汰明细：")
    for ex in run.get("excluded", []):
        print(f"    × {ex.get('talent_name')} 判定={ex.get('status')} 原因={ex.get('reasons')}")
    check(r.status_code == 200, "匹配接口返回 200")
    check(bool(run.get("batch_id")), "返回 batch_id（结果已落库）")
    check(len(run.get("items", [])) <= 5, "返回条数不超过 top_k")
    for it in run.get("items", []):
        check(0 <= float(it.get("score", -1)) <= 100, f"{it.get('talent_name')} 分数在 0~100")
    excluded_names = {e.get("talent_name") for e in run.get("excluded", [])}
    check("王强" not in excluded_names, "档案缺失者（王强）未被硬条件默认淘汰")

    # ---------------------------------------------------------------- 6. 回读一致性
    line("6. 回读最新批次（落库 → 还原一致性）")
    r = client.get(f"{API}/matchings/positions/1", headers=headers)
    latest = r.json().get("data", {})
    same_batch = latest.get("batch_id") == run.get("batch_id")
    print(f"GET /matchings/positions/1 -> {r.status_code} batch_id={latest.get('batch_id')} 条数={len(latest.get('items', []))}")
    check(r.status_code == 200, "回读接口返回 200")
    check(same_batch, "回读批次与刚执行批次一致")
    check(
        [i.get("talent_id") for i in latest.get("items", [])] == [i.get("talent_id") for i in run.get("items", [])],
        "回读排序与执行结果一致",
    )
    # MATCH-014 回归：淘汰明细必须能回读（否则刷新页面就丢失「为什么被淘汰」）
    latest_excluded = {e.get("talent_id"): e for e in latest.get("excluded", [])}
    print(f"  回读淘汰明细：{[(e.get('talent_name'), e.get('reasons')) for e in latest.get('excluded', [])]}")
    check(set(latest_excluded) == set(run_excluded), "回读淘汰名单与执行结果一致")
    check(
        all(latest_excluded[k].get("reasons") == run_excluded[k].get("reasons") for k in run_excluded),
        "回读淘汰原因文本与执行结果一致",
    )

    # ---------------------------------------------------------------- 7. 分页
    line("7. 匹配记录分页")
    r = client.get(f"{API}/matchings", headers=headers, params={"page": 1, "page_size": 5})
    page = r.json().get("data", {})
    print(
        f"GET /matchings?page=1&page_size=5 -> {r.status_code} "
        f"total={page.get('total')} pages={page.get('pages')} 本页={len(page.get('items', []))}"
    )
    check(r.status_code == 200, "分页接口返回 200")
    check(len(page.get("items", [])) <= 5, "分页条数受 page_size 约束")
    if page.get("items"):
        first = page["items"][0]
        print(f"  首条：batch={first.get('batch_id')} talent={first.get('talent_name')} score={first.get('score')}")

    # ---------------------------------------------------------------- 8. 人才历史
    line("8. 人才历史匹配记录")
    r = client.get(f"{API}/matchings/talents/1", headers=headers)
    hist = r.json().get("data", [])
    print(f"GET /matchings/talents/1 -> {r.status_code} 记录数={len(hist)}")
    if hist:
        print(f"  最近一条：{brief(hist[0])}")
    check(r.status_code == 200, "历史接口返回 200")

    # ---------------------------------------------------------------- 9. 解释 + 证据
    line("9. 匹配解释（证据引用可追溯）")
    r = client.post(f"{API}/matchings/explain", headers=headers, json={"position_id": 1, "talent_id": 1})
    exp = r.json().get("data", {})
    print(f"POST /matchings/explain -> {r.status_code} source={exp.get('source')}")
    print(f"  summary={exp.get('summary')}")
    for c in (exp.get("highlights") or [])[:3]:
        print(f"  + {c.get('text')}  ← evidence_ids={c.get('evidence_ids')}")
    for c in (exp.get("gaps") or [])[:3]:
        print(f"  - {c.get('text')}  ← evidence_ids={c.get('evidence_ids')}")
    for c in (exp.get("risks") or [])[:3]:
        print(f"  ! {c.get('text')}  ← evidence_ids={c.get('evidence_ids')}")
    print(f"  待核实={len(exp.get('pending_verification') or [])} 被剔除结论={len(exp.get('rejected_claims') or [])}")
    check(r.status_code == 200, "解释接口返回 200")
    claims = (exp.get("highlights") or []) + (exp.get("gaps") or []) + (exp.get("risks") or [])
    check(bool(claims), "解释产出了结构化结论（非空）")
    check(all(c.get("evidence_ids") for c in claims), "所有解释结论都引用了 evidence_ids")
    check(exp.get("source") in ("dify", "llm", "rule_fallback"), "解释来源被如实标注")

    # ---------------------------------------------------------------- 10. 负向
    line("10. 负向与权限校验")
    r = client.post(f"{API}/matchings/run", json={"position_id": 1})
    print(f"  无 token 执行匹配 -> {r.status_code} {r.json().get('message')}")
    check(r.status_code == 401, "无 token 返回 401")
    r = client.post(f"{API}/matchings/run", headers=headers, json={"position_id": 999999})
    print(f"  未知岗位执行匹配 -> {r.status_code} {r.json().get('message')}")
    check(r.status_code == 404, "未知岗位返回 404")
    r = client.post(f"{API}/matchings/run", headers=headers, json={"position_id": 0})
    print(f"  非法 position_id=0 -> {r.status_code}")
    check(r.status_code == 422, "参数校验返回 422")

    # ---------------------------------------------------------------- 汇总
    line("验收汇总")
    if failures:
        print(f"未通过 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部检查通过：JD 解析 → 画像确认 → 匹配 → 落库 → 回读 → 解释 全链路真实跑通。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
