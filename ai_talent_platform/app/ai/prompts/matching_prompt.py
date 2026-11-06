"""
模块：app/ai/prompts/matching_prompt.py
职责：人岗匹配提示词（业务场景的集中再导出 + 渲染辅助）。

标签：[架构组] [P1]（被 [Agent组] matching_agent 依赖）

============================ 实现思路与代码要点 ============================
1. 提示词是「代码资产」，必须版本化：模板统一收敛到 app/config/prompt_config.py
   的 PromptRegistry，本文件只做业务再导出与渲染辅助，避免两处定义漂移；
2. 模板清单：
   - "matching"              单对（岗位 × 人才）研判，变量 {position} {talent}
   - "matching_run_summary"  跑批总结（matching_agent 使用），变量
                             {requirement_version} {stats} {results}
3. 接入 Dify 时：远端模板覆盖本地默认值，本地作为兜底；
4. 修改提示词请改 prompt_config.py 并保持 PROMPT_VERSION 递增。
=========================================================================
"""

from __future__ import annotations

from typing import Any

from app.config.prompt_config import PROMPT_VERSION, prompt_registry

# 复用集中管理的提示词（修改提示词请改 app/config/prompt_config.py）
PROMPT_NAME = "matching"                          # 单对研判模板
RUN_SUMMARY_PROMPT_NAME = "matching_run_summary"  # 跑批总结模板（matching_agent）


def render_run_summary(
    requirement_version: int, stats: dict[str, Any], results: list[dict[str, Any]]
) -> str:
    """渲染跑批总结模板（变量缺失直接抛 KeyError，尽早暴露问题）。"""
    return prompt_registry.render(
        RUN_SUMMARY_PROMPT_NAME,
        requirement_version=requirement_version,
        stats=stats,
        results=results,
    )


__all__ = [
    "PROMPT_NAME",
    "PROMPT_VERSION",
    "RUN_SUMMARY_PROMPT_NAME",
    "prompt_registry",
    "render_run_summary",
]
