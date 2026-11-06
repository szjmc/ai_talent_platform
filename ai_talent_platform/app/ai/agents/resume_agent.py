"""
模块：app/ai/agents/resume_agent.py
职责：简历解析 Agent。

标签：[Agent组] [P0]  依赖：[架构组] BaseAgent + resume_* 技能（简历解析）

============================ 实现思路与代码要点 ============================
执行流程：

    接收简历文本（或文件 ID）
      -> 文本清洗（去除页眉页脚、乱码）
      -> LLM 结构化抽取（姓名/学历/经历/技能/项目/年限）
      -> 校验与补全（手机号格式、时间区间合理性）
      -> 返回 JSON 并由 ResumeService 落库、绑定人才档案
      -> 记录 confidence 与 warnings 供人工复核"""

"""gxd-简历解析Agent；保存由工作流唯一负责，避免BaseAgent重试时重复入库。"""

import json
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.adapters.llm.base_llm import ChatMessage
from app.ai.utils.output_parser import parse_model
from app.ai.utils.resume_normalizer import normalize
from app.schemas.talent_resume_schema import ResumeParsedData
from app.config.prompt_config import GXD_PROMPT_VERSION
from app.core.exceptions import ParamError

@register_agent("resume")
class ResumeAgent(BaseAgent):
    prompt_name = "resume_parse_gxd"
    purpose = "resume"
    persist_result = False
    temperature = 0.0

    @property
    def prompt_version(self):
        """gxd-标记本次业务提示词版本以便回溯。"""
        return GXD_PROMPT_VERSION

    async def build_prompt(self, payload):
        """gxd-从文本或已鉴权简历读取资料，附带结构Schema约束。"""
        self.context.require_permission("resume:parse")
        text = payload.get("text")
        if text is None and payload.get("resume_id"):
            from app.services.talent_resume_service import ResumeService
            text = await ResumeService(self.db, context=self.context).source_text(payload["resume_id"])
        if not isinstance(text, str) or not text.strip():
            raise ParamError("缺少有效简历文本")
        from app.config.talent_config import talent_settings
        if len(text) > talent_settings.max_text_chars:
            raise ParamError("简历文本超过单次解析上限")
        self.source = text
        return [ChatMessage.user(json.dumps({"schema": ResumeParsedData.model_json_schema(), "resume_text": text}, ensure_ascii=False))]

    async def parse_output(self, raw):
        """gxd-校验模型输出并执行原文核对、标准化和异常标记。"""
        parsed = parse_model(raw, ResumeParsedData)
        return normalize(parsed, self.source).model_dump(mode="json")

    async def persist(self, result, payload):
        """gxd-解析Agent不直接保存；统一交给工作流调用ResumeService。"""
        return None
