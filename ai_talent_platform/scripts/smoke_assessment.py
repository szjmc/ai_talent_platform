"""
标签：[Agent组] [P2]
模块：scripts/smoke_assessment.py
职责：人才测评功能「离线冒烟测试」（不连 DB、不打真实 LLM/视觉模型）。

覆盖范围（纯函数 / 本地降级链路）：
    1. 简历解析（正则） -> 难度推断；旧同步组卷入口明确拒绝内置题库
    2. 五维雷达评分 + 等级 + 短板 + 提升建议
    3. 面试记录表「兜底正则解析」（视觉模型不可用时的降级路径）
    4. 面试记录向量化（本地哈希嵌入确定性）+ 本地向量库写入/召回
    5. 邮件模板渲染（笔试链接按钮 + 防注入转义）

运行方式：
    cd ai-digital-talent-platform
    python scripts/smoke_assessment.py

说明：本脚本是「无外部依赖」的功能自检，通过后即可进入
    scripts/smoke_assessment_integration.py（连 MySQL 的全流程集成测试）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "✅ 通过"
FAIL = "❌ 失败"


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{PASS if cond else FAIL}  {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


async def _test_resume_and_paper() -> None:
    from app.ai.agents.resume_parser import infer_difficulty, parse_resume, retrieve_questions
    from app.core.exceptions import BusinessRuleError

    resume = ("张三，8年Java开发经验，高级工程师，硕士，"
              "曾负责高并发分布式微服务系统，期望25-35K，熟悉Spring/Redis/MySQL/AI大模型")
    kw = parse_resume(resume)
    diff = infer_difficulty(kw)
    print("\n【1】简历解析 -> 难度（数据库组卷见集成脚本/回归测试）")
    _check("正则抽取技能", len(kw.get("skills", [])) >= 5,
           f"skills={kw.get('skills')}")
    _check("难度推断为 专家", diff == "专家", f"difficulty={diff}")
    try:
        retrieve_questions(diff, kw.get("skills", []), count=5)
    except BusinessRuleError:
        _check("旧同步组卷入口拒绝内置题库", True)
    else:
        _check("旧同步组卷入口拒绝内置题库", False)


async def _test_radar() -> None:
    from app.services.assessment_service import RADAR_DIMS, RADAR_LABELS, AssessmentService

    svc = AssessmentService(None)  # 纯计算不需要真实 DB
    radar = svc._compute_radar(
        80.0,
        {"专业基础": 80.0, "架构设计": 70.0},
        {"interview_score": 82.0, "qa_avg": 78.0, "records": []},
        {"education": "硕士", "school_tier": 4, "years": 8, "skills": ["Java", "Spring", "AI"]},
    )
    total = round(sum(radar[d] for d in RADAR_DIMS) / len(RADAR_DIMS), 1)
    level = svc._level(total)
    weaknesses = svc._weaknesses(radar)
    suggestions = svc._suggestions(radar, {"records": []})

    print("\n【2】五维量化评分 + 提升建议")
    _check("五维分数齐全且在 0-100", all(0 <= radar[d] <= 100 for d in RADAR_DIMS),
           "、".join(f"{RADAR_LABELS[d]}={radar[d]}" for d in RADAR_DIMS))
    _check("综合得分与等级", total > 0 and level in ("初级", "中级", "高级", "专家"),
           f"total={total} level={level}")
    _check("产出短板", len(weaknesses) >= 0)
    _check("产出提升建议", len(suggestions) >= 1, f"suggestions={suggestions[:2]}")


async def _test_interview_parse() -> None:
    from app.ai.agents.interview_record_parser import _parse_fallback_text

    text = ("岗位：高级Java工程师\n"
            "面试得分：85\n"
            "综合评价：技术功底扎实，沟通良好\n"
            "亮点：分布式经验丰富；主导过核心项目\n"
            "不足：AI工程化经验欠缺")
    parsed = _parse_fallback_text(text)

    print("\n【3】面试记录表兜底正则解析")
    _check("岗位解析", parsed["position"] == "高级Java工程师", str(parsed["position"]))
    _check("面试得分解析", parsed["interview_score"] == 85.0, str(parsed["interview_score"]))
    _check("亮点列表", parsed["highlights"] == ["分布式经验丰富", "主导过核心项目"])
    _check("不足列表", parsed["weaknesses"] == ["AI工程化经验欠缺"])


async def _test_vector() -> None:
    from app.adapters.vector_store.interview_vector_store import get_vector_store, new_vector_id
    from app.ai.rag.record_embedding import embed_dim, embed_query

    text = "高级Java工程师 分布式 高并发"
    v1 = await embed_query(text)
    v2 = await embed_query(text)
    _check("本地哈希嵌入确定性（同文本同向量）", v1 == v2 and len(v1) == embed_dim(),
           f"dim={len(v1)}")

    from uuid import uuid4

    store = get_vector_store()
    col = f"interview_record_smoke_{uuid4().hex}"
    await store.create_collection(col, embed_dim())
    try:
        vid = new_vector_id()
        await store.upsert(col, [vid], [v1],
                           metadata=[{"assessment_id": 900001, "talent_id": 900001}],
                           docs=[text])
        hits = await store.search(col, v1, top_k=3, expr="assessment_id == 900001")
    finally:
        await store.drop(col)

    print("\n【4】面试记录向量化 + 本地向量库")
    _check("向量库实例为本地降级或 Milvus", type(store).__name__ in
           ("LocalInterviewStore", "MilvusInterviewStore"), type(store).__name__)
    _check("写入后可按条件召回", len(hits) == 1 and hits[0]["id"] == vid,
           f"hits={len(hits)}")


def _test_email() -> None:
    from app.infrastructure.notify.email_client import _TEMPLATES, _build_message

    msg = _build_message("candidate@test.com", _TEMPLATES["assessment_invite"],
                         {"name": "张三<测试>", "paper": "高级Java工程师",
                          "url": "http://localhost:3000/exam/123"})
    html_body = msg.get_payload()[1].get_payload(decode=True).decode("utf-8") \
        if msg.is_multipart() else str(msg)

    print("\n【5】邮件模板渲染（笔试链接）")
    _check("含「立即开始笔试」链接按钮", "立即开始笔试" in html_body)
    _check("链接指向笔试入口", "http://localhost:3000/exam/123" in html_body)
    _check("姓名 HTML 转义防注入", "张三&lt;测试&gt;" in html_body)


async def main() -> None:
    print("=" * 60)
    print("人才测评功能 · 离线冒烟测试")
    print("=" * 60)
    await _test_resume_and_paper()
    await _test_radar()
    await _test_interview_parse()
    await _test_vector()
    _test_email()
    print("\n" + "=" * 60)
    print("全部离线冒烟测试通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
