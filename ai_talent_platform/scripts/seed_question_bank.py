"""
脚本：scripts/seed_question_bank.py
职责：生成演示题库（question_bank_questions）+ 演示测评（assessment_questions 快照）。

标签：[Vanna组] [P2]

为什么需要这个脚本：
    1. 「智能测评 / 邀约笔试」链路从数据库题库组卷（retrieve_questions_rag），
       空库时创建测评直接报「题库为空」；邀约笔试从 assessment_questions 抽题，
       没有任何测评记录时创建邀约同样报「题库为空」——错误信息里指向的
       scripts/seed_question_bank.py 此前并不存在（承诺的脚本没落地）。
    2. 本脚本一次性补齐两层：
       - 题库层：内置 30 道覆盖 python / java / frontend / database / devops / ai
         的演示题（single / multiple / text 三种组卷可用题型），幂等 upsert；
       - 测评层：若无任何 assessment_questions，则用题库题目生成一份
         「演示测评（通用能力）」，让邀约笔试开箱可用。

用法：
    python scripts/seed_question_bank.py            # 题库 + 演示测评
    python scripts/seed_question_bank.py --no-assessment   # 只导题库
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.models.assessment import Assessment, AssessmentQuestion  # noqa: E402
from app.models.talent import Talent  # noqa: E402
from app.repositories.question_bank_repository import QuestionBankRepository  # noqa: E402
from app.schemas.question_bank import QuestionImport  # noqa: E402

BANK_ID = 1  # 演示题库 ID


def _opt(key: str, text: str) -> dict:
    return {"key": key, "text": text}


def build_demo_questions() -> list[QuestionImport]:
    """内置演示题库：6 个技术方向 × 三种组卷题型 × 三档难度。"""
    raw: list[dict] = [
        # ---------------- Python ----------------
        {"external_id": "demo-py-s-001", "technology": "python", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["基础语法"],
         "stem": "Python 中用于定义匿名函数的关键字是？",
         "options": [_opt("A", "def"), _opt("B", "lambda"), _opt("C", "func"), _opt("D", "async")],
         "answer": "B", "analysis": "lambda 用于创建匿名函数。"},
        {"external_id": "demo-py-s-002", "technology": "python", "difficulty": "intermediate",
         "question_type": "single", "knowledge_points": ["数据结构"],
         "stem": "下列哪种容器保证元素唯一且无序？",
         "options": [_opt("A", "list"), _opt("B", "tuple"), _opt("C", "set"), _opt("D", "dict.values()")],
         "answer": "C", "analysis": "set 是无序不重复集合。"},
        {"external_id": "demo-py-s-003", "technology": "python", "difficulty": "advanced",
         "question_type": "single", "knowledge_points": ["并发"],
         "stem": "asyncio 中让出事件循环控制权的正确写法是？",
         "options": [_opt("A", "time.sleep(1)"), _opt("B", "await asyncio.sleep(1)"),
                     _opt("C", "threading.sleep(1)"), _opt("D", "await time.sleep(1)")],
         "answer": "B", "analysis": "time.sleep 会阻塞整个事件循环。"},
        {"external_id": "demo-py-m-001", "technology": "python", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["内置函数"],
         "stem": "以下哪些是 Python 的内置数据结构？",
         "options": [_opt("A", "list"), _opt("B", "deque"), _opt("C", "ndarray"), _opt("D", "dict")],
         "answer": ["A", "D"], "analysis": "deque 在 collections，ndarray 属于 numpy。"},
        {"external_id": "demo-py-m-002", "technology": "python", "difficulty": "advanced",
         "question_type": "multiple", "knowledge_points": ["元类", "描述符"],
         "stem": "关于 Python 元类与描述符，下列说法正确的有？",
         "options": [_opt("A", "type 是所有新式类的默认元类"), _opt("B", "实现 __get__/__set__ 的类可作为描述符"),
                     _opt("C", "元类在类定义时被调用"), _opt("D", "描述符只能用于类属性")],
         "answer": ["A", "B", "C", "D"], "analysis": "四项均正确；数据描述符也常用于类属性协议。"},
        {"external_id": "demo-py-t-002", "technology": "python", "difficulty": "advanced",
         "question_type": "text", "knowledge_points": ["异步"],
         "stem": "请解释 asyncio 事件循环的工作机制，以及 CPU 密集型任务为什么不适合直接放入协程。",
         "answer": "事件循环按就绪队列调度协程并在 await 处让出；CPU 密集任务不主动让出会饿死其他协程，应丢进进程池。"},
        {"external_id": "demo-py-t-001", "technology": "python", "difficulty": "intermediate",
         "question_type": "text", "knowledge_points": ["工程实践"],
         "stem": "请简述 Python 中深拷贝与浅拷贝的区别，并各举一个适用场景。",
         "answer": "浅拷贝复制外层容器但共享内层对象（如 list.copy）；深拷贝递归复制全部层级（copy.deepcopy），适用于完全独立的副本。"},
        # ---------------- Java ----------------
        {"external_id": "demo-java-s-001", "technology": "java", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["基础"],
         "stem": "Java 中用于实现多态的机制是？",
         "options": [_opt("A", "接口与继承的重写"), _opt("B", "方法重载"), _opt("C", "泛型"), _opt("D", "包访问控制")],
         "answer": "A", "analysis": "运行时多态依赖父类引用指向子类对象并重写方法。"},
        {"external_id": "demo-java-s-002", "technology": "java", "difficulty": "advanced",
         "question_type": "single", "knowledge_points": ["JVM"],
         "stem": "JVM 中发生 StackOverflowError 的典型原因是？",
         "options": [_opt("A", "堆内存不足"), _opt("B", "递归调用过深"), _opt("C", "元空间满"), _opt("D", "GC 停顿")],
         "answer": "B", "analysis": "栈深度超限通常由无终止递归导致。"},
        {"external_id": "demo-java-m-001", "technology": "java", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["集合框架"],
         "stem": "以下哪些 Java 集合是线程安全的？",
         "options": [_opt("A", "ArrayList"), _opt("B", "CopyOnWriteArrayList"), _opt("C", "ConcurrentHashMap"), _opt("D", "HashMap")],
         "answer": ["B", "C"], "analysis": "ArrayList/HashMap 非线程安全。"},
        {"external_id": "demo-java-t-001", "technology": "java", "difficulty": "advanced",
         "question_type": "text", "knowledge_points": ["并发"],
         "stem": "请说明 synchronized 与 ReentrantLock 的主要区别。",
         "answer": "synchronized 是 JVM 内置锁、自动释放；ReentrantLock 显式加解锁，支持可中断、超时与多条件队列。"},
        # ---------------- 前端 ----------------
        {"external_id": "demo-fe-s-001", "technology": "frontend", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["HTML"],
         "stem": "HTML 语义化标签中，表示页面主导航的是？",
         "options": [_opt("A", "<div>"), _opt("B", "<nav>"), _opt("C", "<section>"), _opt("D", "<aside>")],
         "answer": "B", "analysis": "nav 专门用于导航链接区域。"},
        {"external_id": "demo-fe-s-002", "technology": "frontend", "difficulty": "intermediate",
         "question_type": "single", "knowledge_points": ["React"],
         "stem": "React 中用于在函数组件里保存跨渲染状态的是？",
         "options": [_opt("A", "useMemo"), _opt("B", "useState"), _opt("C", "useEffect"), _opt("D", "useRef")],
         "answer": "B", "analysis": "useState 返回状态与其更新函数。"},
        {"external_id": "demo-fe-m-001", "technology": "frontend", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["CSS"],
         "stem": "以下哪些方式可以让块级元素水平居中？",
         "options": [_opt("A", "margin: 0 auto（定宽）"), _opt("B", "flex + justify-content: center"),
                     _opt("C", "float: left"), _opt("D", "grid + place-items: center")],
         "answer": ["A", "B", "D"], "analysis": "float 用于浮动布局，不居中。"},
        {"external_id": "demo-fe-t-001", "technology": "frontend", "difficulty": "intermediate",
         "question_type": "text", "knowledge_points": ["性能"],
         "stem": "请列举至少三种首屏加载性能优化手段。",
         "answer": "路由/代码分割、图片懒加载与压缩、CDN、HTTP 缓存、关键 CSS 内联等。"},
        # ---------------- 数据库 ----------------
        {"external_id": "demo-db-s-001", "technology": "database", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["SQL 基础"],
         "stem": "SQL 中去除查询结果重复行的关键字是？",
         "options": [_opt("A", "UNIQUE"), _opt("B", "DISTINCT"), _opt("C", "GROUP"), _opt("D", "LIMIT")],
         "answer": "B", "analysis": "SELECT DISTINCT 会去重。"},
        {"external_id": "demo-db-s-002", "technology": "database", "difficulty": "advanced",
         "question_type": "single", "knowledge_points": ["事务"],
         "stem": "MySQL InnoDB 的默认事务隔离级别是？",
         "options": [_opt("A", "READ UNCOMMITTED"), _opt("B", "READ COMMITTED"),
                     _opt("C", "REPEATABLE READ"), _opt("D", "SERIALIZABLE")],
         "answer": "C", "analysis": "InnoDB 默认可重复读，并通过 MVCC + 间隙锁解决幻读。"},
        {"external_id": "demo-db-m-001", "technology": "database", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["索引"],
         "stem": "关于数据库索引，下列说法正确的有？",
         "options": [_opt("A", "联合索引遵循最左前缀原则"), _opt("B", "索引越多写入越慢"),
                     _opt("C", "所有查询都能命中索引"), _opt("D", "主键默认是聚簇索引（InnoDB）")],
         "answer": ["A", "B", "D"], "analysis": "模糊前缀、函数计算等场景无法命中索引。"},
        {"external_id": "demo-db-t-001", "technology": "database", "difficulty": "advanced",
         "question_type": "text", "knowledge_points": ["优化"],
         "stem": "一条慢 SQL 的排查思路是什么？请描述关键步骤。",
         "answer": "EXPLAIN 查看执行计划、确认索引命中与扫描行数、检查统计信息、改写 SQL 或调整索引、必要时加缓存。"},
        # ---------------- DevOps ----------------
        {"external_id": "demo-ops-s-001", "technology": "devops", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["容器"],
         "stem": "Docker 中查看正在运行容器的命令是？",
         "options": [_opt("A", "docker images"), _opt("B", "docker ps"), _opt("C", "docker ls"), _opt("D", "docker top")],
         "answer": "B", "analysis": "docker ps 列出运行中的容器。"},
        {"external_id": "demo-ops-s-002", "technology": "devops", "difficulty": "intermediate",
         "question_type": "single", "knowledge_points": ["Kubernetes"],
         "stem": "Kubernetes 中管理一组同 Pod 副本的工作负载是？",
         "options": [_opt("A", "Service"), _opt("B", "Deployment"), _opt("C", "ConfigMap"), _opt("D", "Ingress")],
         "answer": "B", "analysis": "Deployment 维护副本集与滚动更新。"},
        {"external_id": "demo-ops-m-001", "technology": "devops", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["CI/CD"],
         "stem": "一个健康的 CI/CD 流水线通常包含哪些阶段？",
         "options": [_opt("A", "代码检查与单元测试"), _opt("B", "构建与镜像打包"),
                     _opt("C", "跳过测试直接上线"), _opt("D", "灰度发布与回滚预案")],
         "answer": ["A", "B", "D"], "analysis": "跳过测试上线是反模式。"},
        {"external_id": "demo-ops-t-001", "technology": "devops", "difficulty": "advanced",
         "question_type": "text", "knowledge_points": ["可观测性"],
         "stem": "请解释可观测性三大支柱及其作用。",
         "answer": "Metrics（指标趋势与告警）、Logging（事件细节定位）、Tracing（跨服务调用链路还原）。"},
        # ---------------- AI ----------------
        {"external_id": "demo-ai-s-001", "technology": "ai", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["机器学习"],
         "stem": "模型在训练集表现很好、测试集很差，这种现象称为？",
         "options": [_opt("A", "欠拟合"), _opt("B", "过拟合"), _opt("C", "梯度消失"), _opt("D", "数据泄漏")],
         "answer": "B", "analysis": "过拟合即 memorization，泛化差。"},
        {"external_id": "demo-ai-s-002", "technology": "ai", "difficulty": "intermediate",
         "question_type": "single", "knowledge_points": ["NLP"],
         "stem": "Transformer 的自注意力机制主要解决了什么？",
         "options": [_opt("A", "减少参数量"), _opt("B", "并行捕捉长距离依赖"), _opt("C", "避免数值溢出"), _opt("D", "自动标注数据")],
         "answer": "B", "analysis": "自注意力让任意位置两两直接交互，可并行。"},
        {"external_id": "demo-ai-s-003", "technology": "ai", "difficulty": "advanced",
         "question_type": "single", "knowledge_points": ["RAG"],
         "stem": "RAG 系统中重排（Rerank）阶段的作用是？",
         "options": [_opt("A", "扩大召回数量"), _opt("B", "对候选片段精排提升相关性"),
                     _opt("C", "压缩索引体积"), _opt("D", "生成最终答案")],
         "answer": "B", "analysis": "Rerank 用更重的模型对召回候选精排。"},
        {"external_id": "demo-ai-m-001", "technology": "ai", "difficulty": "intermediate",
         "question_type": "multiple", "knowledge_points": ["大模型"],
         "stem": "缓解大模型「幻觉」的常见手段包括？",
         "options": [_opt("A", "检索增强（RAG）"), _opt("B", "提供明确上下文与约束"),
                     _opt("C", "提高温度参数"), _opt("D", "要求引用来源")],
         "answer": ["A", "B", "D"], "analysis": "高温提升随机性，通常加剧幻觉。"},
        {"external_id": "demo-ai-t-001", "technology": "ai", "difficulty": "advanced",
         "question_type": "text", "knowledge_points": ["工程"],
         "stem": "请描述向量检索与关键词检索（BM25）各自的优劣，以及为什么要混合。",
         "answer": "向量检索擅长语义相似、但对精确词与生僻词弱；BM25 擅长精确匹配与可解释；混合取长补短，再经 RRF/重排融合。"},
        # ---------------- 通用软技能 ----------------
        {"external_id": "demo-gen-s-001", "technology": "general", "difficulty": "beginner",
         "question_type": "single", "knowledge_points": ["协作"],
         "stem": "跨团队需求出现冲突时，最合适的第一步是？",
         "options": [_opt("A", "直接按自己团队方案执行"), _opt("B", "与相关方对齐目标与优先级"),
                     _opt("C", "升级到高层裁决"), _opt("D", "暂缓所有工作")],
         "answer": "B", "analysis": "先对齐目标再决策，避免无效升级。"},
        {"external_id": "demo-gen-t-001", "technology": "general", "difficulty": "intermediate",
         "question_type": "text", "knowledge_points": ["表达"],
         "stem": "请用三句话向非技术同事解释「接口」是什么。",
         "answer": "接口是两个系统之间的约定：约定输入什么、返回什么；双方按约定各自开发，互不关心内部实现。"},
    ]
    items = []
    for item in raw:
        payload = {**item, "bank_id": BANK_ID, "permission_scope": "public", "status": "published"}
        items.append(QuestionImport.model_validate(payload))
    return items


async def seed_questions(db: AsyncSession, owner_id: int) -> int:
    repo = QuestionBankRepository()
    items = build_demo_questions()
    imported = 0
    for item in items:
        row = await repo.upsert_import(db, item, owner_id)
        if row is not None:
            imported += 1
    await db.commit()
    return imported


async def seed_demo_assessment(db: AsyncSession, owner_id: int) -> int | None:
    """无任何 assessment_questions 时，生成一份演示测评作为邀约笔试题池。"""
    existing = (await db.execute(
        select(AssessmentQuestion.id).where(AssessmentQuestion.is_deleted.is_(False)).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return None

    repo = QuestionBankRepository()
    bank_rows = (await db.execute(
        select(repo.model).where(  # type: ignore[attr-defined]
            repo.model.status == "published", repo.model.is_deleted.is_(False)  # type: ignore[attr-defined]
        ).limit(10)
    )).scalars().all()
    if not bank_rows:
        return None

    talent = (await db.execute(select(Talent).where(Talent.is_deleted.is_(False)).limit(1))).scalar_one_or_none()
    if talent is None:
        talent = Talent(name="演示候选人", education="本科", work_years=3,
                        skill_tags=["Python"], status="reserve", source="manual",
                        created_by=owner_id)
        db.add(talent)
        await db.flush()

    assessment = Assessment(
        talent_id=talent.id, title="演示测评（通用能力）", status="pending",
        question_count=len(bank_rows), created_by_ai=False, created_by=owner_id,
    )
    db.add(assessment)
    await db.flush()
    for i, q in enumerate(bank_rows):
        q_type = q.question_type
        if q_type in ("true_false",):
            q_type = "single"
        elif q_type in ("fill_blank", "coding"):
            q_type = "text"
        options = q.options
        if options:
            options = [
                {"key": (o.get("key") if isinstance(o, dict) else o.key),
                 "text": (o.get("text") if isinstance(o, dict) else o.text)}
                for o in options
            ]
        db.add(AssessmentQuestion(
            assessment_id=assessment.id, type=q_type if q_type in ("single", "multiple", "text") else "single",
            content=q.stem, options=options, answer=q.answer, score=Decimal("10"),
            dimension=q.technology or "general", sort=i,
        ))
    await db.commit()
    return assessment.id


async def main(with_assessment: bool = True) -> None:
    print("=" * 68)
    print(" 演示题库初始化（question_bank + 演示测评）")
    print("=" * 68)
    settings.ensure_dirs()
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            from app.models.user import User

            admin = (await db.execute(
                select(User).where(User.is_superuser.is_(True)).order_by(User.id).limit(1)
            )).scalar_one_or_none()
            owner_id = admin.id if admin else 1

        async with factory() as db:
            imported = await seed_questions(db, owner_id)
            print(f"[1/2] 题库导入完成：{imported} 道演示题（bank_id={BANK_ID}，幂等 upsert）")

        if with_assessment:
            async with factory() as db:
                assessment_id = await seed_demo_assessment(db, owner_id)
                if assessment_id is None:
                    print("[2/2] 已存在测评题目快照，跳过演示测评创建")
                else:
                    print(f"[2/2] 演示测评已创建：id={assessment_id}（邀约笔试题池就绪）")
    finally:
        await engine.dispose()
    print("=" * 68)
    print(" 完成。可在「智能测评 / 发邀约」页面直接创建笔试。")
    print("=" * 68)


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="生成演示题库与演示测评")
    parser.add_argument("--no-assessment", action="store_true", help="只导题库，不创建演示测评")
    args = parser.parse_args()
    asyncio.run(main(with_assessment=not args.no_assessment))
