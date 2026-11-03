"""
模块：app/ai/prompts/assessment_prompt.py
职责：测评研判提示词。

============================ 实现思路与代码要点 ============================
1. 提示词是「代码资产」，必须版本化：每次调整递增 PROMPT_VERSION，并记录到结果表，
   便于效果对比与问题回溯；
2. 统一来源：建议所有提示词最终收敛到 app/config/prompt_config.py 的 PromptRegistry，
   本文件作为业务场景的再导出（from app.config.prompt_config import prompt_registry），
   避免两处定义漂移；
3. 接入 Dify 时：远端模板覆盖本地默认值，本地作为兜底；
4. 编写提示词的原则：
   - 明确角色、任务、输出格式（JSON）；
   - 约束「不许编造」，不确定返回 null；
   - 给出 1-2 个 few-shot 示例（长提示词注意 token 成本）；
   - 输出字段与 Pydantic Schema 严格对应。
待实现清单：
    - [ ] 提示词常量（含 few-shot）
    - [ ] 渲染函数（填充变量）
    - [ ] 版本号常量
=========================================================================
"""

from __future__ import annotations

from app.config.prompt_config import PROMPT_VERSION, prompt_registry

# 复用集中管理的提示词（修改提示词请改 app/config/prompt_config.py）
PROMPT_NAME = "assessment"

__all__ = ["PROMPT_NAME", "PROMPT_VERSION", "prompt_registry"]
