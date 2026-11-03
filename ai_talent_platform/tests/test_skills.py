"""Skills 可编排技能组件库测试（不依赖 LLM / Milvus / PgSQL）。

标签：[架构组] [P0]（Skills 21 用例验收）
"""

from __future__ import annotations

import pytest

from app.ai.skills import (
    BaseSkill,
    EXPECTED_SKILLS,
    SkillResult,
    bootstrap_skills,
    compute_match_score,
    describe_skills,
    extract_skills_by_dictionary,
    get_skill,
    list_skills,
    list_tools,
)
from app.core.exceptions import NotFoundError


@pytest.fixture(autouse=True)
def _grant_permission(monkeypatch):
    """测试环境没有登录上下文，这里放开权限校验，只验证技能自身逻辑。"""
    monkeypatch.setattr("app.ai.skills.base.has_permission", lambda roles, required: True)


@pytest.fixture()
def _registered():
    return bootstrap_skills()


# ------------------------------------------------------------------ 注册与清单
def test_bootstrap_registers_all_skills(_registered):
    assert set(EXPECTED_SKILLS).issubset(set(_registered))


def test_bootstrap_is_idempotent(_registered):
    assert bootstrap_skills() == bootstrap_skills()


def test_every_skill_has_valid_tool_schema(_registered):
    for skill in list_skills():
        assert isinstance(skill, BaseSkill)
        assert skill.name and skill.description
        schema = skill.parameters
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert set(schema.get("required", [])) <= set(schema["properties"])
        tool = skill.to_openai_tool()
        assert tool["type"] == "function"
        assert tool["function"]["name"] == skill.name
        assert tool["function"]["parameters"] is schema


def test_describe_and_list_tools(_registered):
    assert {item["name"] for item in describe_skills()} >= set(EXPECTED_SKILLS)
    for item in describe_skills():
        assert {"name", "description", "category", "requires"} <= set(item)
    assert {tool["function"]["name"] for tool in list_tools()} >= set(EXPECTED_SKILLS)


def test_get_skill_unknown_raises_not_found(_registered):
    with pytest.raises(NotFoundError):
        get_skill("not_exists_skill")


# ------------------------------------------------------------------ 参数校验
@pytest.mark.asyncio
async def test_run_fills_defaults_and_drops_unknown_params(_registered):
    result = await get_skill("sql_query").run(question="各部门人才数量", hallucinated=1)
    # 依赖未实现，但参数解析本身应通过（失败原因是依赖而不是参数）
    assert result.error is None or "依赖未就绪" in (result.error or "")


@pytest.mark.asyncio
async def test_missing_required_param(_registered):
    result = await get_skill("knowledge_search").run()
    assert result.ok is False
    assert result.error_code == 1001
    assert "question" in (result.error or "")


@pytest.mark.asyncio
async def test_param_type_and_bounds(_registered):
    result = await get_skill("knowledge_search").run(question="考勤制度", top_k=999)
    assert result.ok is False
    assert result.error_code == 1001
    result = await get_skill("position_search").run(keyword="Python", status="unknown")
    assert result.ok is False
    assert result.error_code == 1001


@pytest.mark.asyncio
async def test_permission_is_checked_from_context(monkeypatch, _registered):
    monkeypatch.setattr("app.ai.skills.base.has_permission", lambda roles, required: False)
    result = await get_skill("knowledge_search").run(question="考勤制度")
    assert result.ok is False
    assert "无权" in (result.error or "")


# ------------------------------------------------------------------ 简历技能（离线可用）
def test_extract_skills_by_dictionary():
    text = "熟练使用 Python、FastAPI 与 MySQL，了解 Docker 与 Kubernetes，具备项目管理经验"
    skills = extract_skills_by_dictionary(text)
    assert {"Python", "FastAPI", "MySQL", "Docker", "Kubernetes", "项目管理"} <= set(skills)


def test_extract_skills_by_dictionary_empty():
    assert extract_skills_by_dictionary("") == []


@pytest.mark.asyncio
async def test_resume_skill_extract_executes_without_llm(_registered):
    result = await get_skill("resume_skill_extract").run(text="熟悉 Java、Spring Boot、Redis")
    assert result.ok is True
    assert {"Java", "Spring Boot", "Redis"} <= set(result.data)


@pytest.mark.asyncio
async def test_resume_parse_requires_identifier(_registered):
    result = await get_skill("resume_parse").run()
    assert result.ok is False
    assert result.error_code == 1001


# ------------------------------------------------------------------ 匹配技能
def test_compute_match_score_full_match():
    talent = {"skill_tags": ["Python", "FastAPI"], "work_years": 5, "education": "本科", "potential_level": "A"}
    position = {"skill_tags": ["Python", "FastAPI"], "requirements": {"min_work_years": 3, "min_education": "本科"}}
    score = compute_match_score(talent, position)
    assert score["dimensions"]["skill"] == 100
    assert score["dimensions"]["experience"] == 100
    assert score["dimensions"]["education"] == 100
    assert score["total_score"] == pytest.approx(99.5, abs=0.01)


def test_compute_match_score_with_gaps():
    talent = {"skill_tags": ["Java"], "work_years": 1, "education": "大专", "potential_level": "D"}
    position = {"skill_tags": ["Python", "MySQL"], "requirements": {"min_work_years": 5, "min_education": "硕士"}}
    score = compute_match_score(talent, position)
    assert score["gaps"] == ["mysql", "python"]
    assert score["total_score"] < 40


@pytest.mark.asyncio
async def test_matching_score_skill_pure_function_path(_registered):
    result = await get_skill("matching_score").run(
        talent={"id": 1, "skill_tags": ["Python"], "work_years": 4, "education": "本科", "potential_level": "B"},
        position={"id": 2, "skill_tags": ["Python", "MySQL"], "requirements": {"min_work_years": 3}},
    )
    assert result.ok is True
    assert result.data["source"] == "skill_pure_function"
    assert 0 <= result.data["total_score"] <= 100
    assert set(result.data["dimensions"]) == {"skill", "experience", "education", "quality"}


@pytest.mark.asyncio
async def test_matching_score_requires_ids_or_data(_registered):
    result = await get_skill("matching_score").run(talent_id=1)
    assert result.ok is False
    assert result.error_code in (1001, 5002)


# ------------------------------------------------------------------ SQL / 检索技能：依赖未就绪
@pytest.mark.asyncio
async def test_sql_query_blocks_dangerous_input(_registered):
    result = await get_skill("sql_query").run(question="drop table talents; select 1")
    assert result.ok is False
    assert result.error_code == 1001
    assert "不被允许" in (result.error or "")


@pytest.mark.asyncio
async def test_sql_query_reports_missing_dependency(_registered):
    result = await get_skill("sql_query").run(question="上个月新增人才数量")
    assert result.ok is False
    assert "依赖未就绪" in (result.error or "")
    assert "Vanna" in (result.error or "")


@pytest.mark.asyncio
async def test_knowledge_search_reports_missing_dependency(_registered):
    result = await get_skill("knowledge_search").run(question="考勤制度")
    assert result.ok is False
    assert "依赖未就绪" in (result.error or "")


# ------------------------------------------------------------------ 结果封装
def test_skill_result_contract():
    ok = SkillResult(skill="demo", ok=True, data={"a": 1})
    assert bool(ok) is True
    assert ok.to_dict()["ok"] is True
    failed = SkillResult(skill="demo", ok=False, error="boom", error_code=5002)
    assert bool(failed) is False
    with pytest.raises(Exception):
        failed.raise_for_error()
    assert ok.raise_for_error() is ok
