"""
脚本：scripts/seed_position_requirements.py
职责：初始化「岗位能力模型」演示数据（positions + position_requirements 两张表）。

标签：[Dify组] [P2]（岗位能力模型演示数据初始化）

为什么需要这个脚本：
    培训模块的节点 1 需要「目标分 required」和「维度权重 weight」才能判断什么叫短板。
    这两样不属于测评结果，属于**岗位标准**。库里 position_requirements 表本来就存在，
    但没有数据 —— 没有标准，诊断就只能退回默认值（80 分 / 0.2 权重），
    「千人千面」就会退化成「千人一面」。本脚本把标准补齐。

数据设计（与课程库的 8 个能力维度严格同名，否则会静默降级）：
    维度名必须和 courses.category 一字不差：需求分析 / 产品设计 / 用户研究 /
    数据分析 / SQL与数据库 / 项目管理 / 沟通表达 / 团队协作

幂等策略：
    - 岗位按 title 查重，已存在则复用，不重复建；
    - 能力模型按 (position_id, version) 唯一，已存在则就地更新 dimensions/weights；
    - 带 --bind 时，把演示人才（默认张伟）的 current_position 改成岗位名，
      用于验证「不传 position_id 也能自动匹配岗位模型」这条兜底链路。

使用方式：
    python scripts/seed_position_requirements.py            # 只建岗位与能力模型
    python scripts/seed_position_requirements.py --bind      # 顺带把张伟绑到「数据分析师」
    python scripts/seed_position_requirements.py --list      # 只看当前库里有什么

注意：仅用于开发与演示环境。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.infrastructure.database.mysql import dispose_engine  # noqa: E402
from app.infrastructure.database.session import get_engine, get_session_factory  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.position import Position, PositionRequirement  # noqa: E402
from app.models.talent import Talent  # noqa: E402

# 岗位必须挂在部门下（positions.organization_id 是物理外键），
# 而演示库里 organizations 可能是空的，所以先确保有一个根部门。
DEMO_ORG_CODE = "DEMO_HQ"
DEMO_ORG_NAME = "演示事业部"

# ------------------------------------------------------------------ 岗位与能力模型
# dimensions: {"维度": {"required": 目标分, "weight": 权重}}
#   - required 是「岗位要求达到的分数」，不是当前分；
#   - weight 合计为 1.0，权重 >= 0.25 的维度会被诊断直接抬到 P0（关键岗位维度）。
POSITIONS: list[dict] = [
    {
        "title": "数据分析师",
        "code": "DATA_ANALYST",
        "description": "负责业务数据的采集、清洗、建模与归因分析，输出决策依据。",
        "dimensions": {
            "数据分析": {"required": 88, "weight": 0.30},
            "SQL与数据库": {"required": 85, "weight": 0.25},
            "需求分析": {"required": 78, "weight": 0.15},
            "沟通表达": {"required": 78, "weight": 0.15},
            "项目管理": {"required": 72, "weight": 0.10},
            "团队协作": {"required": 75, "weight": 0.05},
        },
    },
    {
        "title": "AI产品经理",
        "code": "AI_PM",
        "description": "负责 AI 产品的需求定义、方案设计与跨团队推进。",
        "dimensions": {
            "需求分析": {"required": 85, "weight": 0.25},
            "产品设计": {"required": 82, "weight": 0.25},
            "用户研究": {"required": 78, "weight": 0.15},
            "数据分析": {"required": 80, "weight": 0.15},
            "项目管理": {"required": 75, "weight": 0.12},
            "沟通表达": {"required": 78, "weight": 0.08},
        },
    },
    {
        "title": "后端开发工程师",
        "code": "BACKEND_DEV",
        "description": "负责服务端接口、数据存储与性能优化。",
        "dimensions": {
            "SQL与数据库": {"required": 90, "weight": 0.30},
            "项目管理": {"required": 75, "weight": 0.20},
            "团队协作": {"required": 80, "weight": 0.20},
            "数据分析": {"required": 75, "weight": 0.15},
            "沟通表达": {"required": 72, "weight": 0.15},
        },
    },
]

# --bind 时绑定的人才与目标岗位
DEFAULT_TALENT_NAME = "张伟"
DEFAULT_BIND_POSITION = "数据分析师"


async def _ensure_org(db) -> Organization:
    """确保存在一个可用于挂岗位的根部门（positions.organization_id 是物理外键）。"""
    org = (
        await db.execute(
            select(Organization).where(
                Organization.code == DEMO_ORG_CODE, Organization.is_deleted.is_(False)
            )
        )
    ).scalar_one_or_none()
    if org is not None:
        return org

    # 库里若已有任何根部门，直接复用，避免多造一个孤立的"演示事业部"
    existing = (
        await db.execute(
            select(Organization)
            .where(Organization.parent_id.is_(None), Organization.is_deleted.is_(False))
            .order_by(Organization.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    org = Organization(
        code=DEMO_ORG_CODE,
        name=DEMO_ORG_NAME,
        level=1,
        path="/",
        sort=0,
        is_active=True,
        remark="岗位能力模型演示用的根部门（由 seed 脚本自动创建）",
    )
    db.add(org)
    await db.flush()
    print(f"  + 新建根部门 {DEMO_ORG_NAME} (id={org.id})")
    return org


async def seed(bind: bool, list_only: bool) -> None:
    factory = get_session_factory()
    created = updated = 0

    async with factory() as db:
        # ---------------------------------------------- --list：只看现状
        if list_only:
            positions = (
                (await db.execute(select(Position).where(Position.is_deleted.is_(False)))).scalars().all()
            )
            if not positions:
                print("岗位表为空。先执行：python scripts/seed_position_requirements.py")
                return
            for pos in positions:
                req = (
                    await db.execute(
                        select(PositionRequirement)
                        .where(
                            PositionRequirement.position_id == pos.id,
                            PositionRequirement.is_deleted.is_(False),
                        )
                        .order_by(PositionRequirement.version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if req is None:
                    print(f"- {pos.title:<12} (id={pos.id}) 无能力模型")
                    continue
                dims = req.dimensions or {}
                weights = req.weights or {}
                print(f"- {pos.title:<12} (id={pos.id}) v{req.version} 共 {len(dims)} 个维度")
                for name, spec in dims.items():
                    required = spec.get("required") if isinstance(spec, dict) else spec
                    weight = weights.get(name) or (spec.get("weight") if isinstance(spec, dict) else None)
                    print(f"    {name:<12} 目标 {required}  权重 {weight}")
            return

        # ---------------------------------------------- 建岗位 + 能力模型
        org = await _ensure_org(db)
        for item in POSITIONS:
            pos = (
                await db.execute(
                    select(Position).where(
                        Position.title == item["title"], Position.is_deleted.is_(False)
                    )
                )
            ).scalar_one_or_none()

            if pos is None:
                pos = Position(
                    title=item["title"],
                    code=item["code"],
                    organization_id=org.id,
                    level="middle",
                    status="open",
                    description=item["description"],
                    skill_tags=list(item["dimensions"].keys()),
                )
                db.add(pos)
                await db.flush()  # 拿到自增 id
                created += 1
                print(f"  + 新建岗位 {item['title']} (id={pos.id})")
            else:
                print(f"  = 岗位已存在 {item['title']} (id={pos.id})")

            # 拆成 dimensions / weights 两个字段存：与表结构和其它模块的读法保持一致
            dims = {name: {"required": spec["required"]} for name, spec in item["dimensions"].items()}
            weights = {name: spec["weight"] for name, spec in item["dimensions"].items()}
            total_weight = round(sum(weights.values()), 4)
            if abs(total_weight - 1.0) > 0.001:
                raise SystemExit(f"权重合计必须为 1.0，当前 {item['title']} = {total_weight}")

            req = (
                await db.execute(
                    select(PositionRequirement).where(
                        PositionRequirement.position_id == pos.id,
                        PositionRequirement.version == 1,
                        PositionRequirement.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()

            if req is None:
                db.add(
                    PositionRequirement(
                        position_id=pos.id,
                        version=1,
                        dimensions=dims,
                        weights=weights,
                        source="manual",
                    )
                )
                updated += 1
                print(f"    + 能力模型 v1：{len(dims)} 个维度，权重合计 {total_weight}")
            else:
                changed = False
                if req.dimensions != dims:
                    req.dimensions = dims
                    changed = True
                if req.weights != weights:
                    req.weights = weights
                    changed = True
                print(f"    {'~ 能力模型已更新' if changed else '= 能力模型无变化'} v{req.version}")
                if changed:
                    updated += 1

        # ---------------------------------------------- 可选：把人才绑到岗位
        if bind:
            talent = (
                await db.execute(
                    select(Talent).where(
                        Talent.name == DEFAULT_TALENT_NAME, Talent.is_deleted.is_(False)
                    )
                )
            ).scalar_one_or_none()
            if talent is None:
                print(f"\n  ! 未找到演示人才「{DEFAULT_TALENT_NAME}」，跳过绑定")
            else:
                old = talent.current_position
                talent.current_position = DEFAULT_BIND_POSITION
                print(
                    f"\n  ~ 绑定人才：{DEFAULT_TALENT_NAME}(id={talent.id}) "
                    f"现职位 {old or '(空)'} → {DEFAULT_BIND_POSITION}"
                )

        await db.commit()

    print("\n✅ 岗位能力模型初始化完成")
    print(f"   新建岗位 {created} 个 / 写入或更新能力模型 {updated} 份")
    if bind:
        print(f"   已把「{DEFAULT_TALENT_NAME}」的现职位设为「{DEFAULT_BIND_POSITION}」")
        print("   → 生成计划时不传 position_id 也会自动匹配到该岗位模型")
    print("\n下一步：python scripts/demo_training.py   # 观察诊断是否采用了岗位标准")
    await dispose_engine(get_engine())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化岗位能力模型演示数据")
    parser.add_argument("--bind", action="store_true", help=f"把演示人才的现职位绑到「{DEFAULT_BIND_POSITION}」")
    parser.add_argument("--list", action="store_true", help="只列出当前库里的岗位与能力模型")
    args = parser.parse_args()

    setup_logging()
    asyncio.run(seed(bind=args.bind, list_only=args.list))
