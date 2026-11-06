"""
阈值漏洞、缺失信息、Swagger示例的回归验证。

标签：[Agent组] [P1]（批次快照与策略单元用例）
"""
import pytest
from app.schemas.matching import MatchExplanation, ExplanationClaim, MatchRunRequest
from app.schemas.position import JobProfileConfirmRequest
from app.services.evidence_builder import build_match_evidence
from app.services.evidence_verifier import verify_explanation

def pack():
    return build_match_evidence({"id":101,"title":"Python工程师","requirements":{"min_work_years":3}},
                                {"id":201,"work_years":None,"skill_tags":["Python"]})

@pytest.mark.parametrize("field", ["summary","highlights","gaps","risks","interview_suggestions","pending_verification"])
def test_invented_threshold_removed_everywhere(field):
    p=pack(); text="完整度低于0.7阈值，因此不合格"
    values={"summary":"工作年限缺失，待核实"}
    if field=="summary": values[field]=text
    elif field in ("highlights","gaps","risks"):
        values[field]=[ExplanationClaim(text=text,evidence_ids=[p.evidences[0].evidence_id])]
    else: values[field]=[text]
    result=verify_explanation(MatchExplanation(**values),p)
    cleaned=result.explanation.model_dump(exclude={"rejected_claims"})
    assert "0.7" not in str(cleaned)
    assert any("0.7" in r.text for r in result.rejected)

def test_factual_completeness_is_not_threshold():
    result=verify_explanation(MatchExplanation(summary="档案完整度为50%，工作年限待核实"),pack())
    assert "50%" in result.explanation.summary

def test_swagger_examples_validate():
    for model in [MatchRunRequest,JobProfileConfirmRequest]:
        for example in model.model_json_schema()["examples"]:
            model.model_validate(example)
