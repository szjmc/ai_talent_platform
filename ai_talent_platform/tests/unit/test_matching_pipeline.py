"""
文件：tests/unit/test_matching_pipeline.py
职责：匹配流水线纯函数单元测试（不连数据库）。

覆盖：
1. 权重归一化：非法配置回落默认，比例型配置归一化到 1.0；
2. 岗位视图：打分/判定统一使用「已确认画像」的必备技能，而不是历史 skill_tags；
3. 默认维度识别：档案缺字段时正确标注，供前端提示可信度。

标签：[Agent组] [P1]（匹配流水线单元用例）
"""

from __future__ import annotations

from types import SimpleNamespace

from app.patterns.strategy import MATCH_DEFAULT_WEIGHTS
from app.schemas.position import JobProfileParsed
from app.services.matching_pipeline import _position_view, _weights_from, defaulted_dimensions


def _profile(**kwargs) -> JobProfileParsed:
    data = {
        "job_title": "后端开发工程师",
        "required_skills": ["Python", "FastAPI"],
        "min_education": "本科",
        "min_work_years": 3,
    }
    data.update(kwargs)
    return JobProfileParsed(**data)


def _position(requirements=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        title="后端开发工程师",
        requirements=requirements,
        skill_tags=["Java", "Spring"],
    )


class TestWeights:
    def test_default_when_missing(self):
        assert _weights_from(_position(None)) == MATCH_DEFAULT_WEIGHTS

    def test_invalid_weights_fall_back(self):
        assert _weights_from(_position({"weights": {"skill": "x"}})) == MATCH_DEFAULT_WEIGHTS
        assert _weights_from(_position({"weights": {"skill": -1}})) == MATCH_DEFAULT_WEIGHTS

    def test_ratio_weights_normalized(self):
        weights = _weights_from(
            _position({"weights": {"skill": 4, "experience": 3, "education": 2, "quality": 1}})
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-6
        assert weights["skill"] == 0.4


class TestPositionView:
    def test_uses_confirmed_profile_skills(self):
        view = _position_view(_position({"weights": None}), _profile())

        assert view["skill_tags"] == ["Python", "FastAPI"]
        assert view["requirements"]["required_skills"] == ["Python", "FastAPI"]
        # 历史 skill_tags（Java/Spring）不得参与判定
        assert "Java" not in view["skill_tags"]


class TestDefaultedDimensions:
    def test_all_missing(self):
        talent = SimpleNamespace(education=None, skill_tags=None, work_years=0, potential_level=None)
        assert set(defaulted_dimensions(talent, _profile())) == {
            "education",
            "skill",
            "experience",
            "quality",
        }

    def test_complete_profile(self):
        talent = SimpleNamespace(education="本科", skill_tags=["Python"], work_years=5, potential_level="A")
        assert defaulted_dimensions(talent, _profile()) == []

    def test_dict_input(self):
        talent = {"education": "本科", "skill_tags": ["Python"], "work_years": 5, "potential_level": "B"}
        assert defaulted_dimensions(talent, _profile()) == []


class TestResponsibilityEvidence:
    """F-6 修复：职责证据不得用字符串包含关系制造假命中。"""

    def test_java_must_not_match_javascript(self):
        from app.services.evidence_builder import build_match_evidence

        position = {
            "id": 31,
            "title": "前端开发工程师",
            "requirements": {
                "job_title": "前端开发工程师",
                "responsibilities": ["负责 JavaScript 前端页面开发"],
                "required_skills": [],
                "min_education": None,
                "min_work_years": None,
            },
        }
        talent = {"id": 32, "name": "李四", "skill_tags": ["Java"], "education": "本科", "work_years": 3}

        pack = build_match_evidence(position, talent, position_id=31, talent_id=32)

        responsibility = [item for item in pack.evidences if item.requirement_ref.startswith("responsibilities")]
        assert responsibility == []

    def test_exact_english_skill_still_matches(self):
        from app.services.evidence_builder import build_match_evidence

        position = {
            "id": 41,
            "title": "前端开发工程师",
            "requirements": {
                "job_title": "前端开发工程师",
                "responsibilities": ["负责 JavaScript 前端页面开发"],
                "required_skills": [],
                "min_education": None,
                "min_work_years": None,
            },
        }
        talent = {
            "id": 42,
            "name": "王五",
            "skill_tags": ["JavaScript"],
            "education": "本科",
            "work_years": 3,
        }

        pack = build_match_evidence(position, talent, position_id=41, talent_id=42)

        responsibility = [item for item in pack.evidences if item.requirement_ref.startswith("responsibilities")]
        assert responsibility
        assert responsibility[0].source_value == ["JavaScript"]


class TestOrmEvidence:
    """F-2 复核：传 ORM 对象时不得出现「判定 PASS + 同时声明字段缺失」的矛盾证据。"""

    def test_orm_input_produces_consistent_pack(self):
        from app.models.position import Position
        from app.models.talent import Talent
        from app.services.evidence_builder import build_match_evidence
        from app.services.evidence_verifier import audit_pack

        position = Position(
            title="后端开发工程师",
            code="P-UNIT-1",
            organization_id=1,
            requirements={
                "job_title": "后端开发工程师",
                "required_skills": ["Python", "FastAPI"],
                "min_education": "本科",
                "min_work_years": 3,
            },
        )
        talent = Talent(
            name="张三",
            education="本科",
            work_years=5,
            skill_tags=["Python", "FastAPI"],
            potential_level="B",
            status="active",
        )

        pack = build_match_evidence(position, talent, position_id=11, talent_id=22, requirement_version=2)

        assert pack.requirement_version == 2
        assert audit_pack(pack) == []
        missing_fields = {
            item.source_field for item in pack.evidences if item.evidence_type == "missing"
        }
        assert "education" not in missing_fields
        assert "skill_tags" not in missing_fields
