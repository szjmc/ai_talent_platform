"""
脚本：scripts/demo_training.py
职责：培训模块六节点端到端演示（一条命令跑完，可直接用于答辩演示）。

标签：[Dify组] [P1]（六节点端到端演示，答辩用）

所属模块：智能培训赋能 Agent（培训模块）

前置：
    python scripts/init_db.py              # 建表
    python scripts/seed_training_courses.py # 课程库（不跑则节点 3 全部降级）
    python scripts/demo_training.py        # 本脚本

它做的事（严格按六个业务节点顺序）：
    节点 1 短板诊断   → 收测评缺口、剔已达标、排 P0/P1/P2
    节点 2 方案排期   → 4 周、每周 ≤8 学时、末周留考核
    节点 3 课程匹配   → 只从课程库挂真实课程，缺课则降级不编造
    节点 4 推送存档   → 落库 + training.plan_created 事件
    节点 5 进度跟踪   → 算完成率、落后判级、发预警事件
    节点 6 考核复测   → 结课评分、达标发 training.retest.required 回流测评

事件会被订阅并打印出来，用来证明「Agent 之间靠事件解耦，不是直接调用」。
重复执行安全：会先清掉该演示人才的历史计划再重建。
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
from app.models.position import Position  # noqa: E402
from app.models.talent import Talent  # noqa: E402
from app.models.training import Course, TrainingPlan  # noqa: E402
from app.schemas.training import (  # noqa: E402
    GapItem,
    LearningRecordUpdate,
    ScoreSubmitRequest,
    TrainingPlanGenerateRequest,
)
from app.services.training_service import TrainingService  # noqa: E402

LINE = "=" * 74
SUB = "-" * 74

# 演示用人才（与需求文档 2.4 的例子一致：产品经理 P6 张伟）
TALENT_NAME = "张伟"
TALENT_MOBILE = "13900001086"

# 测评 Agent 交付的短板清单（这正是两个模块的交接字段，字段名必须与测评侧一致）
GAPS = [
    {"dimension": "需求分析", "current": 62, "required": 85, "weight": 0.30},
    {"dimension": "数据分析", "current": 55, "required": 80, "weight": 0.25},
    {"dimension": "SQL与数据库", "current": 48, "required": 70, "weight": 0.15},
    {"dimension": "项目管理", "current": 78, "required": 85, "weight": 0.20},
    {"dimension": "沟通表达", "current": 82, "required": 80, "weight": 0.10},  # 已达标，应被剔除
]

_captured: list[dict] = []


def _on_event(payload) -> None:
    """订阅全部培训事件，用于在终端里证明事件确实发出去了。"""
    if isinstance(payload, dict):
        _captured.append(payload)


async def _ensure_talent(db) -> Talent:
    talent = (
        await db.execute(
            select(Talent).where(Talent.mobile == TALENT_MOBILE, Talent.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if talent is None:
        talent = Talent(
            name=TALENT_NAME,
            gender="male",
            mobile=TALENT_MOBILE,
            education="本科",
            work_years=6,
            current_position="产品经理 P6",
            status="active",
            source="manual",
            skill_tags=["需求分析", "数据分析", "SQL"],
        )
        db.add(talent)
        await db.flush()
        print(f"  · 已创建演示人才：{talent.name}（id={talent.id}，产品经理 P6）")
    else:
        print(f"  · 复用已有演示人才：{talent.name}（id={talent.id}）")
    return talent


async def main() -> None:
    for name in ("training.plan_created", "training.lag.alert", "training.lag.remind",
                 "training.retest.required"):
        event_bus.subscribe(name, _on_event)

    async with session_scope() as db:
        service = TrainingService(db)

        # ---------------------------------------------------------- 前置检查
        print(LINE)
        print("培训赋能 Agent · 六节点端到端演示")
        print(LINE)
        course_count = len(
            (await db.execute(select(Course).where(Course.is_deleted.is_(False)))).scalars().all()
        )
        if course_count == 0:
            print("⚠️  课程库为空，节点 3 将全部降级。请先执行：")
            print("   python scripts/seed_training_courses.py")
            return
        print(f"课程库：{course_count} 门课")

        talent = await _ensure_talent(db)

        # 清理历史演示计划（只清这个演示人才，不影响其他人）
        old = (
            await db.execute(select(TrainingPlan.id).where(TrainingPlan.talent_id == talent.id))
        ).scalars().all()
        if old:
            await db.execute(delete(TrainingPlan).where(TrainingPlan.talent_id == talent.id))
            await db.flush()
            print(f"  · 已清理 {len(old)} 条历史演示计划")

        # ---------------------------------------------------------- 节点 1
        print(f"\n{SUB}\n【节点 1】短板清单接收与诊断\n{SUB}")
        gaps = await service.diagnose(talent.id, gaps=[GapItem(**g) for g in GAPS])
        print(f"  收到测评单 {len(GAPS)} 项 → 剔除已达标后剩 {len(gaps)} 项真短板：")
        for g in gaps:
            print(
                f"    {g['priority']}  {g['dimension']:<10} {g['current']}→{g['required']}"
                f"  缺口 {g['delta']:>4.0f}  权重 {g['weight']}  预估 {g['est_hours']}h"
            )

        # ---------------------------------------------- 节点 1 补充：岗位标准对照
        # 这段是「千人千面」的直接证据：分数一模一样，只换岗位标准，短板清单就变了。
        print(f"\n{SUB}\n【节点 1 补充】同一份分数 + 不同岗位标准 = 不同短板清单\n{SUB}")
        score_only = [
            {"dimension": g["dimension"], "current": g["current"]} for g in GAPS
        ]  # 故意只给分数，不给目标分与权重——标准必须来自岗位模型
        compared_any = False
        for title in ("数据分析师", "AI产品经理"):
            position = (
                await db.execute(select(Position).where(Position.title == title))
            ).scalar_one_or_none()
            if position is None:
                continue
            compared_any = True
            compare_service = TrainingService(db)
            compared = await compare_service.diagnose(
                talent.id, gaps=[GapItem(**g) for g in score_only], position_id=position.id
            )
            print(
                f"  按「{title}」标准（来源={compare_service._requirement_source}"
                f"，岗位 id={position.id} v{compare_service._requirement_version}）："
                f"{len(compared)} 项短板"
            )
            for g in compared[:4]:
                print(
                    f"    {g['priority']}  {g['dimension']:<10} {g['current']}→{g['required']}"
                    f"  权重 {g['weight']}  缺口 {g['delta']:>4.0f}"
                )
        if not compared_any:
            print("  （库里没有岗位能力模型，先跑：python scripts/seed_position_requirements.py）")

        # ---------------------------------------------------------- 节点 2~4
        print(f"\n{SUB}\n【节点 2】方案排期  【节点 3】课程匹配  【节点 4】落库推送\n{SUB}")
        req = TrainingPlanGenerateRequest(
            talent_id=talent.id,
            talent_name=talent.name,
            assessment_id=None,
            gaps=[GapItem(**g) for g in GAPS],
            max_weeks=4,
            max_hours_per_week=8,
        )
        plan = await service.generate_plan(req, operator_id=None)

        print(f"  计划：{plan['name']}（id={plan['id']}，生成方式={plan['generated_by']}）")
        print(f"  {'周次':<6}{'能力维度':<12}{'学时':<8}{'课程':<26}{'来源'}")
        for item in plan["items"]:
            source = "平台课程库" if item["is_platform"] else "降级外部建议"
            print(
                f"  W{item['week']:<5}{item['dimension']:<12}"
                f"{item['hours']:<8}{str(item.get('title') or '—'):<26}{source}"
            )
        print(f"  合计 {plan['total_hours']}h / {plan['total_weeks']} 周"
              f"｜课程来自库 {plan['library_rate']:.0%}｜降级 {plan['external_count']} 项")
        print(f"  考核节点：第 {plan['assessment']['week']} 周 {plan['assessment']['type']}"
              f"，及格 {plan['assessment']['pass_score']} 分")
        print(f"  摘要：{plan['summary']}")

        # ---------------------------------------------------------- 节点 5
        print(f"\n{SUB}\n【节点 5】学习进度跟踪与滞后预警\n{SUB}")
        # 演示构造：周期已过 60%，但只学了 4 小时（真实场景由定时任务扫描）
        plan_row = (
            await db.execute(select(TrainingPlan).where(TrainingPlan.id == plan["id"]))
        ).scalar_one()
        plan_row.start_date = date.today() - timedelta(days=17)
        plan_row.end_date = date.today() + timedelta(days=11)
        await db.flush()

        first = next((i for i in plan["items"] if i["course_id"]), None)
        if first:
            await service.update_progress(
                LearningRecordUpdate(
                    plan_id=plan["id"],
                    course_id=first["course_id"],
                    talent_id=talent.id,
                    progress=50,
                    study_minutes=int(first["hours"] * 60 * 0.5),
                )
            )
            progress = await service.get_progress(plan["id"])
            elapsed = service._elapsed_ratio(plan_row.start_date, plan_row.end_date)
            print(f"  周期 {plan_row.start_date} → {plan_row.end_date}（时间已过 {elapsed:.0%}）")
            print(f"  应完成 {progress['expected_h']}h｜已完成 {progress['completed_h']}h"
                  f"｜完成率 {progress['completion']:.0%}")
            print(f"  落后 {progress['lag_ratio']:.0%} → 判定：{progress['alert']}")
            if progress.get("event"):
                print(f"  已发事件：{progress['event']['type']}")
        else:
            print("  课程全部降级，跳过进度演示")

        # ---------------------------------------------------------- 节点 6
        print(f"\n{SUB}\n【节点 6】培训效果考核与复测回流\n{SUB}")
        result = await service.submit_score(
            ScoreSubmitRequest(plan_id=plan["id"], talent_id=talent.id, score=88, auto_retest=True)
        )
        print(f"  结课考核 {result['score']:.0f} 分 → {result['assessment']}"
              f"（计划状态 {result['plan_status']}）")
        print(f"  回流事件：{[e['type'] for e in result['events']] or '无'}")

        await db.flush()
        await event_bus.drain(timeout=2)

        # ---------------------------------------------------------- 通过率统计
        print(f"\n{SUB}\n【节点 6 补充】考核通过率统计（完成率看过程，通过率看结果）\n{SUB}")
        stats = await service.pass_rate(plan_id=plan["id"])
        print(
            f"  本计划：{stats['total_examined']} 条考核记录｜通过 {stats['passed']}"
            f"｜通过率 {stats['pass_rate']:.0%}｜平均分 {stats['avg_score']}"
        )
        for row in stats["by_course"][:5]:
            print(
                f"    {str(row['title'] or '(未命名)'):<24} 考 {row['examined']}"
                f"  过 {row['passed']}  通过率 {row['pass_rate']:.0%}"
            )

        # ---------------------------------------------------------- 事件汇总
        print(f"\n{SUB}\n本模块发出的业务事件（证明与其他 Agent 靠事件解耦）\n{SUB}")
        if _captured:
            for payload in _captured:
                detail = payload.get("level") or payload.get("assessment") or ""
                print(f"  · {payload.get('type', '?'):<28} {detail}")
        else:
            print("  （无事件：可能全部订阅在发布之后注册）")

        print(f"\n{LINE}")
        print("✅ 六节点全部跑通。可直接用 POST /api/v1/trainings/plans 复现同一流程。")
        print(LINE)

    # 显式释放连接池，避免解释器退出时打「Event loop is closed」红字
    await dispose_engine(get_engine())


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
