"""
模块：app/config/prompt_config.py
职责：AI 提示词集中管理（版本化）。

标签：[架构组] [P1]（PromptRegistry，与 ai/prompts 合并去重）

实现思路：
1. 所有提示词以「常量 + 版本号」的形式集中在此文件，禁止散落在 Service / Router；
2. 提供 PromptRegistry，可按名称取提示词并渲染变量（str.format 风格）；
3. 便于后续接入 Dify Prompt 管理：把远端模板拉下来覆盖本文件默认值即可。

当前为骨架阶段，先放置核心场景模板，AI 模块开发时按需扩充。
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "v1.0.0"

# ---------------------------------------------------------------- 简历解析
RESUME_PARSE_PROMPT = """你是一名专业的人才档案分析助手。请根据提供的简历内容提取：
1. 姓名
2. 学历（最高学历）
3. 工作经历（公司、职位、起止时间、职责）
4. 技能标签
5. 项目经验
6. 工作年限

要求：
- 不允许编造简历中不存在的信息。
- 不确定的信息返回 null。
- 使用 JSON 结构输出，不要输出 JSON 以外的任何内容。
"""

# ---------------------------------------------------------------- 人岗匹配
MATCHING_PROMPT = """你是资深的人才测评专家。请对比【岗位要求】与【人才画像】，
输出 0-100 的匹配分，并给出：
1. 匹配维度拆解（技能/经验/学历/素质）
2. 候选人的优势亮点
3. 存在的短板
4. 适配建议

要求：所有结论必须能在给定材料中找到依据，使用 JSON 输出。
岗位要求：{position}
人才画像：{talent}
"""

# ---------------------------------------------------------------- 综合问答
ASSISTANT_PROMPT = """你是企业人才数字化平台的智能助手。
请基于【知识库检索结果】回答用户问题，规则：
1. 只能使用检索结果中的内容作答，检索结果为空时明确告知用户"知识库中未找到依据"；
2. 回答末尾列出引用来源（文档名 / 条文编号）；
3. 涉及个人隐私数据时，仅返回当前用户权限范围内的信息。

检索结果：{context}
用户问题：{question}
"""

# ---------------------------------------------------------------- 测评研判
ASSESSMENT_PROMPT = """你是人才能力测评分析师。请根据以下测评作答数据，
输出能力雷达维度得分、短板项、能力等级（初级/中级/高级/专家）与提升建议。
测评数据：{answers}
"""

# ---------------------------------------------------------------- 培训推荐
TRAINING_PROMPT = """你是企业培训规划师。请根据人才能力短板与岗位能力模型，
生成个性化培训方案（课程、周期、考核节点）。
能力差距：{gap}
岗位模型：{position_model}
"""

_PROMPTS: dict[str, str] = {
    "resume_parse": RESUME_PARSE_PROMPT,
    "matching": MATCHING_PROMPT,
    "assistant": ASSISTANT_PROMPT,
    "assessment": ASSESSMENT_PROMPT,
    "training": TRAINING_PROMPT,
}


class PromptRegistry:
    """提示词注册表：按名称获取 / 渲染提示词。"""

    def __init__(self, prompts: dict[str, str] | None = None) -> None:
        self._prompts = dict(prompts or _PROMPTS)

    def get(self, name: str) -> str:
        if name not in self._prompts:
            raise KeyError(f"未注册的提示词：{name}，可用：{sorted(self._prompts)}")
        return self._prompts[name]

    def render(self, name: str, **variables: Any) -> str:
        """渲染提示词变量；变量缺失时抛出 KeyError，便于尽早暴露问题。"""
        return self.get(name).format(**variables)

    def register(self, name: str, template: str) -> None:
        self._prompts[name] = template

    @property
    def version(self) -> str:
        return PROMPT_VERSION


prompt_registry = PromptRegistry()
