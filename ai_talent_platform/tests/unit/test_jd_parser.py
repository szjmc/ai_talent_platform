"""
文件：tests/unit/test_jd_parser.py
职责：JD 解析与标准化单元测试（纯函数，无网络 / 无数据库）。

覆盖：
1. 规则提取：中英文技能、学历、年限、薪资、地点、职责 / 要求分段；
2. 「优先」半句切分：同一行里的必备技能不得被误降级；
3. 缺失项：JD 未提及的字段必须进入 missing_fields，禁止补默认值；
4. 冲突项：技能同时必备+优先、学历无法识别、岗位名不一致；
5. 通道标注：Dify 未配置时必须返回 rule_fallback，且 source=manual。

标签：[Agent组] [P1]（JD 解析单元用例）
"""

from __future__ import annotations

import pytest

from app.adapters.dify.workflow_adapter import DifyWorkflowAdapter
from app.core.exceptions import ParamError
from app.services.jd_parser import (
    extract_by_rules,
    normalize_job_profile,
    parse_jd,
)

JD_TEXT = """岗位名称：后端开发工程师
工作地点：深圳

岗位职责：
1. 负责招聘平台后端服务的设计与开发；
2. 参与接口设计与性能优化。

任职要求：
1. 统招本科及以上学历，计算机相关专业；
2. 3 年以上后端开发经验；
3. 熟悉 Python、FastAPI、MySQL，了解 Redis 优先；
4. 具备良好的沟通能力。
薪资：20k-35k
"""


class TestRuleExtraction:
    """规则提取（无模型通道）。"""

    def test_extracts_structured_fields(self):
        data = extract_by_rules(JD_TEXT)

        assert data["job_title"] == "后端开发工程师"
        assert data["min_education"] == "本科"
        assert data["min_work_years"] == 3
        assert data["location"] == "深圳"
        assert data["salary_range"] == "20k-35k"
        assert len(data["responsibilities"]) >= 2
        assert data["source"] == "manual"
        assert data["prompt_version"] == "rule-v1"

    def test_preferred_half_sentence_does_not_downgrade_required_skills(self):
        """「…MySQL，了解 Redis 优先」：Python/FastAPI/MySQL 必须是必备。"""
        data = extract_by_rules(JD_TEXT)

        required = {item.lower() for item in data["required_skills"]}
        preferred = {item.lower() for item in data["preferred_skills"]}

        assert {"python", "fastapi", "mysql"} <= required
        assert "redis" in preferred
        # 同一技能不得同时出现在两类条件里
        assert not (required & preferred)

    def test_noise_tokens_are_not_skills(self):
        """薪资串与职责描述里的能力词不能被当成技能标签。"""
        data = extract_by_rules(JD_TEXT)
        all_skills = {item.lower() for item in data["required_skills"] + data["preferred_skills"]}

        assert "k-35k" not in all_skills
        assert "35k" not in all_skills
        assert "性能优化" not in all_skills

    def test_empty_text_rejected(self):
        with pytest.raises(ParamError):
            extract_by_rules("   ")


class TestNormalization:
    """标准化：缺失 / 冲突 / 脏字段处理。"""

    def test_missing_fields_not_filled(self):
        profile = normalize_job_profile(
            {"job_title": "产品经理", "required_skills": [], "min_education": None, "min_work_years": None},
            source="dify",
        )

        assert profile.min_education is None
        assert profile.min_work_years is None
        assert "min_education" in profile.missing_fields
        assert "min_work_years" in profile.missing_fields
        assert "required_skills" in profile.missing_fields
        assert profile.review_required is True

    def test_skill_overlap_is_conflict(self):
        profile = normalize_job_profile(
            {
                "job_title": "后端开发工程师",
                "required_skills": ["Python", "MySQL"],
                "preferred_skills": ["python"],
                "min_education": "本科",
                "min_work_years": 3,
            }
        )

        assert profile.conflicts
        assert any("同时出现" in item for item in profile.conflicts)
        assert profile.review_required is True

    def test_unknown_education_is_conflict(self):
        profile = normalize_job_profile(
            {
                "job_title": "后端开发工程师",
                "required_skills": ["Python"],
                "min_education": "本科及以上（985/211 优先）",
                "min_work_years": 3,
            }
        )

        # 词表命中「本科」，属于可标准化取值；构造一个完全无法识别的学历再验一次
        assert profile.min_education == "本科"

        profile2 = normalize_job_profile(
            {
                "job_title": "后端开发工程师",
                "required_skills": ["Python"],
                "min_education": "不限学历",
                "min_work_years": 3,
            }
        )
        assert profile2.min_education == "不限学历"
        assert any("学历取值无法识别" in item for item in profile2.conflicts)

    def test_title_mismatch_is_conflict(self):
        profile = normalize_job_profile(
            {"job_title": "前端开发工程师", "required_skills": ["Vue"]},
            job_title_hint="后端开发工程师",
        )
        assert any("不一致" in item for item in profile.conflicts)

    def test_unknown_keys_are_dropped(self):
        """Dify 可能带回调试字段，不能因为多字段直接 500。"""
        profile = normalize_job_profile(
            {
                "job_title": "后端开发工程师",
                "required_skills": ["Python"],
                "_debug": {"tokens": 123},
                "raw_output": "....",
            }
        )
        assert profile.job_title == "后端开发工程师"

    def test_missing_title_rejected(self):
        with pytest.raises(ParamError):
            normalize_job_profile({"required_skills": ["Python"]})


