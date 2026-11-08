"""临时复现脚本：mock 取数与 LLM，观察决策报告输出结构。"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.ai.workflows.talent_decision_report import run_analysis
from app.services.talent_decision_service import generate_decision_report


FAKE_USER = type("U", (), {"id": 1, "username": "admin", "real_name": "管理员", "is_superuser": True, "organization_id": 1, "role_codes": ["super_admin"]})()


async def fake_query_database(question, max_rows=200):
    return {"question": question, "columns": ["学历", "人数"], "rows": [["本科", 12], ["硕士", 5], ["博士", 2]], "row_count": 3, "error": None}


def make_llm_result(content):
    class R:
        pass
    r = R()
    r.content = content
    r.model = "fake"
    r.prompt_tokens = 10
    r.completion_tokens = 10
    return r


async def main():
    fake_llm = make_llm_result(json.dumps({
        "report_audience": "leader",
        "meta": {"report_purpose": "测试报告", "data_cutoff": "2026-11-08", "data_scope": "全公司19人", "sample_tip": "", "threshold_desc": "", "kpi_goal": {"reassess_pass_rate": "≥70%", "train_complete_rate": "≥60%"}},
        "conclusions": ["学历结构以本科为主，占 63%。"],
        "suggestions": {"p0": ["推进硕士以上占比提升至 30%"], "p1": [], "warn_if_no_action": ""},
        "group_summaries": {"人才大盘": "本科为主"},
        "risk_list": [],
        "chart_display": {},
        "key_talent_top3": [],
        "appendix_note": "",
    }, ensure_ascii=False))

    ctx = type("C", (), {"data_scope": "all", "user_id": 1, "conversation_id": None, "trace_id": "t", "username": "admin", "roles": ["super_admin"], "organization_id": 1})()
    from contextlib import contextmanager

    @contextmanager
    def _cs(user, roles=None):
        yield ctx

    with patch("app.ai.workflows.talent_decision_report.query_database", new=fake_query_database), \
         patch("app.ai.core.llm_manager.llm_manager.chat", new=AsyncMock(return_value=fake_llm)), \
         patch("app.services.talent_decision_service.get_context", return_value=ctx), \
         patch("app.ai.context.get_context", return_value=ctx), \
         patch("app.services.talent_decision_service.context_scope", side_effect=_cs):
        result = await generate_decision_report(FAKE_USER, "分析人才结构", None)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:4000])


asyncio.run(main())
