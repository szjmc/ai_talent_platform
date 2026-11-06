"""真实Dify验收：使用虚构岗位/人才验证两条正式入口；不连接数据库。
步骤：检查配置 -> 真实JD提取 -> 证据构建 -> 真实解释 -> 严格检查来源。
运行：python scripts/check_matching_dify.py；失败退出1，降级结果不计接通成功。

标签：[Agent组] [P2]（匹配链路 Dify 配置自检脚本）
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import settings
from app.services.jd_parser import parse_jd
from app.services.evidence_builder import build_match_evidence
from app.services.explanation_service import ExplanationService

async def check() -> dict:
    # 1. 预检必需配置，错误只说明字段名，不泄露密钥或发送人才真实资料。
    for name, value in {
        "DIFY_BASE_URL": settings.DIFY_BASE_URL,
        "DIFY_JD_API_KEY (or DIFY_API_KEY)": settings.DIFY_JD_API_KEY or settings.DIFY_API_KEY,
        "DIFY_EXPLANATION_API_KEY (or DIFY_API_KEY)": settings.DIFY_EXPLANATION_API_KEY or settings.DIFY_API_KEY,
    }.items():
        if not value: raise RuntimeError("缺少配置：" + name)
    jd = "岗位名称：Python后端工程师\n岗位职责：开发后端API服务\n任职要求：本科以上学历，3年以上开发经验，熟悉Python和FastAPI。"
    # 2. 调正式入口，任何降级都使验收失败，不把规则产物冒充Dify。
    profile, channel = await parse_jd(jd, job_title="Python后端工程师")
    if channel != "dify": raise RuntimeError("JD未走Dify，请检查工作流输入jd_text及输出result JSON")
    # 3. 使用虚构主档构建证据，仅验证连接，不保存/确认岗位或更改数据库。
    pack = build_match_evidence({"id": 1, "title": profile.job_title, "requirements": profile.model_dump(mode="json")}, {"id": 2, "name": "虚构联调人才", "education": "本科", "work_years": 5, "skill_tags": ["Python", "FastAPI"], "potential_level": "B"})
    # 禁用LLM降级：本脚本只验Dify，不产生第二家模型调用及额外费用。
    service = ExplanationService()
    service._llm_configured = lambda: False
    outcome = await service.generate(pack)
    if outcome.channel != "dify" or outcome.explanation.source != "dify":
        raise RuntimeError("解释未走Dify，请检查evidence_pack输入与解释JSON契约")
    # 4. 输出非敏感验收摘要；剔除结论数用于人工评估工作流质量。
    return {"jd_channel": channel, "explanation_source": outcome.explanation.source, "rejected_claims": len(outcome.explanation.rejected_claims), "pending_verification": len(outcome.explanation.pending_verification), "note": "仅虚构数据两工作流联调通过，不代表数据库及完整业务验收"}

if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(check()), ensure_ascii=False, indent=2))
    except Exception as exc:
        # 不输出异常详情，避免外部网关错误夹带认证信息。
        print("Dify验收失败：" + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
