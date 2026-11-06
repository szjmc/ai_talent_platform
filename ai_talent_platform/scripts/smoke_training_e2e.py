"""
培训模块端到端冒烟测试：走完 6 个节点 + 边界用例。

标签：[Dify组] [P1]（培训六节点端到端冒烟）
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8123/api/v1"
import os
TOKEN = os.environ["TRAINING_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# 每轮课程标题带时间戳：脚本要能重复跑。
# 早期用固定标题，第二遍执行时上一轮留下的同名列还在（已软删但 get_by_title 不过滤软删），
# 于是「重名应 400」和「重复下架应 404」会假报警——是脚本不幂等，不是接口有问题。
COURSE_TITLE = f"_冒烟_自动化_{int(time.time() * 1000)}"

ok, fail = [], []


def _request(method, path, body=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=payload, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def call(method, path, *, body=None, note="", expect=200, quiet=False):
    status, text = _request(method, path, body)
    try:
        data = json.loads(text)
    except Exception:
        data = {"raw": text[:300]}
    passed = (status == expect)
    tag = "OK  " if passed else "FAIL"
    print(f"[{tag}] {method:6} {path:42} {status} expect {expect}  {note}")
    snippet = json.dumps(data, ensure_ascii=False)
    if not quiet:
        print(f"        {snippet[:260]}")
    (ok if passed else fail).append(
        (method, path, status, expect, note, snippet if not quiet else "", data)
    )
    return data


print("=" * 78)
print("阶段 1：课程库 CRUD")
print("=" * 78)
call("GET", "/trainings/courses?page=1&page_size=5", note="课程列表")
created = call("POST", "/trainings/courses", body={
    "title": COURSE_TITLE, "category": "数据分析", "level": "L3",
    "duration_hours": 6.0, "description": "端到端测试创建", "skill_tags": ["数据分析"],
}, note="新建课程", expect=201)
cid = (created.get("data") or {}).get("id")
call("POST", "/trainings/courses", body={"title": COURSE_TITLE, "category": "数据分析"},
     note="重名应 400", expect=400)
if cid:
    call("PUT", f"/trainings/courses/{cid}", body={"duration_hours": 7.5}, note="更新课程")

print()
print("=" * 78)
print("阶段 2：节点 1~4 生成计划（规则模式）")
print("=" * 78)
gen = call("POST", "/trainings/plans/generate", body={
    "talent_id": 2,
    "gaps": [
        {"dimension": "数据分析", "current": 55, "required": 85, "weight": 0.3},
        {"dimension": "SQL与数据库", "current": 60, "required": 80, "weight": 0.2},
    ],
    "max_weeks": 4, "max_hours_per_week": 8, "use_ai": False, "auto_push": True,
    # replace_existing=True：该人才若已有 ongoing 计划，先置为 cancelled 再生成新计划。
    # 不传时服务端会返回 409 并说明原因（下一行用例专门验证这个保护）。
    "replace_existing": True,
}, note="生成计划(use_ai=False, replace_existing)", expect=201)
plan = gen.get("data") or {}
pid = plan.get("id")
print(f"        plan_id={pid} 学时={plan.get('total_hours')} 课数={len(plan.get('items') or [])} "
      f"外部={plan.get('external_count')} 来源={plan.get('generated_by')} "
      f"库覆盖={plan.get('library_rate')} 标准={plan.get('requirement_source')}")
for it in (plan.get("items") or []):
    print(f"          W{it['week']} {it['dimension']:10} {it['hours']}h  "
          f"{it.get('title')}  platform={it['is_platform']}")

# 防重复计划：刚生成的计划处于 ongoing，再生成一份而不加 replace_existing 必须被拒（409）
call("POST", "/trainings/plans/generate", body={
    "talent_id": 2, "use_ai": False, "auto_push": False,
    "gaps": [{"dimension": "数据分析", "current": 55, "required": 85}],
}, note="★ 重复生成未加 replace_existing 应 409", expect=409, quiet=True)

if pid:
    detail = call("GET", f"/trainings/plans/{pid}", note="计划详情")
    d = detail.get("data") or {}
    print(f"        详情: total_weeks={d.get('total_weeks')} total_hours={d.get('total_hours')} "
          f"completion={d.get('completion_rate')} 课程数={len(d.get('items') or [])}")

print()
print("=" * 78)
print("阶段 3：节点 5 进度跟踪")
print("=" * 78)
links = (d.get("items") or []) if pid else []
first_course = links[0]["course_id"] if links else None
if pid and first_course:
    call("POST", "/trainings/records", body={
        "plan_id": pid, "course_id": first_course, "talent_id": 2,
        "progress": 50, "study_minutes": 180,
    }, note="上报进度 50%")
    call("POST", "/trainings/records", body={
        "plan_id": pid, "course_id": first_course, "talent_id": 2,
        "progress": 30, "study_minutes": 200,
    }, note="进度回退应 422(ParamError)", expect=422)
    call("POST", "/trainings/records", body={
        "plan_id": pid, "course_id": first_course, "talent_id": 2,
        "progress": 90, "study_minutes": 5,
    }, note="刷课(学时不足)应标记 suspicious")
    call("GET", f"/trainings/plans/{pid}/progress", note="进度统计")

print()
print("=" * 78)
print("阶段 4：节点 6 考核 + 通过率")
print("=" * 78)
if pid:
    call("POST", f"/trainings/plans/{pid}/score", body={
        "talent_id": 2, "score": 88, "auto_retest": True,
    }, note="提交考核 88 分(应通过)")
    call("GET", f"/trainings/stats/pass-rate", note="通过率统计")
    call("GET", f"/trainings/stats/pass-rate?plan_id=" + str(pid), note="按计划筛选通过率")

print()
print("=" * 78)
print("阶段 5：列表 / 推荐 / 滞后扫描")
print("=" * 78)
call("GET", "/trainings/plans?page=1&page_size=5", note="计划列表")
call("POST", "/trainings/recommend", body={
    "talent_id": 2, "top_k": 2,
    "gaps": [{"dimension": "沟通表达", "current": 50, "required": 80}],
}, note="课程推荐")
call("POST", "/trainings/lag-scan", note="滞后扫描")

print()
print("=" * 78)
print("阶段 6：边界与异常（找 BUG）")
print("=" * 78)
# 6.1 容量不足
call("POST", "/trainings/plans/generate", body={
    "talent_id": 2, "max_weeks": 1, "max_hours_per_week": 2, "use_ai": False,
    "gaps": [{"dimension": "数据分析", "current": 10, "required": 99, "weight": 0.5},
             {"dimension": "SQL与数据库", "current": 10, "required": 99, "weight": 0.5}],
}, note="容量不足应 400", expect=400)
# 6.2 不存在的人才
call("POST", "/trainings/plans/generate", body={
    "talent_id": 999999, "use_ai": False,
    "gaps": [{"dimension": "数据分析", "current": 50, "required": 80}],
}, note="人才不存在应 404", expect=404)
# 6.3 空短板 / 全部达标
call("POST", "/trainings/plans/generate", body={
    "talent_id": 2, "use_ai": False,
    "gaps": [{"dimension": "数据分析", "current": 95, "required": 80}],
}, note="全达标(200 且无需培训)", expect=201)
# 6.4 计划不存在
call("GET", "/trainings/plans/999999", note="计划不存在应 404", expect=404, quiet=True)
call("POST", "/trainings/records", body={
    "plan_id": 999999, "course_id": 1, "talent_id": 2, "progress": 10, "study_minutes": 10,
}, note="写不存在计划的进度应 404", expect=404, quiet=True)
# 6.5 ★ 跨人才写入：用别人的人才 ID 写这个计划的进度
if pid and first_course:
    r = call("POST", "/trainings/records", body={
        "plan_id": pid, "course_id": first_course, "talent_id": 999999,
        "progress": 10, "study_minutes": 60,
    }, note="★ 非本计划人才上报进度（应拒绝 422）", expect=422, quiet=True)
# 6.6 ★ 课程不属于该计划
if pid:
    call("POST", "/trainings/records", body={
        "plan_id": pid, "course_id": 7, "talent_id": 2,
        "progress": 10, "study_minutes": 60,
    }, note="★ 课程不在计划内（应拒绝 422）", expect=422, quiet=True)
# 6.7 负分考核
if pid:
    call("POST", f"/trainings/plans/{pid}/score", body={"talent_id": 2, "score": -10},
         note="负分应 422", expect=422, quiet=True)
    call("POST", f"/trainings/plans/{pid}/score", body={"talent_id": 2, "score": 150},
         note="超 100 分应 422", expect=422, quiet=True)

if cid:
    call("DELETE", f"/trainings/courses/{cid}", note="下架课程(软删)")
    call("DELETE", f"/trainings/courses/{cid}", note="重复下架应 404", expect=404, quiet=True)

print()
print("=" * 78)
print(f"结果：通过 {len(ok)} / 失败 {len(fail)}")
for m, p, got, exp, note, _, _ in fail:
    print(f"  ✗ {m} {p} → {got} (期望 {exp})  {note}")
print("=" * 78)
sys.exit(0 if not fail else 1)
