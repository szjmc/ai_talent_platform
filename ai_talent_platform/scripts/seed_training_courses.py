"""
脚本：scripts/seed_training_courses.py
职责：初始化培训模块的课程库演示数据（41 门 / 8 个能力维度 / 每维度覆盖 L1~L4）。

标签：[Dify组] [P2]（课程库演示数据初始化）

所属模块：智能培训赋能 Agent（培训模块）

使用方式：
    python scripts/seed_training_courses.py

为什么必须有这个脚本：
    节点 3「课程智能匹配」的铁律是「课程 100% 来自课程库」。
    课程库为空时不会报错，只会整份方案静默降级为「外部学习方向建议」——
    演示效果会从「排出 4 周课表」变成「全部暂无课程」。所以演示前先跑本脚本。

数据设计要点（与 training_logic 的匹配规则严格对齐）：
    1. category 与能力维度**一字不差**（维度名带空格/多两个字都会导致静默降级）；
    2. level 用 L1~L4，且**每个维度四个层级都必须有课**：
       target_level() 按 required 分三档（≥85→L4、≥75→L3、其余→L2），
       level_slack=1 决定贴合区间。矩阵缺格时，某个缺口就永远匹配不到课、
       整份方案被判定为「库内暂无」——这不是数据缺失，是演示事故。
       本脚本末尾会按「维度 × 层级」打印矩阵，缺任何一格都会醒目告警；
    3. duration_hours 取 4~8 学时，与 estimate_hours() 的预估量级一致，避免「8 学时的周
       却挂了 24 学时的课」这种答辩时说不通的组合；
    4. 同一维度不放重名课程，避免「学了同一门两次」。

幂等：按 title 判断，已存在则更新可变字段（category/level/duration_hours/skill_tags），
      不重复插入；可反复执行。
注意：仅用于开发与演示环境。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.infrastructure.database.mysql import dispose_engine  # noqa: E402
from app.infrastructure.database.session import get_engine, get_session_factory  # noqa: E402
from app.models.training import Course  # noqa: E402

LEVELS = ("L1", "L2", "L3", "L4")

# ------------------------------------------------------------------ 课程库
# 字段顺序：title / category(能力维度) / level / duration_hours / description / skill_tags
COURSES: list[dict] = [
    # ============================================== 需求分析（6）
    {
        "title": "需求采集与文档规范",
        "category": "需求分析",
        "level": "L1",
        "duration_hours": 4,
        "description": "需求来源识别、原始诉求记录与标准化需求文档格式。",
        "skill_tags": ["需求分析", "需求文档"],
    },
    {
        "title": "需求分析与用户故事拆解",
        "category": "需求分析",
        "level": "L2",
        "duration_hours": 6,
        "description": "从业务目标拆到可用研发的用户故事，含验收标准写法。",
        "skill_tags": ["需求分析", "用户故事", "PRD"],
    },
    {
        "title": "需求评审与验收机制",
        "category": "需求分析",
        "level": "L2",
        "duration_hours": 4,
        "description": "评审会怎么开、验收集怎么定，减少上线后的反复扯皮。",
        "skill_tags": ["需求分析", "需求评审", "验收"],
    },
    {
        "title": "需求优先级与 MVP 设计",
        "category": "需求分析",
        "level": "L3",
        "duration_hours": 8,
        "description": "KANO / 价值-成本矩阵，把一堆需求砍到能上线的范围。",
        "skill_tags": ["需求分析", "优先级", "MVP"],
    },
    {
        "title": "业务流程图与领域建模",
        "category": "需求分析",
        "level": "L3",
        "duration_hours": 6,
        "description": "用流程图与领域模型把模糊业务讲清楚，落到可开发的结构。",
        "skill_tags": ["需求分析", "流程建模", "领域模型"],
    },
    {
        "title": "高阶需求架构与变更治理",
        "category": "需求分析",
        "level": "L4",
        "duration_hours": 8,
        "description": "多产品线需求分层、变更影响面评估与评审机制。",
        "skill_tags": ["需求分析", "需求治理"],
    },
    # ============================================== 数据分析（6）
    {
        "title": "Excel 进阶与数据透视",
        "category": "数据分析",
        "level": "L1",
        "duration_hours": 4,
        "description": "透视表、函数组合与基础图表，适合零基础起步。",
        "skill_tags": ["数据分析", "Excel"],
    },
    {
        "title": "业务数据看板与可视化",
        "category": "数据分析",
        "level": "L2",
        "duration_hours": 8,
        "description": "指标体系搭建、看板设计与异常波动的读图方法。",
        "skill_tags": ["数据分析", "可视化", "指标体系"],
    },
    {
        "title": "指标体系搭建实战",
        "category": "数据分析",
        "level": "L2",
        "duration_hours": 6,
        "description": "从北极星指标拆到可采集的过程指标，避免口径打架。",
        "skill_tags": ["数据分析", "指标体系"],
    },
    {
        "title": "业务数据分析方法与归因",
        "category": "数据分析",
        "level": "L3",
        "duration_hours": 8,
        "description": "漏斗 / 留存 / AB 实验，以及指标下跌的三层归因法。",
        "skill_tags": ["数据分析", "归因", "AB测试"],
    },
    {
        "title": "数据叙事与汇报表达",
        "category": "数据分析",
        "level": "L3",
        "duration_hours": 4,
        "description": "把分析结论讲成决策建议：结论先行、证据分层、口径可追溯。",
        "skill_tags": ["数据分析", "数据叙事"],
    },
    {
        "title": "增长分析与实验设计",
        "category": "数据分析",
        "level": "L4",
        "duration_hours": 8,
        "description": "假设驱动增长、实验分流与显著性判定、长期效应评估。",
        "skill_tags": ["数据分析", "增长", "实验设计"],
    },
    # ============================================== SQL 与数据库（5）
    {
        "title": "SQL 入门到熟练",
        "category": "SQL与数据库",
        "level": "L1",
        "duration_hours": 8,
        "description": "单表查询、聚合、多表关联与子查询，配套练习库。",
        "skill_tags": ["SQL与数据库", "SQL"],
    },
    {
        "title": "SQL 查询优化与索引原理",
        "category": "SQL与数据库",
        "level": "L2",
        "duration_hours": 6,
        "description": "执行计划解读、索引设计、慢查询改写。",
        "skill_tags": ["SQL与数据库", "SQL", "性能优化"],
    },
    {
        "title": "Python 数据处理入门",
        "category": "SQL与数据库",
        "level": "L2",
        "duration_hours": 6,
        "description": "pandas 读写与清洗、与 SQL 的分工边界。",
        "skill_tags": ["SQL与数据库", "Python", "pandas"],
    },
    {
        "title": "数据建模与数仓分层基础",
        "category": "SQL与数据库",
        "level": "L3",
        "duration_hours": 8,
        "description": "维度建模、ODS/DWD/DWS 分层与指标口径治理。",
        "skill_tags": ["SQL与数据库", "数据建模"],
    },
    {
        "title": "湖仓架构与数据治理",
        "category": "SQL与数据库",
        "level": "L4",
        "duration_hours": 8,
        "description": "湖仓一体选型、元数据与血缘、数据质量与权限治理。",
        "skill_tags": ["SQL与数据库", "数据治理", "湖仓"],
    },
    # ============================================== 项目管理（5）
    {
        "title": "项目管理基础与计划编制",
        "category": "项目管理",
        "level": "L1",
        "duration_hours": 4,
        "description": "WBS 拆解、工期估算与计划表写法。",
        "skill_tags": ["项目管理", "计划编制"],
    },
    {
        "title": "进度管控与里程碑管理",
        "category": "项目管理",
        "level": "L2",
        "duration_hours": 6,
        "description": "里程碑设置、周报机制与偏差纠正动作。",
        "skill_tags": ["项目管理", "进度管控"],
    },
    {
        "title": "敏捷与 Scrum 实战",
        "category": "项目管理",
        "level": "L3",
        "duration_hours": 4,
        "description": "迭代节奏、故事点估算、站会与回顾会怎么开才不流于形式。",
        "skill_tags": ["项目管理", "敏捷", "Scrum"],
    },
    {
        "title": "多项目并行与资源调度",
        "category": "项目管理",
        "level": "L3",
        "duration_hours": 6,
        "description": "资源冲突识别、优先级仲裁与跨项目排期。",
        "skill_tags": ["项目管理", "资源调度"],
    },
    {
        "title": "项目风险与干系人管理",
        "category": "项目管理",
        "level": "L4",
        "duration_hours": 6,
        "description": "风险登记册、关键路径与跨部门干系人对齐策略。",
        "skill_tags": ["项目管理", "风险管理"],
    },
    # ============================================== 沟通表达（5）
    {
        "title": "职场书面表达与邮件规范",
        "category": "沟通表达",
        "level": "L1",
        "duration_hours": 4,
        "description": "邮件结构、纪要写法与跨时区协作的表达习惯。",
        "skill_tags": ["沟通表达", "书面表达"],
    },
    {
        "title": "结构化表达与向上汇报",
        "category": "沟通表达",
        "level": "L2",
        "duration_hours": 4,
        "description": "结论先行、MECE 拆解，把复杂事讲成三句话。",
        "skill_tags": ["沟通表达", "汇报"],
    },
    {
        "title": "跨部门沟通与冲突处理",
        "category": "沟通表达",
        "level": "L3",
        "duration_hours": 6,
        "description": "利益对齐、非暴力沟通与僵局破解的实操话术。",
        "skill_tags": ["沟通表达", "跨部门协作"],
    },
    {
        "title": "公众演讲与方案宣讲",
        "category": "沟通表达",
        "level": "L3",
        "duration_hours": 4,
        "description": "方案宣讲的叙事结构、幻灯节奏与现场问答应对。",
        "skill_tags": ["沟通表达", "演讲"],
    },
    {
        "title": "关键对话与谈判技巧",
        "category": "沟通表达",
        "level": "L4",
        "duration_hours": 6,
        "description": "高冲突场景下的对话框架、BATNA 与让步策略。",
        "skill_tags": ["沟通表达", "谈判"],
    },
    # ============================================== 产品设计（5）
    {
        "title": "产品设计入门与用户体验常识",
        "category": "产品设计",
        "level": "L1",
        "duration_hours": 4,
        "description": "用户体验五要素、常见交互范式与设计评审视角。",
        "skill_tags": ["产品设计", "用户体验"],
    },
    {
        "title": "产品原型与交互设计基础",
        "category": "产品设计",
        "level": "L2",
        "duration_hours": 8,
        "description": "从流程图到高保真原型，交互四原则与可用性自检。",
        "skill_tags": ["产品设计", "原型", "交互"],
    },
    {
        "title": "PRD 写作与需求评审",
        "category": "产品设计",
        "level": "L3",
        "duration_hours": 6,
        "description": "一份研发愿意照着做的 PRD 该有什么、不该有什么。",
        "skill_tags": ["产品设计", "PRD"],
    },
    {
        "title": "设计系统与组件化实践",
        "category": "产品设计",
        "level": "L3",
        "duration_hours": 6,
        "description": "设计规范沉淀、组件库建设与跨端一致性维护。",
        "skill_tags": ["产品设计", "设计系统"],
    },
    {
        "title": "产品战略与路线图规划",
        "category": "产品设计",
        "level": "L4",
        "duration_hours": 8,
        "description": "市场与竞品判断、产品定位、多版本路线图与取舍。",
        "skill_tags": ["产品设计", "产品战略"],
    },
    # ============================================== 用户研究（5）
    {
        "title": "用户研究入门与问卷设计",
        "category": "用户研究",
        "level": "L1",
        "duration_hours": 4,
        "description": "定性定量分工、问卷题型设计与常见偏差。",
        "skill_tags": ["用户研究", "问卷"],
    },
    {
        "title": "用户访谈与调研方法",
        "category": "用户研究",
        "level": "L2",
        "duration_hours": 6,
        "description": "访谈提纲设计、避免诱导性提问、样本与结论边界。",
        "skill_tags": ["用户研究", "用户访谈"],
    },
    {
        "title": "用户画像与需求洞察",
        "category": "用户研究",
        "level": "L3",
        "duration_hours": 8,
        "description": "从行为数据与访谈记录里提炼可落地的用户画像。",
        "skill_tags": ["用户研究", "用户画像"],
    },
    {
        "title": "可用性测试与行为分析",
        "category": "用户研究",
        "level": "L3",
        "duration_hours": 6,
        "description": "任务式可用性测试设计、行为埋点与问题分级。",
        "skill_tags": ["用户研究", "可用性测试"],
    },
    {
        "title": "研究体系与洞察体系建设",
        "category": "用户研究",
        "level": "L4",
        "duration_hours": 8,
        "description": "研究资产沉淀、洞察到决策的通路与组织机制。",
        "skill_tags": ["用户研究", "洞察体系"],
    },
    # ============================================== 团队协作（4）
    {
        "title": "团队协作与高效会议",
        "category": "团队协作",
        "level": "L1",
        "duration_hours": 4,
        "description": "会议前置材料、议题收敛与行动项闭环。",
        "skill_tags": ["团队协作", "会议管理"],
    },
    {
        "title": "协作机制与团队冲突化解",
        "category": "团队协作",
        "level": "L2",
        "duration_hours": 6,
        "description": "RACI 分工、接口人机制与冲突升级路径。",
        "skill_tags": ["团队协作", "RACI"],
    },
    {
        "title": "高绩效团队建设与激励",
        "category": "团队协作",
        "level": "L3",
        "duration_hours": 6,
        "description": "目标对齐、反馈机制与非物质激励的落地做法。",
        "skill_tags": ["团队协作", "团队建设"],
    },
    {
        "title": "组织协作与跨团队治理",
        "category": "团队协作",
        "level": "L4",
        "duration_hours": 8,
        "description": "跨团队目标共识、协作协议与组织级复盘机制。",
        "skill_tags": ["团队协作", "组织治理"],
    },
]


def _matrix(rows: list[Course]) -> dict[str, dict[str, int]]:
    """按「维度 × 层级」统计课程数（用于发现匹配矩阵的缺格）。"""
    grid: dict[str, dict[str, int]] = {}
    for row in rows:
        category = row.category or "(未分类)"
        level = row.level or "(未分级)"
        grid.setdefault(category, {})
        grid[category][level] = grid[category].get(level, 0) + 1
    return grid


async def seed() -> None:
    factory = get_session_factory()
    created = updated = skipped = 0
    async with factory() as session:
        for item in COURSES:
            row = (
                await session.execute(select(Course).where(Course.title == item["title"]))
            ).scalar_one_or_none()
            payload = {
                "category": item["category"],
                "level": item["level"],
                "duration_hours": item["duration_hours"],
                "description": item["description"],
                "skill_tags": item["skill_tags"],
                "is_active": True,
            }
            if row is None:
                session.add(Course(title=item["title"], **payload))
                created += 1
            else:
                changed = False
                for key, value in payload.items():
                    current = getattr(row, key)
                    # duration_hours 在库里是 Numeric/Decimal，直接和 int 比永远不等，
                    # 会导致每次执行都误报「已更新」，统一按数值比较。
                    same = float(current) == float(value) if key == "duration_hours" and current is not None else current == value
                    if not same:
                        setattr(row, key, value)
                        changed = True
                if changed:
                    updated += 1
                else:
                    skipped += 1
        await session.commit()

        rows = (
            await session.execute(select(Course).where(Course.is_deleted.is_(False)))
        ).scalars().all()
        grid = _matrix(list(rows))

    print("培训课程库初始化完成")
    print(f"   新增 {created} 门 / 更新 {updated} 门 / 无变化 {skipped} 门")
    print(f"   课程库合计 {len(rows)} 门，覆盖 {len(grid)} 个能力维度")

    header = "   " + f"{'维度':<12}" + "".join(f"{level:>5}" for level in LEVELS) + f"{'合计':>7}"
    print("\n   维度 × 层级矩阵（缺格会导致该层级缺口永远匹配不到课）：")
    print(header)
    print("   " + "-" * (len(header) - 3))
    missing: list[str] = []
    for category in sorted(grid):
        counts = [grid[category].get(level, 0) for level in LEVELS]
        total = sum(counts)
        print("   " + f"{category:<12}" + "".join(f"{count:>5}" for count in counts) + f"{total:>7}")
        for level, count in zip(LEVELS, counts):
            if count == 0:
                missing.append(f"{category}/{level}")

    if missing:
        print("\n   [警告] 以下「维度 × 层级」没有课程，相应缺口会被判定为「库内暂无」：")
        for item in missing:
            print(f"     - {item}")
    else:
        print("\n   8 个维度 × 4 个层级全部有课，节点 3 不会因缺课而静默降级")

    print("\n下一步：python scripts/demo_training.py   # 端到端跑一遍六个节点")
    # 显式释放连接池：否则解释器退出时 aiomysql 在已关闭的事件循环里关闭连接会打红字
    await dispose_engine(get_engine())


if __name__ == "__main__":
    setup_logging()
    asyncio.run(seed())
