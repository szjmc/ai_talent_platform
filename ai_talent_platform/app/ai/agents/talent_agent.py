"""
模块：app/ai/agents/talent_agent.py
职责：人才分析 Agent（生成人才画像）。

标签：[Agent组] [P1]  依赖：[架构组] BaseAgent（人才画像 / 盘点）

============================ 实现思路与代码要点 ============================
执行流程：
    接收 talent_id
      -> TalentService 读取人才档案 + 简历 + 测评 + 培训记录
      -> 组装 Prompt（config/prompt_config 或 ai/prompts）
      -> LLM 输出结构化画像（维度分 / 标签 / 优势 / 短板 / 建议岗位 / 潜力）
      -> Pydantic 校验输出，字段缺失或类型错误时重试或降级
      -> 写入 talent_portraits 表
      -> 返回画像摘要"""


"""gxd-人才画像Agent；根据档案事实生成可追溯标签和画像。"""
import json
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.adapters.llm.base_llm import ChatMessage
from app.ai.utils.output_parser import parse_model
from app.schemas.talent import TalentPortrait
from app.ai.utils.talent_tags import tag_catalog, normalize_labels
from app.config.prompt_config import GXD_PROMPT_VERSION

@register_agent("talent")
class TalentAgent(BaseAgent):
    prompt_name = "talent_portrait_gxd"
    purpose = "talent"
    persist_result = False
    temperature = 0.0

    @property
    def prompt_version(self):
        """gxd-返回画像业务提示词版本。"""
        return GXD_PROMPT_VERSION

    async def build_prompt(self, payload):
        """gxd-优先读取已鉴权档案，组装画像资料与Schema。"""
        self.context.require_permission("talent:read")
        if payload.get("talent_id"):
            from app.services.talent_profile_service import TalentService
            source = await TalentService(self.db, context=self.context).profile_source(payload["talent_id"])
        else:
            source = payload.get("source", "")
        from app.config.talent_config import talent_settings
        from app.core.exceptions import ParamError
        if not isinstance(source, str) or not source.strip():
            raise ParamError("缺少可用的人才资料")
        if len(source) > talent_settings.max_text_chars:
            raise ParamError("人才资料超过单次画像上限，请整理重复简历后重试")
        self.source = source
        return [ChatMessage.user(json.dumps({"schema": TalentPortrait.model_json_schema(), "source": source, "allowed_tags": tag_catalog()}, ensure_ascii=False))]

    async def parse_output(self, raw):
        """gxd-验证标签原文证据，未建立评分标准时不采纳维度分与潜力评级。"""
        parsed = parse_model(raw, TalentPortrait)
        parsed.labels = normalize_labels(parsed.labels, self.source)
        if parsed.dimensions or parsed.potential_level:
            parsed.warnings.append("未配置统一评价标准，维度分与潜力评级未采纳")
        parsed.dimensions = {}
        parsed.potential_level = None
        return parsed.model_dump(mode="json")

    async def persist(self, result, payload):
        """gxd-画像由TalentService保存版本，Agent只生成结果。"""
        return None
