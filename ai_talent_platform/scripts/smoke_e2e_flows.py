"""端到端业务流冒烟测试：覆盖各页面的关键写操作链路。

标签：[架构组] [P2]

用法：.venv/bin/python scripts/smoke_e2e_flows.py [--base http://127.0.0.1:8000]

覆盖链路：
  组织/岗位/人才 CRUD -> 简历上传解析 -> 人岗匹配 -> 测评（建卷/答题/报告）
  -> 培训（课程/计划/推荐/通知）-> 知识库问答 -> 邀约（邮件渠道 + 笔试全流程）
  -> 用户管理（建/授权/禁用/重置/删）-> AI 助手流式对话。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import httpx  # noqa: E402

results: list[tuple[str, bool, str]] = []


class RetryingClient(httpx.AsyncClient):
    """服务端 500 后会关闭连接：对连接级错误自动重试一次，避免误报网络故障。"""

    async def request(self, *args, **kwargs):  # type: ignore[override]
        last: Exception | None = None
        for _ in range(3):
            try:
                return await super().request(*args, **kwargs)
            except httpx.TransportError as exc:  # noqa: PERF203
                last = exc
                await asyncio.sleep(0.3)
        raise last  # type: ignore[misc]


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail[:400]}" if not ok else ""))


def ok_resp(r: httpx.Response) -> bool:
    return r.status_code in (200, 201) and r.json().get("code") in (200, 201)


def data_of(r: httpx.Response):
    return r.json().get("data")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    api = f"{args.base}/api/v1"

    async with RetryingClient(timeout=120) as c:
        r = await c.post(f"{api}/auth/login", json={"username": "admin", "password": "Admin@12345"})
        token = data_of(r)["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        record("登录", ok_resp(r), r.text)

        # ============ 组织 ============
        r = await c.post(f"{api}/organizations", json={"name": f"E2E测试部门{int(time.time())}", "code": f"e2e-{int(time.time())}"}, headers=h)
        record("创建组织", ok_resp(r), r.text)
        org_id = data_of(r)["id"] if ok_resp(r) else None

        # ============ 岗位 ============
        jd_text = (
            "岗位职责：负责人才平台后端服务的设计与开发，参与系统架构演进。"
            "任职要求：本科及以上学历，5 年以上后端开发经验，精通 Python 与 FastAPI，"
            "熟悉 MySQL 数据库设计与 Redis 缓存优化，掌握 Docker 与 Kubernetes 部署，"
            "有高并发系统经验者优先，具备良好的沟通协作能力。"
        )
        r = await c.post(f"{api}/positions", json={
            "title": "高级Python工程师", "code": f"E2E-PY-{int(time.time())}", "organization_id": org_id,
            "level": "senior", "headcount": 2, "status": "open",
            "skill_tags": ["Python", "FastAPI", "MySQL"],
            "salary_min": 25000, "salary_max": 40000,
            "description": jd_text,
        }, headers=h)
        record("创建岗位", ok_resp(r), r.text)
        position_id = data_of(r)["id"] if ok_resp(r) else None
        if position_id:
            r = await c.get(f"{api}/positions/{position_id}", headers=h)
            record("岗位详情", ok_resp(r), r.text)

        # ============ 人才 ============
        stamp = int(time.time())
        r = await c.post(f"{api}/talents", json={
            "name": "张伟", "education": "本科", "school": "测试大学",
            "major": "计算机科学", "work_years": 6, "organization_id": org_id,
            "current_company": "BBB公司", "current_position": "高级后端工程师",
            "skill_tags": ["Python", "MySQL", "Redis"],
            "mobile": f"138{stamp % 100000000:08d}", "email": f"zhangwei{stamp}@example.com",
            "self_evaluation": "E2E 测试人才档案",
        }, headers=h)
        record("创建人才", ok_resp(r), r.text)
        talent_id = data_of(r)["id"] if ok_resp(r) else None
        if talent_id:
            r = await c.get(f"{api}/talents/{talent_id}", headers=h)
            record("人才详情", ok_resp(r), r.text)
            r = await c.post(f"{api}/talents/{talent_id}/portrait/refresh", headers=h)
            record("刷新人才画像", r.status_code in (200, 202), r.text)

        # ============ 简历 ============
        resume_text = (
            "张伟 的个人简历\n"
            "联系电话：13800000001，邮箱：zhangwei@example.com\n"
            "教育背景：测试大学 计算机科学 本科 2013-2017\n"
            "工作经历：\n"
            "2017-2020 AAA公司 后端开发工程师，负责订单系统，使用 Python/Django/MySQL。\n"
            "2020-至今 BBB公司 高级后端工程师，负责中台服务，使用 Python/FastAPI/MySQL/Redis。\n"
            "技能：Python, FastAPI, MySQL, Redis, Docker, Kubernetes\n"
            "自我评价：六年后端开发经验，擅长高并发系统设计。"
        ).encode("utf-8")
        files = {"file": ("张伟-后端工程师.txt", io.BytesIO(resume_text), "text/plain")}
        r = await c.post(f"{api}/resumes/upload", files=files, headers=h)
        record("上传简历", ok_resp(r), r.text)
        resume_id = data_of(r)["id"] if ok_resp(r) else None
        file_id = data_of(r).get("file_id") if ok_resp(r) else None
        if resume_id:
            r = await c.post(f"{api}/resumes/{resume_id}/parse", json={"force": True}, headers=h)
            record("触发简历解析", r.status_code in (200, 202), r.text)
            # 轮询解析结果（进程内后台任务）
            parsed = None
            for _ in range(20):
                await asyncio.sleep(1)
                r = await c.get(f"{api}/resumes/{resume_id}/result", headers=h)
                if ok_resp(r):
                    parsed = data_of(r)
                    if str(parsed.get("status")) in ("parsed", "success", "failed"):
                        break
            record("简历解析结果", parsed is not None and str(parsed.get("status")) != "failed",
                   json.dumps(parsed, ensure_ascii=False)[:400] if parsed else "无结果")
            if resume_id and talent_id:
                r = await c.post(f"{api}/resumes/{resume_id}/bind", json={"talent_id": talent_id}, headers=h)
                record("简历绑定人才", ok_resp(r), r.text)

        # ============ 人岗匹配（真实路径：JD 解析 -> 确认画像 -> 匹配） ============
        if position_id:
            r = await c.post(f"{api}/positions/{position_id}/jd/parse",
                             json={"jd_text": jd_text, "job_title": "高级Python工程师"}, headers=h)
            record("JD 智能解析", ok_resp(r), r.text)
            profile = (data_of(r) or {}).get("profile") if ok_resp(r) else None
            if profile:
                r = await c.post(f"{api}/positions/{position_id}/profile/confirm",
                                 json={"profile": profile, "note": "E2E 自动确认"}, headers=h)
                record("确认岗位画像", ok_resp(r), r.text)
            r = await c.post(f"{api}/matchings/run", json={
                "position_id": position_id, "top_k": 5, "strategy": "hybrid",
            }, headers=h)
            record("执行人岗匹配", ok_resp(r), r.text)

        # ============ 智能测评 ============
        if file_id:
            r = await c.post(f"{api}/assessments", json={
                "file_id": file_id, "question_count": 3, "title": "E2E 测评试卷",
            }, headers=h)
            record("创建测评", ok_resp(r), r.text)
            assessment_id = data_of(r)["id"] if ok_resp(r) else None
            if assessment_id:
                r = await c.get(f"{api}/assessments/{assessment_id}/paper", headers=h)
                record("获取试卷", ok_resp(r), r.text)
                r = await c.post(f"{api}/assessments/{assessment_id}/start", headers=h)
                record("开始测评", ok_resp(r), r.text)
            paper = data_of(r) if ok_resp(r) else {}
            questions = paper.get("questions") or []
            answers = []
            for q in questions:
                qid = q.get("question_id") or q.get("id")
                options = q.get("options") or []
                if q.get("type") == "single" and options:
                    answers.append({"question_id": qid, "answer": options[0].get("key")})
                elif q.get("type") == "multiple" and options:
                    answers.append({"question_id": qid, "answer": [o.get("key") for o in options]})
                else:
                    answers.append({"question_id": qid, "answer": "具备相关经验，能胜任该岗位。"})
            if assessment_id:
                r = await c.post(f"{api}/assessments/{assessment_id}/submit",
                                 json={"answers": answers}, headers=h)
                record("提交答卷", ok_resp(r), r.text)
                r = await c.post(f"{api}/assessments/{assessment_id}/interview/finish",
                                 headers=h)
                record("生成综合报告", ok_resp(r), r.text)
                r = await c.get(f"{api}/assessments/{assessment_id}/report", headers=h)
                record("测评报告", ok_resp(r), r.text)

        # ============ 培训 ============
        r = await c.post(f"{api}/trainings/courses", json={
            "title": f"E2E 课程：FastAPI 进阶 {int(time.time())}", "category": "技术",
            "duration_hours": 8, "description": "端到端测试课程",
        }, headers=h)
        record("创建课程", ok_resp(r), r.text)
        course_id = data_of(r)["id"] if ok_resp(r) else None
        if talent_id:
            # 培训计划/推荐需要能力短板来源：显式传 gaps（与前端一致；无测评记录时的入口）
            gaps = [
                {"dimension": "SQL与数据库", "current": 62, "required": 85},
                {"dimension": "数据分析", "current": 70, "required": 85},
            ]
            r = await c.post(f"{api}/trainings/plans/generate", json={
                "talent_id": talent_id, "gaps": gaps, "goal": "E2E：补齐数据能力短板",
            }, headers=h)
            record("生成培训计划", ok_resp(r), r.text)
            r = await c.post(f"{api}/trainings/recommend",
                             json={"talent_id": talent_id, "gaps": gaps, "top_k": 3}, headers=h)
            record("培训推荐", ok_resp(r), r.text)
        r = await c.post(f"{api}/trainings/lag-scan", headers=h)
        record("培训滞后扫描", ok_resp(r), r.text)
        r = await c.post(f"{api}/trainings/notifications/read-all", headers=h)
        record("通知全部已读", ok_resp(r), r.text)

        # ============ 知识库问答 ============
        r = await c.post(f"{api}/text2answer", json={
            "question": "公司的请假制度是怎样的？",
        }, headers=h)
        record("知识库问答 text2answer", ok_resp(r), r.text)

        # ============ 邀约（邮件渠道 + 笔试全流程） ============
        r = await c.post(f"{api}/invites", json={
            "type": "written_test", "talent_id": talent_id,
            "talent_name": "张伟", "email": "zhangwei@example.com",
            "channels": ["email"], "question_count": 3, "duration_minutes": 30,
            "title": "E2E 笔试邀约",
        }, headers=h)
        record("创建邀约（邮件渠道）", ok_resp(r), r.text)
        invite = data_of(r) if ok_resp(r) else None
        token_url = (invite or {}).get("url") or ""
        send_results = (invite or {}).get("send_results") or []
        # 沙箱无外网，SMTP 发送失败是预期；关键是不抛异常、send_results 如实报告
        if send_results:
            record("邀约发送结果如实上报（沙箱无外网允许失败）",
                   all(("ok" in item) for item in send_results),
                   json.dumps(send_results, ensure_ascii=False))
        invite_token = (invite or {}).get("token") or ""
        if invite_token:
            r = await c.get(f"{api}/invites/visit/{invite_token}")
            record("候选人打开邀约页", ok_resp(r), r.text)
            r = await c.get(f"{api}/invites/exam/{invite_token}")
            record("开始笔试（取题）", ok_resp(r), r.text)
            exam = data_of(r) if ok_resp(r) else {}
            answers = {}
            for q in exam.get("questions") or []:
                options = q.get("options") or []
                if options:
                    answers[str(q["id"])] = options[0].get("key")
                else:
                    answers[str(q["id"])] = "知道答案的文本回答"
            r = await c.post(f"{api}/invites/exam/{invite_token}/submit", json={"answers": answers})
            record("交卷", ok_resp(r), r.text)
            r = await c.get(f"{api}/invites/exam/{invite_token}/result")
            record("查看笔试结果", ok_resp(r), r.text)

        # ============ 用户管理 ============
        r = await c.post(f"{api}/users", json={
            "username": "e2e_user", "password": "E2eUser@12345", "real_name": "E2E用户",
            "organization_id": org_id, "role_codes": ["employee"],
        }, headers=h)
        record("创建用户", ok_resp(r), r.text)
        user_id = data_of(r)["id"] if ok_resp(r) else None
        if user_id:
            r = await c.put(f"{api}/users/{user_id}/roles", json={"role_codes": ["hr"]}, headers=h)
            record("分配角色", ok_resp(r), r.text)
            r = await c.patch(f"{api}/users/{user_id}/status", json={"is_active": False, "reason": "E2E"}, headers=h)
            record("禁用用户", ok_resp(r), r.text)
            r = await c.post(f"{api}/users/{user_id}/reset-password", json={"new_password": "NewPass@12345"}, headers=h)
            record("重置密码", ok_resp(r), r.text)
            r = await c.delete(f"{api}/users/{user_id}", headers=h)
            record("删除用户", ok_resp(r), r.text)

        # ============ AI 助手流式 ============
        lines = []
        async with c.stream("POST", f"{api}/agents/chat/stream",
                            json={"agent_type": "assistant", "message": "你好", "stream": True},
                            headers=h) as r:
            async for line in r.aiter_lines():
                if line.strip():
                    lines.append(line)
            record("AI 助手 SSE 流", r.status_code == 200 and any("event: done" in x for x in lines),
                   "; ".join(lines[:6]))

        # ============ 审计核对 ============
        r = await c.get(f"{api}/audit/stats", headers=h)
        record("审计统计（有记录）", ok_resp(r) and (data_of(r).get("total") or 0) > 0, r.text)

    failed = [x for x in results if not x[1]]
    print("=" * 60)
    print(f"总计 {len(results)} 项：通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  - {name}: {detail[:500]}")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
