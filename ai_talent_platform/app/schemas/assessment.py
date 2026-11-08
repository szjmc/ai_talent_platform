"""
模块：app/schemas/assessment.py
职责：人才测评相关请求 / 响应结构（Pydantic）。

标签：[Vanna组] [P1]

============================ 说明 ============================
本文件先落地「测评分析 Agent」运行所需的核心结构：
  - AssessmentAgentInput   Agent 入参（dispatch 时传入）
  - QuestionItem           组卷后的题目（不下发标准答案）
  - RuleScoreItem          客观题规则评分结果
  - LLMScoreResult         Dify「语义打分」工作流返回
  - AnalyzeResult          Dify「短板研判」工作流返回
  - AssessmentReport       最终报告（落库 assessment_results.report）
后续 HTTP 接口层（创建/开始/提交/结果查询）按需在此文件增补即可。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ 入参
class AssessmentAgentInput(BaseModel):
    """测评分析 Agent 入参（POST /agents/tasks {type:"assessment"} 的 payload）。"""

    assessment_id: int = Field(..., description="测评任务 ID")
    talent_id: int = Field(..., description="人才 ID")
    position_id: int | None = Field(None, description="岗位 ID（可空）")
    job_level: str = Field("中级", description="岗位层级：初级/中级/高级/专家")
    profession: str = Field("数字化", description="专业方向")
    grade: str = Field("中级", description="人才等级：初级/中级/高级/专家")
    # 已提交的答题（客观题含标准答案可由规则判分；主观题交 Dify 语义打分）
    answers: list[dict] = Field(default_factory=list, description="答题列表")
    # 触发阶段：assign（组卷）/ full（提交后全流程打分分析）
    stage: Literal["assign", "full"] = Field("full", description="触发阶段")


# ------------------------------------------------------------------ assign（组卷）
class QuestionItem(BaseModel):
    """下发给前端的题目（绝不包含标准答案）。"""

    question_id: int
    type: Literal["single", "multiple", "text"] = "single"
    content: str
    options: list[dict] | None = None
    score: float = 5.0
    dimension: str | None = None
    sort: int = 0


class AssignOutput(BaseModel):
    """assign 节点（Dify 组卷工作流）返回。"""

    paper_id: str
    questions: list[QuestionItem]
    total_score: float = 0.0


# ------------------------------------------------------------------ stat（规则分）
class RuleScoreItem(BaseModel):
    """单题规则判分结果。"""

    question_id: int
    dimension: str | None = None
    rule_score: float = 0.0
    scored_by: Literal["rule", "ai"] = "rule"


# ------------------------------------------------------------------ score（Dify 语义打分）
class LLMScoreResult(BaseModel):
    """score 节点（Dify 语义打分工作流）返回：量化综合素质/专业能力/岗位适配度。"""

    comprehensive: float = Field(0.0, ge=0, le=100, description="综合素质分 0-100")
    professional: float = Field(0.0, ge=0, le=100, description="专业能力分 0-100")
    position_fit: float = Field(0.0, ge=0, le=100, description="岗位适配度 0-100")
    subjective_total: float = Field(0.0, ge=0, le=100, description="主观题语义总分")
    rationale: str = ""


# ------------------------------------------------------------------ analyze（Dify 短板研判）
class WeaknessItem(BaseModel):
    dimension: str
    score: float = 0.0
    gap: str = ""           # 与岗位标准的差距
    suggestion: str = ""    # 该短板的提升建议


class AnalyzeResult(BaseModel):
    """analyze 节点（Dify 短板研判工作流）返回。"""

    weakness: list[WeaknessItem] = Field(default_factory=list)
    ability_structure: dict = Field(default_factory=dict, description="能力结构雷达数据")
    level: str = Field("中级", description="建议等级：初级/中级/高级/专家")
    growth_potential: str = ""


# ------------------------------------------------------------------ report（最终报告）
class AssessmentReport(BaseModel):
    """最终个性化能力评估结论与提升建议（落库 assessment_results.report）。"""

    summary: str = ""
    total_score: float = 0.0
    level: str = "中级"
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    llm_scores: dict[str, float] = Field(default_factory=dict)
    weakness: list[dict] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    model_name: str = ""
    prompt_version: str = ""


__all__ = [
    "AnalyzeResult",
    "AssessmentAgentInput",
    "AssessmentReport",
    "AssignOutput",
    "LLMScoreResult",
    "QuestionItem",
    "RuleScoreItem",
    "WeaknessItem",
]


# ================================================================ HTTP 请求/响应 Schema
from datetime import datetime
from pydantic import field_validator
from app.schemas.common import BaseSchema, ORMModel  # noqa: E402


class AssessmentCreateRequest(BaseSchema):
    """POST /assessments 创建测评（仅需 file_id，其余信息全部从简历自动解析）。

    前端只需传入上传简历返回的 file_id，后端自动完成闭环：
        file_id -> 读取简历原件 -> 提取文本 -> 人才测评分析 Agent 结构化解析
        （应聘岗位 / 等级 / 学历 / 工作年限 / 邮箱 / 技能）
        -> 依据关键字匹配知识库、按难度组卷 -> 用解析出的邮箱自动推送笔试邀约邮件。
    """

    file_id: int = Field(..., gt=0, description="已上传简历文件 ID（唯一必填入参）")
    title: str = Field("人才能力测评", max_length=128, description="测评标题（可选）")
    question_count: int = Field(5, ge=1, le=50, description="题目数量（可选，默认 5）")
    deadline: datetime | None = Field(None, description="可选截止时间；带时区时转换为服务器本地时间")

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is not None:
                value = value.astimezone().replace(tzinfo=None)
            if value <= datetime.now():
                raise ValueError("截止时间必须晚于当前时间")
        return value



class AnswerItem(BaseSchema):
    """单条答题。"""

    question_id: int = Field(..., gt=0)
    answer: Any = Field(..., description="用户答案（单选为 'A'，多选为 ['A','C']，主观题为文本）")


class AnswerSubmitRequest(BaseSchema):
    """POST /assessments/{id}/submit 提交答案。"""

    answers: list[AnswerItem] = Field(..., min_length=1)


class AnalyzeRequest(BaseSchema):
    """POST /assessments/{id}/analyze 触发 AI 全流程研判（打分+短板+报告）。"""

    stage: str = Field("full", pattern="^(full|assign)$", description="full=全流程；assign=仅组卷")
