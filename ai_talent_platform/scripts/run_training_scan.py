"""
脚本：scripts/run_training_scan.py
职责：节点 5「学习进度跟踪」的定时任务入口（供 cron / 任务调度器按天调用）。

标签：[Dify组] [P1]（节点5 学习进度扫描定时任务）

所属模块：智能培训赋能 Agent（培训模块）

为什么单独做成脚本，而不是写在 Agent 图里：
    培训周期是 4 周，进度跟踪要天天扫。如果写成图里的 while 循环，
    进程一重启、一发布，扫描状态就丢了；而且一个长驻循环会占住工作进程。
    所以正确形态是「无状态的一次性扫描」+ 外部定时器，每天跑一次即可。

用法：
    python scripts/run_training_scan.py              # 扫描并发事件（生产/演示）
    python scripts/run_training_scan.py --dry-run    # 只算不发事件（联调/压测）
    python scripts/run_training_scan.py --talent 2   # 只看某个人

等价 HTTP 接口（调度器不方便跑脚本时用）：
    POST /api/v1/trainings/lag-scan    （需 training:manage 权限）

cron 示例（每天 08:30）：
    30 8 * * * cd /path/to/project && /path/to/python scripts/run_training_scan.py >> logs/training_scan.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.logging_config import setup_logging  # noqa: E402
from app.infrastructure.database.mysql import dispose_engine  # noqa: E402
from app.infrastructure.database.session import get_engine, session_scope  # noqa: E402
from app.infrastructure.messaging.event_bus import event_bus  # noqa: E402
from app.services.training_service import TrainingService  # noqa: E402

_WATCHED = ("training.lag.alert", "training.lag.remind")


async def scan(*, dry_run: bool = False, talent_id: int | None = None) -> int:
    captured: list[dict] = []
    for name in _WATCHED:
        event_bus.subscribe(name, captured.append)

    async with session_scope() as db:
        result = await TrainingService(db).scan_lag(publish=not dry_run)

    await event_bus.drain(timeout=3)

    alerts = result["alerts"]
    if talent_id is not None:
        alerts = [a for a in alerts if int(a.get("talent_id") or 0) == talent_id]

    mode = "DRY-RUN（不发送事件）" if dry_run else "正式执行"
    print(f"[培训进度扫描] {mode}")
    print(f"  扫描进行中计划：{result['scanned']} 个")
    print(f"  HR 预警：{result['warned']} 个｜提醒：{result['reminded']} 个")

    if not alerts:
        print("  无需跟进的计划 ✅")
        return 0

    print(f"  {'计划':<8}{'人才':<8}{'完成率':<10}{'落后':<10}{'判定'}")
    for alert in alerts:
        print(
            f"  {alert['plan_id']:<8}{alert['talent_id']:<8}"
            f"{alert['completion']:<10.0%}{alert['lag_ratio']:<10.0%}{alert['alert']}"
        )
    if captured:
        print(f"  已发事件：{sorted({e.get('type') for e in captured})}")
    return len(alerts)


async def _run(args: argparse.Namespace) -> int:
    """包一层：无论成功或提前返回，都在同一事件循环内释放连接池。"""
    try:
        return await scan(dry_run=args.dry_run, talent_id=args.talent)
    finally:
        await dispose_engine(get_engine())


def main() -> None:
    parser = argparse.ArgumentParser(description="培训进度扫描（节点 5）")
    parser.add_argument("--dry-run", action="store_true", help="只计算不发送事件")
    parser.add_argument("--talent", type=int, default=None, help="只看指定人才 ID")
    args = parser.parse_args()

    setup_logging()
    count = asyncio.run(_run(args))
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
