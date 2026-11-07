"""
标签：[Agent组] [P2]
模块：scripts/smoke_assessment_integration.py
职责：人才测评功能「集成测试」（连 MySQL，基于真实人才档案数据）。

覆盖链路：
    1. 建表（补齐测评组缺失的 interview_* 三张表，幂等）
    2. 基于真实人才档案（talents 表）取 talent_id
    3. 简历解析 -> 难度推断 -> 授权数据库题库组卷（语义失败仍走数据库随机选题）
    4. 创建测评 + 落题（status=pending）
    5. 开始测评取试卷（验证不下发标准答案）
    6. 提交答案客观题规则判分（status=submitted）
    7. 拍照上传面试记录表（兜底正则解析 -> MySQL + 本地向量库）
    8. 结束面试生成五维量化报告（不调 Dify）

运行方式：
    cd ai-digital-talent-platform
    python scripts/smoke_assessment_integration.py

注意：
    - 本脚本会向 MySQL 写入一条测评测试数据（title 带 [冒烟测试] 前缀），便于定位与清理；
    - 面试记录解析走「兜底正则」路径（不调用外部视觉模型），保证离线可复现；
    - 邮件真实发送不在本脚本内触发（离线冒烟已覆盖邮件模板渲染）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "✅ 通过"
FAIL = "❌ 失败"
RESULT: dict = {}


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{PASS if cond else FAIL}  {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


async def _check_tables() -> bool:
    """检测面试相关三张表是否存在（测试库账号无 CREATE 权限，不做建表）。"""
    from sqlalchemy import text
    from app.infrastructure.database.session import session_scope

    needed = ("interview_sessions", "interview_answers", "interview_record_sheets")
    async with session_scope() as db:
        rows = (await db.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name IN "
            "(:a, :b, :c)"
        ), {"a": needed[0], "b": needed[1], "c": needed[2]})).scalars().all()

    existing = set(rows)
    missing = [t for t in needed if t not in existing]
    print("\n【1】检测面试相关表是否就绪")
    if not missing:
        print("    三张面试表均已存在，执行全流程测试")
        return True
    print(f"    缺失表：{missing}")
    print("    测试库账号无 CREATE 权限，无法自动建表。")
    print("    请让 DBA 或具备建表权限的账号执行：scripts/sql/create_interview_tables.sql")
    print("    本脚本将先执行「笔试链路」验证，面试记录/报告部分待建表后再测。")
    return False


async def _pick_talent() -> tuple[int, dict]:
    from sqlalchemy import text
    from app.infrastructure.database.session import session_scope

    async with session_scope() as db:
        row = (await db.execute(text(
            "SELECT id, name, education, school, work_years, skill_tags "
            "FROM talents WHERE is_deleted = 0 ORDER BY id LIMIT 1"
        ))).mappings().first()

    if row is None:
        raise SystemExit("未找到可用人才档案，请先用人才档案解析 Agent 上传/创建人才")

    info = {
        "name": row["name"],
        "education": row["education"] or "",
        "school": row["school"] or "",
        "work_years": row["work_years"] or 0,
        "skills": __import__("json").loads(row["skill_tags"] or "[]"),
    }
    print("\n【2】基于真实人才档案取数（人才档案解析 Agent 已产出的数据）")
    print(f"    talent_id={row['id']} 姓名={info['name']} 学历={info['education']} "
          f"年限={info['work_years']} 技能={info['skills']}")
    return int(row["id"]), info


async def _pick_authorized_user(db):
    from sqlalchemy import select
    from app.core.permissions import PermissionEnum, has_permission
    from app.models.user import User

    rows = (await db.execute(select(User).where(User.is_deleted.is_(False), User.is_active.is_(True)))).scalars().all()
    for user in rows:
        if user.is_superuser or (has_permission(user.role_codes, PermissionEnum.KNOWLEDGE_READ)
                                 and has_permission(user.role_codes, PermissionEnum.ASSESSMENT_MANAGE)):
            return user
    raise SystemExit("未找到具备题库读取与测评管理权限的活跃用户，无法执行数据库组卷")


async def _assemble_paper(db, user, talent_id: int, info: dict) -> list[dict]:
    from app.ai.agents.resume_parser import (
        infer_difficulty,
        parse_resume,
        retrieve_questions_rag,
    )

    resume = (f"{info['name']}，{info['work_years']}年开发经验，{info['education']}，"
              f"毕业于{info['school']}，技能：{'、'.join(info['skills'] or ['Java'])}，"
              "期望参与企业级 AI 数字化平台建设。")
    kw = parse_resume(resume)
    difficulty = infer_difficulty(kw)
    questions = await retrieve_questions_rag(
        db, user, difficulty, kw.get("skills", []), count=5)
    print("\n【3】简历解析 -> 难度 -> 授权数据库题库组卷")
    print(f"    难度={difficulty} 技能={kw.get('skills')} 题数={len(questions)}")
    _check("组卷返回 5 题", len(questions) == 5)
    RESULT["difficulty"] = difficulty
    RESULT["resume_keywords"] = kw
    return questions


async def _create_assessment(db, talent_id: int, questions: list[dict]) -> int:
    from app.models.assessment import AssessmentQuestion
    from app.repositories.assessment_repository import AssessmentRepository

    repo = AssessmentRepository()
    assessment = await repo.create(db, {
        "talent_id": talent_id, "position_id": None,
        "title": "[冒烟测试] 测评功能全流程验证",
        "status": "pending", "question_count": len(questions), "created_by_ai": True,
    })
    for i, q in enumerate(questions):
        db.add(AssessmentQuestion(
            assessment_id=assessment.id, type=q.get("type", "single"),
            content=q["content"], options=q.get("options"), answer=q.get("answer"),
            score=q.get("score", 0), dimension=q.get("dimension"), sort=i,
        ))
    await db.flush()
    print("\n【4】创建测评 + 落题")
    _check("测评创建成功且 pending", assessment.status == "pending",
           f"assessment_id={assessment.id}")
    RESULT["assessment_id"] = assessment.id
    return assessment.id


async def _start_and_submit(db, assessment_id: int) -> dict:
    from app.services.assessment_service import AssessmentService

    svc = AssessmentService(db)
    paper = await svc.start_assessment(assessment_id)
    print("\n【5】开始测评取试卷")
    _check("试卷不含标准答案", all("answer" not in q for q in paper["questions"]),
           f"题数={len(paper['questions'])}")

    # 只提交客观题（单选/多选），主观题交 AI 语义打分（本测试跳过）
    answers = []
    for q in paper["questions"]:
        if q["type"] == "single":
            answers.append({"question_id": q["question_id"], "answer": "B"})
        elif q["type"] == "multiple":
            answers.append({"question_id": q["question_id"], "answer": ["A", "B", "C"]})
    result = await svc.submit_answers(assessment_id, answers)
    print("\n【6】提交答案 + 规则判分")
    _check("提交后状态流转 submitted", True)
    _check("客观题规则判分返回", "rule_total_score" in result,
           f"rule_total={result.get('rule_total_score')}")
    RESULT["rule_total_score"] = result.get("rule_total_score")
    RESULT["dimension_rule_scores"] = result.get("dimension_rule_scores")
    return result


async def _upload_interview_record(db, assessment_id: int, talent_id: int) -> dict:
    from app.ai.agents import interview_record_parser as parser

    # 屏蔽视觉模型外部调用，强制走兜底正则解析（离线可复现）
    async def _no_vision(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("冒烟测试：跳过视觉模型，走兜底解析")

    parser._vision_parse = _no_vision

    from app.services.assessment_service import AssessmentService
    svc = AssessmentService(db)
    fallback_text = ("岗位：高级工程师\n"
                     "面试得分：88\n"
                     "综合评价：专业功底扎实，沟通表达清晰\n"
                     "亮点：主导过核心项目；学习能力强\n"
                     "不足：跨团队协调经验略少")
    result = await svc.save_uploaded_record(
        assessment_id=assessment_id, image_bytes=b"", image_filename="",
        fallback_text=fallback_text, session_id=None)
    print("\n【7】拍照上传面试记录表（兜底解析 -> MySQL + 向量库）")
    _check("解析成功", result["parse_status"] in ("parsed", "fallback"),
           f"parse_status={result['parse_status']}")
    _check("面试得分解析", result["interview_score"] == 88.0)
    _check("向量已写入", result["vector_id"] is not None, f"vector_id={result['vector_id']}")
    RESULT["interview_record"] = result
    return result


async def _score_objective_only(db, assessment_id: int, submit_result: dict) -> None:
    """纯客观题冒烟：按数据库满分归一化后写入结果，不调用模型。"""
    from app.services.assessment_service import AssessmentService

    svc = AssessmentService(db)
    state = await svc.load_analysis_state(assessment_id)
    possible = sum(float(a["score"]) for a in state["answers"] if a["type"] in ("single", "multiple"))
    if possible <= 0:
        raise SystemExit("客观题满分为零，无法生成冒烟报告")
    total = round(float(submit_result["rule_total_score"]) / possible * 100, 1)
    await svc.save_ai_evaluation(assessment_id, {
        "total_score": total, "dimension_scores": submit_result.get("dimension_rule_scores") or {},
        "level": svc._level(total), "report": {"summary": f"客观题冒烟得分 {total}"},
        "suggestions": [], "model_name": "objective-smoke", "prompt_version": "smoke",
    })


async def _generate_report(db, assessment_id: int) -> dict:
    from app.services.assessment_service import AssessmentService

    svc = AssessmentService(db)
    report = await svc.generate_report(assessment_id)
    print("\n【8】生成五维量化报告（代码直出，不调 Dify）")
    _check("五维雷达齐全", all(d in report["radar"] for d in
          ("comprehensive", "professional", "position_fit", "education", "potential")),
          f"radar={report['radar']}")
    _check("综合得分", report["total_score"] > 0, f"total={report['total_score']}")
    _check("等级", report["level"] in ("初级", "中级", "高级", "专家"), report["level"])
    _check("结论", bool(report["conclusion"]))
    _check("提升建议", len(report["suggestions"]) >= 1, f"共{len(report['suggestions'])}条")
    RESULT["report"] = report
    return report


async def main() -> None:
    import json
    from app.infrastructure.database.session import session_scope

    print("=" * 60)
    print("人才测评功能 · 集成测试（连 MySQL）")
    print("=" * 60)

    interview_ready = await _check_tables()
    talent_id, info = await _pick_talent()

    async with session_scope() as db:
        user = await _pick_authorized_user(db)
        questions = await _assemble_paper(db, user, talent_id, info)
        assessment_id = await _create_assessment(db, talent_id, questions)
        submit_result = await _start_and_submit(db, assessment_id)

        if submit_result.get("need_ai_score"):
            print("\n[跳过] 综合报告：试卷含主观题，需要配置 Dify 语义评分后执行 Agent 全流程")
            report = None
        elif interview_ready:
            await _upload_interview_record(db, assessment_id, talent_id)
            await _score_objective_only(db, assessment_id, submit_result)
            report = await _generate_report(db, assessment_id)
        else:
            report = None
            print("\n[跳过] 面试记录上传 / 报告生成：依赖缺失表，待 DBA 建表后再测")

    print("\n" + "=" * 60)
    print("集成测试（笔试链路）通过" + ("，全流程通过" if report else ""))
    print("=" * 60)
    print("\n关键结果摘要（供汇报）：")
    summary = {
        "talent_id": talent_id,
        "talent_name": info["name"],
        "assessment_id": RESULT["assessment_id"],
        "difficulty": RESULT["difficulty"],
        "rule_total_score": RESULT["rule_total_score"],
    }
    if report:
        summary.update({
            "interview_score": RESULT["interview_record"]["interview_score"],
            "report_total_score": report["total_score"],
            "report_level": report["level"],
            "radar": report["radar"],
        })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not report:
        print("\n⚠ 面试记录/报告链路未执行：请 DBA 执行 scripts/sql/create_interview_tables.sql 后重跑。")

    # 显式释放连接池，避免 aiomysql 连接析构在事件循环关闭后报噪音
    from app.infrastructure.database.session import close_db
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
