"""
标签：[Agent组] [P2]
模块：scripts/smoke_resume_agent.py
职责：验证「简历 -> 人才档案解析 Agent（ResumeAgent）-> 组卷 -> 邮件推送」链路。

覆盖范围（需要 LLM 网络，硅基流动 siliconflow 已配置）：
    1. 人才档案解析 Agent 注册检查
    2. 调用同事 ResumeAgent 解析简历（真实 LLM），映射为组卷关键字
    3. 降级链路：无权限身份调用 -> 自动降级本地正则，不抛异常
    4. 难度推断 + 旧同步组卷入口拒绝内置题库（真实组卷需数据库身份）
    5. 邮件模板渲染（笔试链接）

运行方式：
    cd ai-digital-talent-platform
    python scripts/smoke_resume_agent.py

说明：本脚本聚焦「自动分析简历 + 自动推送笔试」这条链路；连 MySQL 的完整
    端到端测试见 scripts/smoke_assessment_integration.py。
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


class _AdminUser:
    """模拟具备 resume:parse 权限的调用者（admin 角色）。"""

    id = 1
    username = "admin"
    role_codes = ["admin"]
    is_superuser = False
    organization_id = None


class _NoPermUser:
    """模拟无 resume:parse 权限的调用者（employee 角色）。"""

    id = 99
    username = "staff"
    role_codes = ["employee"]
    is_superuser = False
    organization_id = None


_RESUME = ("张三，8年Java开发经验，高级工程师，硕士毕业于浙江大学计算机专业，"
           "现任某互联网公司技术负责人，负责高并发分布式微服务系统，"
           "期望薪资25-35K，熟悉Spring/Redis/MySQL/微服务/AI大模型")


async def _test_agent_registered() -> None:
    from app.ai.agent import bootstrap_ai

    info = bootstrap_ai()
    agents = list(info["agents"])
    print("\n【1】人才档案解析 Agent 注册")
    _check("ResumeAgent（resume）已注册", "resume" in agents, f"agents={agents}")


async def _test_agent_parse() -> dict:
    from app.ai.agents.resume_parser import infer_difficulty, parse_resume_via_agent

    kw = await parse_resume_via_agent(db=None, user=_AdminUser(), resume_text=_RESUME)
    diff = infer_difficulty(kw)

    print("\n【2】调用人才档案解析 Agent 解析简历")
    _check("解析出岗位", bool(kw.get("position_title")), f"position={kw.get('position_title')}")
    _check("解析出层级", kw.get("seniority") in ("初级", "中级", "高级", "专家"),
           f"seniority={kw.get('seniority')}")
    _check("解析出技能", len(kw.get("skills", [])) >= 4, f"skills={kw.get('skills')}")
    _check("解析出学历/年限", kw.get("school") and kw.get("years", 0) > 0,
           f"school={kw.get('school')} years={kw.get('years')}")
    _check("推断出难度", diff in ("初级", "中级", "高级", "专家"), f"difficulty={diff}")
    return kw


async def _test_fallback() -> None:
    from app.ai.agents.resume_parser import parse_resume_via_agent

    kw = await parse_resume_via_agent(db=None, user=_NoPermUser(), resume_text=_RESUME)

    print("\n【3】降级链路（无 resume:parse 权限）")
    _check("无权限时降级本地正则，不抛异常", kw is not None)
    _check("降级后仍产出技能", len(kw.get("skills", [])) >= 4, f"skills={kw.get('skills')}")


async def _test_paper_and_email(kw: dict) -> None:
    from app.ai.agents.resume_parser import retrieve_questions
    from app.core.exceptions import BusinessRuleError
    from app.infrastructure.notify.email_client import _TEMPLATES, _build_message

    diff = "专家"
    print("\n【4】组卷契约（题目只允许来自数据库）")
    try:
        retrieve_questions(diff, kw.get("skills", []), count=5)
    except BusinessRuleError:
        _check("旧同步组卷入口拒绝内置题库", True)
    else:
        _check("旧同步组卷入口拒绝内置题库", False)

    exam_url = "http://localhost:3000/exam/999"
    msg = _build_message("candidate@test.com", _TEMPLATES["assessment_invite"],
                         {"name": "张三", "paper": diff, "url": exam_url})
    html_body = msg.get_payload()[1].get_payload(decode=True).decode("utf-8") \
        if msg.is_multipart() else str(msg)

    print("\n【5】邮件推送（笔试链接）")
    _check("含「立即开始笔试」按钮", "立即开始笔试" in html_body)
    _check("链接指向笔试入口", exam_url in html_body)


async def main() -> None:
    print("=" * 60)
    print("简历自动分析 -> 笔试推送 · 链路测试")
    print("=" * 60)
    await _test_agent_registered()
    kw = await _test_agent_parse()
    await _test_fallback()
    await _test_paper_and_email(kw)
    print("\n" + "=" * 60)
    print("全部通过：简历解析/降级与邮件模板可用；真实组卷需数据库题库与可信身份")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
