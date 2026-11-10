"""
模块：app/ai/prompts/interview_prompt.py
职责：AI 数字人面试提示词再导出（问题计划 + 评估反馈）。

标签：[Agent组] [P1]（AI 面试提示词，被 AiInterviewService 依赖）

说明：
- 提示词正文集中在 app/config/prompt_config.py，本文件只做业务场景再导出，
  避免「两份提示词各改各的」漂移；
- 规则引擎负责量化打分，提示词只允许做问题个性化与有依据的评语润色，
  禁止模型修改分数或编造候选人经历（见 prompt_config 中的硬性规则）。
"""

from __future__ import annotations

from app.config.prompt_config import PROMPT_VERSION, prompt_registry

PLAN_PROMPT_NAME = "ai_interview_plan"
FEEDBACK_PROMPT_NAME = "ai_interview_feedback"

__all__ = ["PLAN_PROMPT_NAME", "FEEDBACK_PROMPT_NAME", "PROMPT_VERSION", "prompt_registry"]