class TestParseChannel:
    """通道选择与降级标注。"""

    async def test_rule_fallback_when_dify_not_configured(self):
        adapter = DifyWorkflowAdapter(api_key="", base_url="http://127.0.0.1/v1")

        profile, channel = await parse_jd(JD_TEXT, adapter=adapter)

        assert channel == "rule_fallback"
        assert profile.source == "manual"
        assert profile.prompt_version == "rule-v1"
        assert profile.review_required is True
        assert profile.job_title == "后端开发工程师"

    async def test_too_short_text_rejected(self):
        with pytest.raises(ParamError):
            await parse_jd("招后端")


# ============================================================================
# *kwq* 新增（MATCH-015）：抽象能力短语与重复条件行的精度回归。
# 背景：真实 JD 验收时发现「熟悉 MySQL 数据库设计与优化」被拆成 MySQL + 数据库设计
#      两个硬条件，导致 Python/FastAPI/MySQL 全中的候选人也被判 required_skills=FAIL。
# ============================================================================
CAPABILITY_JD = """岗位名称：后端开发工程师

任职要求：
1. 本科及以上学历，3 年以上后端开发经验；
2. 熟练掌握 Python，熟悉 FastAPI 框架；
3. 熟悉 MySQL 数据库设计与优化；
4. 有 Redis 缓存使用经验者优先；
5. 薪资 25k-35k，具体面议。"""


class TestSkillPrecision:
    """能力短语与条件行的精度控制。"""

    def test_capability_phrase_not_promoted_when_clause_has_tech(self):
        """「MySQL 数据库设计」只算 MySQL，不额外造一个「数据库设计」硬条件。"""
        raw = extract_by_rules(CAPABILITY_JD, job_title="后端开发工程师")

        assert "MySQL" in raw["required_skills"]
        assert "数据库设计" not in raw["required_skills"]
        assert "数据库设计" not in raw["preferred_skills"]

    def test_capability_phrase_kept_when_standalone(self):
        """整句只提能力时仍保留（不能因为怕误杀就把 JD 要求丢掉）。"""
        raw = extract_by_rules("任职要求：\n1. 熟悉数据库设计，能独立完成表结构设计。")

        assert "数据库设计" in raw["required_skills"]

    def test_salary_line_is_not_a_condition(self):
        """薪资已结构化到 salary_range，不应再进「待复核条件」。"""
        raw = extract_by_rules(CAPABILITY_JD, job_title="后端开发工程师")

        texts = [item["text"] for item in raw["required_conditions"]]
        assert all("25k" not in text for text in texts)

    def test_skill_covered_line_is_not_a_condition(self):
        """已抽出技能的句子不重复登记为模糊条件（否则人人被打上待复核）。"""
        raw = extract_by_rules(CAPABILITY_JD, job_title="后端开发工程师")

        texts = " ".join(item["text"] for item in raw["required_conditions"])
        assert "MySQL" not in texts
        assert "FastAPI" not in texts

    def test_skill_only_jd_has_no_missing_condition(self):
        """技能就是结构化条件：全部可结构化时不应报「缺 required_conditions」。"""
        profile = normalize_job_profile(
            extract_by_rules(CAPABILITY_JD, job_title="后端开发工程师"),
            job_title_hint="后端开发工程师",
        )

        assert "required_conditions" not in profile.missing_fields
        assert "required_skills" not in profile.missing_fields
