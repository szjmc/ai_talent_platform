"""脚本：生成演示题库（笔试抽题池数据）。

标签：[Agent组] [P1]

用途：assessment_questions 为空时无法创建笔试邀约；本脚本写入一份演示试卷
（12 题，单选/多选/简答混合，含 answer/score/dimension），并确保挂靠一个
演示人才（assessments.talent_id 为必填外键），供「笔试邀约 -> 按简历解析
抽题 -> 交卷阅卷」链路联调演示。

使用方式：
    python scripts/seed_question_bank.py            # 幂等：已有同名试卷则跳过
    python scripts/seed_question_bank.py --force    # 删除同名试卷后重建

前置：MySQL 已初始化（scripts/init_db.py）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

TITLE = "演示笔试题库（AI 人才平台）"
DEMO_MOBILE = "13800000000"
DEMO_EMAIL = "demo.invite@example.com"

# options: [{key, text}]（平台约定）；answer 客观题用 key（多选用 key 列表），文本题留空。
DEMO_QUESTIONS: list[dict] = [
    {
        "type": "single",
        "content": "在 Python 中，下列哪个关键字用于定义异步函数？",
        "options": [{"key": "A", "text": "async"}, {"key": "B", "text": "await"}, {"key": "C", "text": "yield"}, {"key": "D", "text": "defer"}],
        "answer": "A",
        "score": 10,
        "dimension": "编程基础",
    },
    {
        "type": "single",
        "content": "HTTP 状态码 403 表示什么？",
        "options": [{"key": "A", "text": "服务器内部错误"}, {"key": "B", "text": "禁止访问"}, {"key": "C", "text": "资源未找到"}, {"key": "D", "text": "请求超时"}],
        "answer": "B",
        "score": 10,
        "dimension": "Web 基础",
    },
    {
        "type": "single",
        "content": "关系型数据库中，用于唯一标识一行记录的约束是？",
        "options": [{"key": "A", "text": "FOREIGN KEY"}, {"key": "B", "text": "UNIQUE"}, {"key": "C", "text": "PRIMARY KEY"}, {"key": "D", "text": "CHECK"}],
        "answer": "C",
        "score": 10,
        "dimension": "数据库",
    },
    {
        "type": "single",
        "content": "快速排序的平均时间复杂度是？",
        "options": [{"key": "A", "text": "O(n)"}, {"key": "B", "text": "O(n log n)"}, {"key": "C", "text": "O(n^2)"}, {"key": "D", "text": "O(log n)"}],
        "answer": "B",
        "score": 10,
        "dimension": "算法",
    },
    {
        "type": "single",
        "content": "Docker 镜像与容器的关系，下列类比最准确的是？",
        "options": [{"key": "A", "text": "容器是镜像运行时的实例"}, {"key": "B", "text": "镜像是容器运行时的实例"}, {"key": "C", "text": "两者完全等价"}, {"key": "D", "text": "镜像只能包含一层"}],
        "answer": "A",
        "score": 10,
        "dimension": "DevOps",
    },
    {
        "type": "single",
        "content": "对简历进行结构化解析时，以下哪项不属于常见抽取字段？",
        "options": [{"key": "A", "text": "工作年限"}, {"key": "B", "text": "技能标签"}, {"key": "C", "text": "浏览器指纹"}, {"key": "D", "text": "教育经历"}],
        "answer": "C",
        "score": 10,
        "dimension": "AI 应用",
    },
    {
        "type": "multiple",
        "content": "下列哪些属于常见的 Web 前端框架？（多选）",
        "options": [{"key": "A", "text": "React"}, {"key": "B", "text": "Vue"}, {"key": "C", "text": "Spring"}, {"key": "D", "text": "Angular"}],
        "answer": ["A", "B", "D"],
        "score": 10,
        "dimension": "前端",
    },
    {
        "type": "multiple",
        "content": "关于 Git，下列说法正确的有？（多选）",
        "options": [{"key": "A", "text": "分支可随时创建与合并"}, {"key": "B", "text": "提交不可修改历史"}, {"key": "C", "text": "tag 常用于标记版本"}, {"key": "D", "text": "origin 是默认远程名之一"}],
        "answer": ["A", "C", "D"],
        "score": 10,
        "dimension": "工程协作",
    },
    {
        "type": "multiple",
        "content": "下列哪些做法有助于保障 API 安全？（多选）",
        "options": [{"key": "A", "text": "鉴权与权限校验"}, {"key": "B", "text": "限流与防重放"}, {"key": "C", "text": "前端硬编码密钥"}, {"key": "D", "text": "敏感信息脱敏"}],
        "answer": ["A", "B", "D"],
        "score": 10,
        "dimension": "安全",
    },
    {
        "type": "text",
        "content": "请简述你最近一个项目中，如何拆分任务并保障交付质量（200 字以内）。",
        "options": [],
        "answer": "",
        "score": 10,
        "dimension": "综合素质",
    },
    {
        "type": "text",
        "content": "请描述一次你处理线上故障的经历：现象、定位过程与改进措施。",
        "options": [],
        "answer": "",
        "score": 10,
        "dimension": "综合素质",
    },
    {
        "type": "text",
        "content": "你如何看待 AI 对招聘流程的影响？请给出你的观点与一个落地建议。",
        "options": [],
        "answer": "",
        "score": 10,
        "dimension": "AI 应用",
    },
]


async def main(force: bool) -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from app.infrastructure.database.session import session_scope  # noqa: PLC0415
    from app.models.assessment import Assessment, AssessmentQuestion  # noqa: PLC0415
    from app.models.talent import Talent  # noqa: PLC0415

    async with session_scope() as db:
        # 1) 演示人才（assessments.talent_id 必填外键）
        talent = (
            await db.execute(select(Talent).where(Talent.mobile == DEMO_MOBILE, Talent.is_deleted.is_(False)))
        ).scalar_one_or_none()
        if talent is None:
            talent = Talent(
                name="演示候选人",
                mobile=DEMO_MOBILE,
                email=DEMO_EMAIL,
                work_years=5,
                skill_tags=["Python", "FastAPI", "MySQL", "Kubernetes"],
                status="active",
                source="manual",
            )
            db.add(talent)
            await db.flush()
            print(f"已创建演示人才：id={talent.id}")

        # 2) 幂等：同名试卷
        existing = (
            await db.execute(select(Assessment).where(Assessment.title == TITLE).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            if not force:
                print(f"题库已存在（id={existing.id}），跳过。如需重建加 --force")
                return
            await db.execute(
                AssessmentQuestion.__table__.delete().where(AssessmentQuestion.assessment_id == existing.id)
            )
            await db.delete(existing)
            await db.flush()

        # 3) 试卷 + 题目
        assessment = Assessment(
            talent_id=talent.id,
            title=TITLE,
            status="pending",
            question_count=len(DEMO_QUESTIONS),
            created_by_ai=False,
        )
        db.add(assessment)
        await db.flush()
        for i, q in enumerate(DEMO_QUESTIONS):
            db.add(
                AssessmentQuestion(
                    assessment_id=assessment.id,
                    type=q["type"],
                    content=q["content"],
                    options=q["options"],
                    answer=q["answer"],
                    score=q["score"],
                    dimension=q["dimension"],
                    sort=i + 1,
                )
            )
        await db.commit()
        print(f"已创建演示题库：{TITLE}（{len(DEMO_QUESTIONS)} 题，assessment_id={assessment.id}）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成演示笔试题库")
    parser.add_argument("--force", action="store_true", help="删除同名试卷后重建")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.force))
    except KeyboardInterrupt:  # pragma: no cover
        print("已中断")
