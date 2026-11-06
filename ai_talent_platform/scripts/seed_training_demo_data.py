"""
脚本：scripts/seed_training_demo_data.py
职责：为培训模块四张表灌入一套「可直接演示 / 联调」的完整业务数据。

标签：[Dify组] [P1]（培训演示数据：多人才 / 多状态 / 多预警等级）

所属模块：智能培训赋能 Agent（培训模块）

使用方式：
    python scripts/seed_training_courses.py       # 先有课程库
    python scripts/seed_training_demo_data.py     # 再灌业务数据

为什么要有这个脚本（与 demo_training.py 的分工）
------------------------------------------------
    demo_training.py          单条链路、逐步打印，用于**答辩现场讲六个节点**
    本脚本                     批量铺数据，用于**前端联调 / 看板截图 / 回归测试**

只有一条 finished 的计划时，很多页面是空的：
    计划列表只有 1 行、完成率全是 0 或 100%、预警看板永远「无预警」、
    通过率统计分母为 0、站内信一条都没有。演示时看起来像「功能没做」。
本脚本造出 5 份计划覆盖 draft / ongoing / finished 三种状态，
并制造「正常 / 提醒 / HR 预警」三档，让每个页面都有内容可看。

数据隔离
--------
所有演示人才使用 13900001086~1090 号段（独立于真实人才），
按手机号幂等复用；脚本重跑只会重置这几个人的培训数据，不动任何其他人。
注意：仅用于开发与演示环境。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import delete, select  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.infrastructure.database.mysql import dispose_engine  # noqa: E402
from app.infrastructure.database.session import get_engine, session_scope  # noqa: E402
from app.infrastructure.messaging.event_bus import event_bus  # noqa: E402
from app.models.talent import Talent  # noqa: E402
from app.models.training import Course, LearningRecord, PlanCourse, TrainingNotification, TrainingPlan  # noqa: E402
from app.schemas.training import (  # noqa: E402
    GapItem,
    LearningRecordUpdate,
    ScoreSubmitRequest,
    TrainingPlanGenerateRequest,
)
from app.services.training_service import TrainingService  # noqa: E402

LINE = "=" * 76
SUB = "-" * 76

# ------------------------------------------------------------------ 演示剧本
# 每个角色对应一个真实业务场景，覆盖三档预警与三种计划状态。
SCENARIOS: list[dict] = [
    {
        "name": "张伟",
        "mobile": "13900001086",
        "position": "产品经理 P6",
        "gender": "male",
        "work_years": 6,
        "education": "本科",
        "note": "常规场景：按节奏推进，已完成一半课程",
        "status": "ongoing",
        "elapsed_days": 17,   # 周期 28 天，已过约 60%
        "progress_ratio": 0.62,
        "score_course": True,  # 给一门课出考核结论，制造通过率数据
        "score": 86,
        "gaps": [
            {"dimension": "需求分析", "current": 62, "required": 85, "weight": 0.30},
            {"dimension": "数据分析", "current": 55, "required": 80, "weight": 0.25},
            {"dimension": "SQL与数据库", "current": 48, "required": 70, "weight": 0.15},
            {"dimension": "项目管理", "current": 78, "required": 85, "weight": 0.20},
        ],
    },
    {
        "name": "李静",
        "mobile": "13900001087",
        "position": "数据分析师",
        "gender": "female",
        "work_years": 4,
        "education": "硕士",
        "note": "落后场景：时间过 74%、只学 40% → 触发「提醒」；另有一门课考核未通过",
        "status": "ongoing",
        "elapsed_days": 20,
        "progress_ratio": 0.40,
        "score_course": True,
        "score": 72,  # 低于 80 及格线：制造「不通过 + 计划调整事件」，让通过率不是 100%
        "gaps": [
            {"dimension": "数据分析", "current": 58, "required": 88, "weight": 0.35},
            {"dimension": "SQL与数据库", "current": 60, "required": 82, "weight": 0.25},
            {"dimension": "沟通表达", "current": 70, "required": 80, "weight": 0.15},
        ],
    },
    {
        "name": "王强",
        "mobile": "13900001088",
        "position": "技术主管",
        "gender": "male",
        "work_years": 9,
        "education": "本科",
        "note": "严重滞后：时间过 86%、只学 18% → 触发「HR预警」",
        "status": "ongoing",
        "elapsed_days": 24,
        "progress_ratio": 0.18,
        "score_course": False,
        "gaps": [
            {"dimension": "团队协作", "current": 55, "required": 85, "weight": 0.30},
            {"dimension": "项目管理", "current": 58, "required": 82, "weight": 0.25},
            {"dimension": "沟通表达", "current": 62, "required": 80, "weight": 0.20},
        ],
    },
    {
        "name": "赵敏",
        "mobile": "13900001089",
        "position": "运营专员",
        "gender": "female",
        "work_years": 3,
        "education": "本科",
        "note": "闭环完成：全部学完 + 结课考核通过 → 计划 finished",
        "status": "finished",
        "elapsed_days": 28,
        "progress_ratio": 1.0,
        "score_course": False,
        "final_score": 92,
        "gaps": [
            {"dimension": "用户研究", "current": 60, "required": 85, "weight": 0.30},
            {"dimension": "产品设计", "current": 58, "required": 80, "weight": 0.25},
        ],
    },
    {
        "name": "陈磊",
        "mobile": "13900001090",
        "position": "应届产品助理",
        "gender": "male",
        "work_years": 0,
        "education": "本科",
        "note": "草稿场景：方案已生成但未下发（draft），不参与滞后扫描",
        "status": "draft",
        "elapsed_days": 0,
        "progress_ratio": 0.0,
        "score_course": False,
        "draft": True,
        "gaps": [
            {"dimension": "需求分析", "current": 40, "required": 80, "weight": 0.30},
            {"dimension": "产品设计", "current": 38, "required": 78, "weight": 0.25},
            {"dimension": "团队协作", "current": 50, "required": 75, "weight": 0.15},
        ],
    },
]


async def _ensure_talent(db, script: dict) -> Talent:
    talent = (
        await db.execute(
            select(Talent).where(
                Talent.mobile == script["mobile"], Talent.is_deleted.is_(False)
            )
        )
    ).scalar_one_or_none()
    if talent is None:
        talent = Talent(
            name=script["name"],
            gender=script["gender"],
            mobile=script["mobile"],
            education=script["education"],
            work_years=script["work_years"],
            current_position=script["position"],
            status="active",
            source="manual",
            skill_tags=[gap["dimension"] for gap in script["gaps"]],
        )
        db.add(talent)
        await db.flush()
    return talent


async def _reset_training_data(db, talent_id: int) -> int:
    """清空该人才的培训数据（计划级联删除课表 / 学习记录 / 站内信）。

    先删子表再删计划：虽然外键已是 ON DELETE CASCADE，但显式删除
    在关闭外键检查的测试库上也能工作，且删除条数可打印出来。
    """
    plan_ids = (
        await db.execute(select(TrainingPlan.id).where(TrainingPlan.talent_id == talent_id))
    ).scalars().all()
    if not plan_ids:
        return 0
    await db.execute(
        TrainingNotification.__table__.delete().where(
            TrainingNotification.plan_id.in_(plan_ids)
        )
    )
    await db.execute(
        LearningRecord.__table__.delete().where(LearningRecord.plan_id.in_(plan_ids))
    )
    await db.execute(PlanCourse.__table__.delete().where(PlanCourse.plan_id.in_(plan_ids)))
    await db.execute(TrainingPlan.__table__.delete().where(TrainingPlan.id.in_(plan_ids)))
    await db.flush()
    return len(plan_ids)


async def _apply_progress(
    service: TrainingService, db, plan: dict, talent: Talent, script: dict
) -> dict:
    """按「总学时完成比例」给课程写入进度，返回最后一条进度用于展示。

    为什么按学时而不是按课程数：滞后判定的分母是学时。
    若按「40% 的课程学完」来铺数据，实际完成学时可能只有 12%，
    造出来的落后幅度会失真（想要「提醒」却直接掉进「HR 预警」）。
    这里先算目标完成分钟数，再按课表顺序逐门分配，最后一门按剩余量折算。
    """
    ratio = float(script.get("progress_ratio") or 0)
    if ratio <= 0:
        return {}

    items = [item for item in plan["items"] if item.get("course_id")]
    if not items:
        return {}

    # 分母必须用**课程实际学时**（duration_hours），不是排期学时（hours）：
    # 排期学时来自短板预估，与实际课程时长常有差额（例如排 16h 但库里只有 14h 的课），
    # 用排期学时铺数据会写出「14h 的计划学了 16h」这种经不起追问的记录。
    def _course_minutes(item: dict) -> float:
        hours = item.get("duration_hours")
        if hours is None:
            hours = item.get("hours")
        return float(hours or 4) * 60

    total_minutes = sum(_course_minutes(item) for item in items)
    target_minutes = total_minutes * ratio
    consumed = 0.0
    last: dict = {}

    for item in items:
        course_minutes = _course_minutes(item)
        remaining = target_minutes - consumed
        if remaining <= 0:
            break
        take = min(course_minutes, remaining)
        progress = int(round(take / course_minutes * 100)) if course_minutes else 0
        # 正在学的那门至少给 10%，避免「有学时但进度 0%」这种自相矛盾的记录
        progress = max(10, min(100, progress))
        minutes = max(1, int(round(take)))
        last = await service.update_progress(
            LearningRecordUpdate(
                plan_id=plan["id"],
                course_id=item["course_id"],
                talent_id=talent.id,
                progress=progress,
                study_minutes=minutes,
            )
        )
        consumed += take
    return last


async def main() -> None:
    stats_captured: list[str] = []

    def _on_event(payload) -> None:
        if isinstance(payload, dict):
            stats_captured.append(str(payload.get("type")))

    for name in (
        "training.plan_created",
        "training.lag.alert",
        "training.lag.remind",
        "training.retest.required",
        "training.plan.adjust",
    ):
        event_bus.subscribe(name, _on_event)

    async with session_scope() as db:
        service = TrainingService(db)

        print(LINE)
        print("培训模块 · 演示数据初始化")
        print(LINE)

        courses = (
            await db.execute(select(Course).where(Course.is_deleted.is_(False)))
        ).scalars().all()
        if not courses:
            print("课程库为空，节点 3 会全部降级。请先执行：")
            print("   python scripts/seed_training_courses.py")
            return
        print(f"课程库：{len(courses)} 门课，覆盖 {len({c.category for c in courses})} 个维度")

        created_plans = 0
        for script in SCENARIOS:
            print(f"\n{SUB}")
            print(f"【{script['name']}】{script['position']} — {script['note']}")
            talent = await _ensure_talent(db, script)
            cleared = await _reset_training_data(db, talent.id)
            if cleared:
                print(f"  已清理历史计划 {cleared} 份")

            req = TrainingPlanGenerateRequest(
                talent_id=talent.id,
                talent_name=talent.name,
                gaps=[GapItem(**gap) for gap in script["gaps"]],
                max_weeks=4,
                max_hours_per_week=8,
                use_ai=False,
                draft=bool(script.get("draft")),
                auto_push=not script.get("draft"),
            )
            plan = await service.generate_plan(req)
            if not plan.get("id"):
                print("  各维度均已达标，本次未生成计划（跳过）")
                continue
            created_plans += 1

            # 用起始日期制造不同的「时间进度」，这是滞后判定的分母
            plan_row = (
                await db.execute(select(TrainingPlan).where(TrainingPlan.id == plan["id"]))
            ).scalar_one()
            start = date.today() - timedelta(days=int(script.get("elapsed_days") or 0))
            plan_row.start_date = start
            plan_row.end_date = start + timedelta(days=27)
            await db.flush()

            await _apply_progress(service, db, plan, talent, script)

            print(
                f"  计划 id={plan_row.id}「{plan['name']}」状态={plan['status']}"
                f"｜{len(plan['items'])} 门课 / {plan['total_hours']}h"
                f"｜来自课程库 {plan['library_rate']:.0%}"
                f"｜周期 {plan_row.start_date} → {plan_row.end_date}"
            )

            # 单课考核（制造通过率数据）
            if script.get("score_course"):
                target = next((i for i in plan["items"] if i.get("course_id")), None)
                if target:
                    await service.submit_score(
                        ScoreSubmitRequest(
                            plan_id=plan_row.id,
                            talent_id=talent.id,
                            course_id=target["course_id"],
                            score=script["score"],
                            auto_retest=True,
                        )
                    )
                    print(f"  单课考核：{target['title']} {script['score']} 分 → 已记录通过率")

            # 整计划结课考核（闭环完成场景）
            if script.get("final_score"):
                result = await service.submit_score(
                    ScoreSubmitRequest(
                        plan_id=plan_row.id,
                        talent_id=talent.id,
                        score=script["final_score"],
                        auto_retest=True,
                    )
                )
                print(
                    f"  结课考核：{script['final_score']} 分 → {result['assessment']}"
                    f"（计划状态 {result['plan_status']}）"
                )

            progress = await service.get_progress(plan_row.id)
            elapsed = service._elapsed_ratio(plan_row.start_date, plan_row.end_date)
            print(
                f"  进度：时间已过 {elapsed:.0%}｜应完成 {progress['expected_h']}h"
                f" / 实际 {progress['completed_h']}h｜学时完成 {progress['completion']:.0%}"
                f"｜落后 {progress['lag_ratio']:.0%} → 判定 {progress['alert']}"
            )

        # ---------------------------------------------------------- 滞后扫描
        print(f"\n{SUB}")
        print("【节点 5 定时任务】滞后扫描（生成预警 + 站内信）")
        scan = await service.scan_lag()
        await db.flush()
        await event_bus.drain(timeout=2)
        print(
            f"  扫描进行中计划 {scan['scanned']} 份 → HR 预警 {scan['warned']} 份"
            f" / 提醒 {scan['reminded']} 份"
        )
        for alert in scan["alerts"]:
            print(f"    {alert['alert']:<6} {alert['name']}：落后 {alert['lag_ratio']:.0%}")

        # ---------------------------------------------------------- 汇总
        print(f"\n{SUB}")
        print("数据汇总")
        plan_total = len(
            (
                await db.execute(
                    select(TrainingPlan).where(TrainingPlan.is_deleted.is_(False))
                )
            ).scalars().all()
        )
        record_total = len(
            (
                await db.execute(
                    select(LearningRecord).where(LearningRecord.is_deleted.is_(False))
                )
            ).scalars().all()
        )
        notify_total = len(
            (await db.execute(select(TrainingNotification))).scalars().all()
        )
        rates = await service.pass_rate()
        print(f"  courses              {len(courses)} 门")
        print(f"  training_plans       {plan_total} 份（本次新建 {created_plans} 份）")
        print(f"  learning_records     {record_total} 条")
        print(f"  training_notifications {notify_total} 条站内信")
        print(
            f"  考核通过率           {rates['pass_rate']:.0%}"
            f"（{rates['passed']}/{rates['total_examined']}，平均分 {rates['avg_score']}）"
        )
        if stats_captured:
            unique = sorted(set(stats_captured))
            print(f"  事件                 {'、'.join(unique)}")

        print(f"\n{LINE}")
        print("演示数据就绪。前端可直接查：GET /api/v1/trainings/plans")
        print("站内信：GET /api/v1/trainings/notifications")
        print(LINE)

    await dispose_engine(get_engine())


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
