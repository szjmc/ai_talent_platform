"""
MVP回归：正式Dify装配、候选状态约束和画像权重保持；全部使用假服务，无数据库。

标签：[Agent组] [P1]（MVP 装配与降级链单元用例）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config.settings import settings
from app.services.matching_pipeline import MatchingPipeline
from app.services.position_profile_service import PositionProfileService
from app.schemas.position import JobProfileConfirmRequest, JobProfileParsed

@pytest.mark.asyncio
async def test_named_candidates_use_status_filtered_repository():
    # 步骤：指定ID -> 带状态的仓库 -> 空候选池；不允许回退到无状态查询。
    repo = SimpleNamespace(list_candidates=AsyncMock(return_value=[]))
    result = await MatchingPipeline(None, talent_repo=repo).recall({"talent_ids": [9]})
    repo.list_candidates.assert_awaited_once_with(None, talent_ids=[9], statuses=("active", "reserve"))
    assert result["candidates"] == []

@pytest.mark.asyncio
async def test_profile_confirmation_keeps_custom_weights():
    # 步骤：已有自定义权重 -> 确认画像 -> 主表及版本记录均保留原权重。
    weights = {"skill": .6, "experience": .2, "education": .1, "quality": .1}
    position = SimpleNamespace(id=1, title="Python工程师", status="open", requirements={"weights": weights})
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=position), latest_requirement=AsyncMock(return_value=None), next_requirement_version=AsyncMock(return_value=1), add_requirement=AsyncMock())
    db = SimpleNamespace(flush=AsyncMock())
    profile = JobProfileParsed(job_title=position.title, required_skills=["Python"], min_education="本科", min_work_years=3)
    await PositionProfileService(db, repo).confirm(1, JobProfileConfirmRequest(profile=profile), user_id=1)
    assert position.requirements["weights"] == weights
    assert repo.add_requirement.await_args.kwargs["weights"] == weights

@pytest.mark.asyncio
async def test_default_jd_entry_uses_and_closes_adapter(monkeypatch):
    # 步骤：调用正式解析入口 -> 自动创建JD适配器 -> 标注真实通道 -> 释放客户端。
    from app.services import jd_parser
    fake = SimpleNamespace(configured=True, run_job_profile_extraction=AsyncMock(return_value={"parsed": {"job_title": "Python工程师", "required_skills": ["Python"]}}))
    class Managed:
        async def __aenter__(self): return fake
        async def __aexit__(self, *args): fake.closed = True
    def factory(**kwargs):
        assert kwargs == {"workflow_key": "job_profile_extraction"}
        return Managed()
    monkeypatch.setattr(jd_parser, "DifyWorkflowAdapter", factory)
    profile, channel = await jd_parser.parse_jd("岗位名称：Python工程师，要求熟悉Python开发，三年以上经验")
    assert channel == "dify" and profile.source == "dify" and fake.closed

@pytest.mark.asyncio
async def test_default_explanation_entry_uses_own_application(monkeypatch):
    # 步骤：配置解释Key -> 默认服务调用解释应用 -> 校验输出并标注dify。
    from app.services import explanation_service as module
    from app.services.evidence_builder import build_match_evidence
    monkeypatch.setattr(settings, "DIFY_EXPLANATION_API_KEY", "test-explanation-key")
    monkeypatch.setattr(settings, "DIFY_BASE_URL", "http://dify.test/v1")
    fake = SimpleNamespace(run_match_explanation=AsyncMock(return_value={"parsed": {"summary": "依据档案信息完成条件核对", "highlights": [], "gaps": [], "risks": []}}))
    class Managed:
        async def __aenter__(self): return fake
        async def __aexit__(self, *args): fake.closed = True
    def factory(**kwargs):
        assert kwargs == {"workflow_key": "match_explanation"}
        return Managed()
    monkeypatch.setattr(module, "DifyWorkflowAdapter", factory)
    pack = build_match_evidence({"id": 1, "title": "工程师"}, {"id": 2, "education": "本科", "work_years": 3, "skill_tags": ["Python"]})
    outcome = await module.ExplanationService().generate(pack)
    assert outcome.channel == "dify" and outcome.explanation.source == "dify" and fake.closed

@pytest.mark.parametrize("workflow,field", [("job_profile_extraction", "DIFY_JD_API_KEY"), ("match_explanation", "DIFY_EXPLANATION_API_KEY")])
def test_workflow_specific_keys(monkeypatch, workflow, field):
    from app.adapters.dify.workflow_adapter import DifyWorkflowAdapter
    monkeypatch.setattr(settings, "DIFY_API_KEY", "shared-test-key")
    monkeypatch.setattr(settings, field, "specific-test-key")
    assert DifyWorkflowAdapter(workflow_key=workflow)._api_key == "specific-test-key"
    assert DifyWorkflowAdapter(workflow_key=workflow, api_key="override")._api_key == "override"
