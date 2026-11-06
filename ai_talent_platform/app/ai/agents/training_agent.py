"""
模块：app/ai/agents/training_agent.py
职责：智能培训赋能 Agent —— 六节点中唯一接触大模型的环节。

标签：[Dify组] [P1]（培训 Agent：LLM 只排周次/维度/学时，不碰课程）

============================ 实现思路与代码要点 ============================
本模块只做两件事，且都**不掌握事实**：
    节点2 方案排期   -> 本 Agent 的 LLM 能力（只排周次/维度/学时，不碰课程）
    节点4 方案说明   -> 把已经算完的结论讲成人话

为什么 Agent 不负责课程匹配、落库、算分：
    这些环节必须可复算、可审计。LLM 只擅长表达与编排，不擅长确定性计算。

四条硬约束：
1. **拿不到课程库**：prompt 里根本没有课程信息，模型无从编造课程名；
2. **失败可兜底**：LLM 不可用 / 输出不合法 → Service 自动回退规则排期，链路不中断；
3. **结构必须校验**：parse_output 只做形状校验，语义约束由 legal_skeleton 复核；
4. **只读不写别人**：persist 复用 Service，不直接操作 ORM 之外的模块。
=========================================================================
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence

from app.adapters.llm.base_llm import ChatMessage
from app.ai.agents import training_logic as L
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.ai.utils.output_parser import parse_json
from app.config.prompt_config import prompt_registry
from app.config.settings import settings
from app.core.exceptions import AIError

logger = logging.getLogger(__name__)

# 内置适配器写死在类属性里的官方地址。
# 配置里的地址与这里不一致时，说明用户接了第三方兼容端点（如 DeepSeek / 硅基流动），
# 此时内置适配器会忽略 LLM_BASE_URL，必须改走本模块自建客户端。
BUILTIN_ENDPOINTS: dict[str, str] = {
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://127.0.0.1:11434",
}

# 方案说明的硬约束（写进 prompt，也在返回后做长度校验）
SUMMARY_MIN_CHARS = 40
SUMMARY_MAX_CHARS = 200

EXPLAIN_SYSTEM_PROMPT = (
    "你是企业培训 HR，负责把已经算好的培训方案讲给员工听。"
    "只能陈述给你的数据，不得新增、修改或推测任何数字、课程名与日期。"
    "不要输出标题、列表符号、markdown，只写一段连续中文。"
)


@register_agent("training")
class TrainingAgent(BaseAgent):
    """培训赋能 Agent：排期（LLM）+ 落库（Service）。"""

    name = "training"
    purpose = "planning"
    # 提示词模板带运行期占位符（max_weeks / gaps_json），不能直接当 system 用，
    # 因此 system 走类属性，模板在 build_prompt 里渲染成 user 消息。
    prompt_name = None
    system_prompt = (
        "你是企业培训规划师。只输出严格合法的 JSON，不要输出任何解释性文字或 markdown 代码块。"
    )
    temperature = 0.2
    persist_result = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Any | None = None

    # ============================================================ 对外主入口
    async def suggest_skeleton(
        self,
        gaps: Sequence[Any],
        *,
        max_weeks: int = 4,
        max_hours_per_week: float = 8.0,
    ) -> tuple[list[dict[str, Any]], str]:
        """只向 LLM 要排期骨架，不落库（供 Service 在 fallback 前尝试）。

        :return: (排期项列表, 实际使用的模型名)
        :raises AIError: 模型不可用、输出无法解析或结构不合法
        """
        payload = {
            "gaps": self._normalize_gaps(gaps),
            "max_weeks": int(max_weeks),
            "max_hours_per_week": float(max_hours_per_week),
        }
        messages = self._with_system_prompt(await self.build_prompt(payload))
        result = await self._chat(messages)
        parsed = await self.parse_output(getattr(result, "content", "") or "")
        model = getattr(result, "model", "") or settings.LLM_MODEL
        return parsed["skeleton"], model

    # ============================================================ 端点判定
    @classmethod
    def _use_custom_endpoint(cls) -> bool:
        """配置里的地址与内置适配器的官方地址不一致 → 走本模块自建客户端。

        为什么要这段：qwen / openai 适配器在 __init__ 里
        `kwargs.setdefault("base_url", self.default_base_url)`，官方地址被写死，
        只改 .env 的 LLM_BASE_URL 对它们完全无效。这里检出差异后显式构造客户端，
        **不需要修改公共层任何文件**。
        """
        provider = (settings.LLM_PROVIDER or "").lower().strip()
        base = (settings.LLM_BASE_URL or "").rstrip("/")
        if not base:
            return False
        builtin = BUILTIN_ENDPOINTS.get(provider)
        if builtin is None:
            # 未登记的供应商：只有 ollama 走本地默认，其余视为自定义端点
            return provider not in ("", "ollama")
        return base != builtin.rstrip("/")

    def _custom_client(self) -> Any:
        """惰性构造 OpenAI 兼容客户端（同实例复用连接池）。"""
        if self._client is None:
            from app.adapters.llm.openai_adapter import OpenAIAdapter

            self._client = OpenAIAdapter(
                model=settings.LLM_MODEL,
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
            )
            logger.info(
                "培训 Agent 使用自定义兼容端点：%s（model=%s）",
                settings.LLM_BASE_URL,
                settings.LLM_MODEL,
            )
        return self._client

    async def _chat(self, messages: list[ChatMessage]) -> Any:
        """统一对话入口：内置厂商走 llm_manager（保留熔断/追踪），兼容端点走专用客户端。"""
        if self._use_custom_endpoint():
            return await self._custom_client().chat(
                messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        from app.ai.core.llm_manager import llm_manager

        return await llm_manager.chat(
            messages,
            purpose=self.purpose,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            user_id=self.context.user_id,
            conversation_id=self.context.conversation_id,
            trace_id=self.context.trace_id,
        )

    # ============================================================ 节点 4 增强
    async def explain_plan(self, facts: dict[str, Any]) -> str:
        """节点 4 增强：让模型把「已经算完的结论」讲成人话。

        调用方传入的 facts 已包含全部数字与课程名；模型只做措辞。
        返回空串或过短由调用方回退模板文案（Service 侧已处理）。
        """
        messages = [
            ChatMessage.system(EXPLAIN_SYSTEM_PROMPT),
            ChatMessage.user(
                "以下是已经确定并落库的培训方案事实（JSON）：\n"
                + json.dumps(facts, ensure_ascii=False, indent=2)
                + f"\n\n请写一段 {SUMMARY_MIN_CHARS}~{SUMMARY_MAX_CHARS} 字的中文说明，"
                "覆盖：面向谁、补哪几项、多长周期多少学时、何时考核及格线多少。"
                "只使用上面出现过的课程名，没有课程名就不要提课程。"
            ),
        ]
        result = await self._chat(messages)
        return (getattr(result, "content", "") or "").strip()

    # ============================================================ 基类三件套
    async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]:
        """构造排期提示词：只给短板与约束，不给课程库。"""
        gaps = self._normalize_gaps(payload.get("gaps"))
        if not gaps:
            raise AIError("未提供可用的能力短板，无法生成排期")

        max_weeks = int(payload.get("max_weeks") or L.MAX_WEEKS)
        max_hours = float(payload.get("max_hours_per_week") or L.MAX_HOURS_PER_WEEK)

        ordered = sorted(
            gaps,
            key=lambda gap: (
                L.PRIORITY_RANK.get(str(gap.get("priority") or "P2"), 9),
                -float(gap.get("delta") or 0),
            ),
        )
        order_hint = "、".join(
            f'{gap["dimension"]}（{gap.get("priority", "P2")}，缺 {gap.get("delta", 0):g} 分）'
            for gap in ordered
        )
        gaps_payload = [
            {
                "dimension": gap["dimension"],
                "current": gap.get("current"),
                "required": gap.get("required"),
                "delta": gap.get("delta"),
                "priority": gap.get("priority"),
                "est_hours": gap.get("est_hours"),
            }
            for gap in ordered
        ]

        template = prompt_registry.get("training")
        content = template.format(
            max_weeks=max_weeks,
            max_hours=max_hours,
            order_hint=order_hint,
            gaps_json=json.dumps(gaps_payload, ensure_ascii=False, indent=2),
        )
        return [ChatMessage.user(content)]

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """解析并做结构校验：周次/维度/学时非法即抛 AIError（由基类重试）。"""
        data = parse_json(raw)

        if isinstance(data, list):
            rows, reasoning = data, ""
        elif isinstance(data, dict):
            rows = data.get("skeleton") or data.get("items") or []
            reasoning = str(data.get("reasoning") or "")
        else:
            raise AIError("模型输出既不是 JSON 对象也不是数组")

        if not isinstance(rows, list) or not rows:
            raise AIError("模型未返回任何排期项（skeleton 为空）")

        cleaned: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise AIError(f"第 {index} 项排期不是对象")
            dimension = str(row.get("dimension") or "").strip()
            if not dimension:
                raise AIError(f"第 {index} 项排期缺少 dimension")
            try:
                week = int(row.get("week"))
                hours = float(row.get("hours"))
            except (TypeError, ValueError) as exc:
                raise AIError(f"第 {index} 项排期的 week / hours 不是数字") from exc
            if week < 1:
                raise AIError(f"第 {index} 项排期周次非法：{week}")
            if hours <= 0:
                raise AIError(f"第 {index} 项排期学时非法：{hours}")
            cleaned.append(
                {
                    "week": week,
                    "dimension": dimension,
                    "hours": round(hours, 1),
                    "reason": str(row.get("reason") or "").strip(),
                }
            )

        return {"skeleton": cleaned, "reasoning": reasoning}

    async def persist(self, result: dict[str, Any], payload: dict[str, Any]) -> Any:
        """节点 3/4：课程匹配 + 建计划 + 推送记录 + 事件（复用 Service）。"""
        if self.db is None:
            return None

        skeleton = result.get("skeleton") or []
        gaps = payload.get("gaps") or []
        talent_id = payload.get("talent_id")
        if not skeleton or not gaps or not talent_id:
            # 缺落库必需信息时只返回排期结果，不建半截计划
            logger.debug("Agent persist 跳过：缺少 talent_id / gaps / skeleton")
            return None

        from app.schemas.training import TrainingPlanGenerateRequest
        from app.services.training_service import TrainingService

        request = payload.get("request")
        if not isinstance(request, TrainingPlanGenerateRequest):
            request = TrainingPlanGenerateRequest(
                talent_id=int(talent_id),
                gaps=[{"dimension": gap["dimension"], "current": gap.get("current")} for gap in gaps],
                max_weeks=int(payload.get("max_weeks") or L.MAX_WEEKS),
                max_hours_per_week=float(
                    payload.get("max_hours_per_week") or L.MAX_HOURS_PER_WEEK
                ),
                use_ai=True,
            )

        # TrainingService.__init__(db, repo=None) 不接受 user；
        # 落库操作人走 create_plan 的 operator_id（写入 TrainingPlan.created_by）
        service = TrainingService(self.db)
        plan = await service.create_plan(
            skeleton,
            gaps,
            request,
            generated_by="llm",
            model_name=getattr(self, "_last_model", None),
            operator_id=self.context.user_id,
        )
        return {"plan_id": plan.get("id"), "generated_by": plan.get("generated_by")}

    # ============================================================ 内部工具
    @staticmethod
    def _normalize_gaps(raw: Any) -> list[dict[str, Any]]:
        """把入参短板统一成算法层需要的结构（容忍 dict / Pydantic / 未诊断原始数据）。"""
        if not raw:
            return []
        plain: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, Iterable) else []:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            if isinstance(item, dict):
                plain.append(dict(item))
        if not plain:
            return []
        # 已经诊断过的（带 priority）也重算一次：阈值改了就应生效，不依赖上游是否缓存
        return L.diagnose_gaps(plain)
