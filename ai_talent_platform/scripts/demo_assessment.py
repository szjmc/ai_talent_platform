"""
模块：scripts/demo_assessment.py
职责：人才测评分析 Agent 离线冒烟测试（不连 DB、不打真实 Dify、LLM 用桩替换）。

运行方式：
    cd ai-digital-talent-platform
    python scripts/demo_assessment.py

验证点：
    1. AssessmentAgent 能被 agent_registry 注册并实例化；
    2. 7 节点管线（assign->dispatch->monitor->stat->score->analyze->report）顺序跑通；
    3. assign/score/analyze 走测试数据（Dify 未交付）；
    4. report 的 LLM 小结用桩替换（不依赖硅基流动真 Key）；
    5. 最终输出 total_score / level / dimension_scores / weakness / suggestions。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 让脚本从项目根目录运行时也能 import app.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main() -> None:
    # ---- 1) 桩替换 llm_manager.chat：模拟硅基流动返回一段小结（避免真实网络请求）----
    from app.adapters.llm.base_llm import LLMResult
    from app.ai.core import llm_manager as llm_pkg

    async def fake_chat(messages, purpose="assessment", **kwargs):  # noqa: ANN001
        return LLMResult(
            content="该人才整体表现中上，专业功底扎实、实战经验与团队协作突出；"
                    "短板集中在 AI 工程化与数据治理深度，建议主导一个端到端 AI 应用项目补全工程闭环。",
            model="Qwen/Qwen2.5-72B-Instruct(siliconflow-stub)",
            prompt_tokens=320,
            completion_tokens=80,
        )

    llm_pkg.llm_manager.chat = fake_chat  # type: ignore[assignment]

    # ---- 2) 注册并实例化 Agent ----
    from app.ai.agent import bootstrap_ai, dispatch
    from app.patterns.registry import agent_registry

    bootstrap_ai()
    assert "assessment" in agent_registry, "assessment Agent 未注册！"
    print("[OK] assessment Agent 已注册，当前已注册：", agent_registry.keys())

    # ---- 3) 构造模拟入参（含客观题规则分 + 主观题待语义打分）----
    payload = {
        "assessment_id": 9001,
        "talent_id": 88,
        "position_id": 102,
        "job_level": "中级",
        "profession": "数字化",
        "grade": "中级",
        "stage": "full",
        "answers": [
            {"question_id": 1001, "type": "single", "dimension": "战略思维",
             "answer": "C", "rule_score": 10, "scored_by": "rule"},
            {"question_id": 1005, "type": "single", "dimension": "专业能力",
             "answer": "B", "rule_score": 20, "scored_by": "rule"},
            {"question_id": 1003, "type": "text", "dimension": "实战经验",
             "answer": "主导轨道交通交付数字化项目，按期上线，回款率提升 15%",
             "rule_score": 0, "scored_by": "ai"},
            {"question_id": 1004, "type": "text", "dimension": "综合素质",
             "answer": "通过建立周例会机制拉通三方，解决跨部门卡点",
             "rule_score": 0, "scored_by": "ai"},
        ],
    }

    # ---- 4) 经统一调度入口 dispatch 跑一遍 ----
    result = await dispatch("assessment", payload)
    print("\n===================== 测评 Agent 执行结果 =====================")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # ---- 5) 断言关键产物 ----
    assert result["ok"], f"执行失败：{result}"
    r = result["result"]
    assert r["total_score"] is not None, "缺少总分"
    assert r["level"] in ("初级", "中级", "高级", "专家"), "等级异常"
    assert r["dimension_scores"], "缺少雷达维度分"
    assert isinstance(r["weakness"], list), "缺少短板"
    print("\n[OK] 全流程跑通：total_score=%s level=%s" % (r["total_score"], r["level"]))


if __name__ == "__main__":
    asyncio.run(main())
