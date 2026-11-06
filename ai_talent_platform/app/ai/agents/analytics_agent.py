"""
模块：app/ai/agents/analytics_agent.py
职责：人才数据决策 Agent（综合分析，输出人才研判结论与决策建议）。

标签：[Agent组] [P2]  依赖：[架构组] BaseAgent + [Vanna组] 问数能力

============================ 实现思路与代码要点 ============================
执行流程：
    接收用户问题 question
      -> build_prompt 调 run_analysis（取数 -> 分析 -> 拼提示词）
      -> 取不到数据：返回拒答消息，不调 LLM（防幻觉）
      -> 取到数据：把结构化 prompt 交给 BaseAgent.run 调 LLM（只调一次）
      -> parse_output 解析 LLM 返回的结构化 JSON（结论 + 建议）
      -> 不落库（纯问答，persist_result = False）

职责边界：
    - 取数只经 sql_skill.query_database（Vanna 对 Agent 唯一出口），不直连数据库；
    - LLM 调用统一交给基座 run 执行，本 Agent 不重复调 LLM（避免 token 浪费）；
    - 权限从 context 注入，由 sql_skill 内部读取，本 Agent 不手动透传 user_scope。
待实现清单：
    - [x] 继承 BaseAgent，实现 build_prompt / parse_output
    - [x] 在 agent_registry 注册（@register_agent("analytics")）
=========================================================================
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.adapters.llm.base_llm import ChatMessage
from app.ai.agents.base_agent import BaseAgent, register_agent
from app.ai.workflows.talent_decision_report import run_analysis


@register_agent("analytics")
class AnalyticsAgent(BaseAgent):
    """人才数据决策 Agent：综合分析人才 / 岗位 / 测评 / 培训数据，给出决策建议。"""

    name = "analytics"
    purpose = "summary"          # 复用 model_selector 中已配置的 summary 档
    persist_result = False       # 纯问答，不写业务表

    system_prompt = (
        "你是一名人才数据分析师，负责根据人才、岗位、测评、培训等数据"
        "给出分析结论与决策建议。只能基于查询到的真实数据作答，严禁编造。"
    )

    def __init__(self, db: Any | None = None, user: Any | None = None, context: Any | None = None) -> None:
        super().__init__(db=db, user=user, context=context)
        # 暂存 workflow 结果，供 parse_output 读取（避免每次 getattr 兜底）
        self._report: dict[str, Any] = {}
        # 第二次 LLM 业务推理产出的业务元数据（report_meta）；失败时为 None
        self._report_meta: dict[str, Any] | None = None
        # 多轮追问标记：True 表示本次是「基于上次报告追问」，走纯文字回答
        self._followup: bool = False
        # 下钻标记：True 表示本次是「追问明细名单」，重新查库返回明细
        self._drilldown: bool = False
        self._conversation_id: str | None = None
        # 周期报告类型（weekly/monthly/yearly）；None 表示普通单次分析
        self._report_type: str | None = None

    async def build_prompt(self, payload: dict[str, Any]) -> list[ChatMessage]:
        """取数据 + 触发第二次业务推理 + 组装最终提示词。

        【架构说明】本方法在末尾会触发一次网络 IO（第二次 LLM 调用），
        这是受 BaseAgent.run 固定流程（build_prompt -> chat -> parse_output）限制、
        无法插入中间钩子的妥协；所有 LLM 逻辑已隔离到 _build_business_meta，禁止内联。

        【多轮问答】若 payload 带 conversation_id 且进程内存里有该会话的上次报告，
        则不走「重新取数」流程，直接进入追问分支（_build_followup_prompt），
        让 LLM 基于上次报告回答用户问题。

        传进：payload（含 question 字段，可选 conversation_id）
        干了：读 context 权限档，调 run_analysis 完成取数/出图/拼提示词，结果暂存到 _report；
              再调 _build_business_meta 做第二次业务推理，结果暂存 _report_meta
        传出：交给基座调 LLM 的 messages 列表
        """
        question = str((payload or {}).get("question", "") or "").strip()

        # 周期报告类型：weekly / monthly / yearly；非法值一律当作普通分析
        report_type = str((payload or {}).get("report_type") or "").strip() or None
        from app.ai.workflows.talent_decision_report import PERIOD_REPORT_TEMPLATE

        if report_type not in PERIOD_REPORT_TEMPLATE:
            report_type = None
        self._report_type = report_type

        # 周期报告：由系统定时触发，不接受用户自然语言提问；
        # question 为空是正常的，取数走 run_analysis 内部的固定查询清单（QUERY_GROUP_MAP）。
        if report_type is None and not question:
            raise ValueError("question 不能为空")

        conversation_id = str((payload or {}).get("conversation_id") or "").strip() or None

        # 周期报告无需用户提问，用周期标签作为 question 占位（仅用于报告标识，不参与取数）
        if report_type and not question:
            from app.ai.workflows.talent_decision_report import period_label

            question = f"生成人才决策{PERIOD_REPORT_TEMPLATE[report_type]['label']}（{period_label(report_type)}）"

        # 权限档从 context 读取（Router 入口已 set_context），不手动透传 user_scope
        from app.ai.context import get_context

        data_scope = get_context().data_scope

        # --- 多轮问答分支：会话里已有报告，且用户是在追问 ---
        if conversation_id:
            from app.ai.memory.conversation_memory import ConversationMemory

            mem = ConversationMemory(conversation_id)
            recent = await mem.get_recent(limit=2)
            last_report = self._report_from_memory(recent)
            if last_report:
                # 记下「这是追问」，供 parse_output 走纯文字回答分支
                self._followup = True
                self._conversation_id = conversation_id
                # 下钻：用户追问的是「明细名单」时，重新查库拿明细，而不是只基于旧报告答
                if self._is_drilldown(question):
                    self._drilldown = True
                    return await self._build_drilldown_prompt(question)
                return self._build_followup_prompt(last_report, question)

        report = await run_analysis(question, data_scope=data_scope, report_type=report_type)
        self._report = report

        if not report.get("answered"):
            # 取不到数据：拒答，直接返回拒答文本（基座拿到后不会再调 LLM 也能正常返回）
            return [ChatMessage.user(report.get("message", "没有查询到相关数据。"))]

        # 第二次 LLM 调用：业务推理出 report_meta（成功则存 self._report_meta，失败为 None）
        self._report_meta = await self._build_business_meta(report)

        # 生成报告后，把「业务推理结果」存进会话记忆，供后续追问
        # 注意：这里存 report_meta（含 conclusions/risks/suggestions），不是原始 report，
        # 因为 report 里的 conclusions 在第二次 LLM 之前还是空的。
        if conversation_id:
            from app.ai.memory.conversation_memory import ConversationMemory

            mem = ConversationMemory(conversation_id)
            summary = self._report_meta if self._report_meta is not None else self._report_summary(report)
            await mem.append({
                "role": "assistant",
                "content": json.dumps(summary, ensure_ascii=False),
                "meta": {"type": "talent_report"},
            })

        if self._report_meta is not None:
            # 成功：给基座返回「透传占位」prompt，让基座第三轮几乎不干活，
            # 直接把第二次产出的 JSON 原样返回；parse_output 会优先用 self._report_meta。
            passthrough = json.dumps(self._report_meta, ensure_ascii=False)
            return [ChatMessage.user(
                "业务元数据已生成，请原样返回以下 JSON，不要做任何修改或解释：\n" + passthrough
            )]

        # 失败降级：返回原完整 prompt，基座走老逻辑生成文字结论（旧版流程兜底）
        return [ChatMessage.user(report["prompt"])]

    @staticmethod
    def _is_drilldown(question: str) -> bool:
        """判断追问是不是「要明细名单」类下钻。

        传进：question（用户追问）
        干了：检查是否含「要具体是谁/哪些人/名单/明细」等措辞
        传出：True 表示要下钻查明细，False 表示普通答疑追问
        """
        keywords = (
            "谁", "哪些人", "哪个人", "名单", "明细", "具体是",
            "分别是", "有哪些", "部门", "哪几个", "列出来",
        )
        return any(k in question for k in keywords)

    async def _build_drilldown_prompt(self, question: str) -> list[ChatMessage]:
        """下钻追问：重新调 query_database 查明细，再交给 LLM 整理回答。

        传进：question（用户追问的明细问题，如「这17次待提升的是谁」）
        干了：调 query_database 自然语言查库（复用 Vanna 组的 NL2SQL + 权限过滤），
              把列名 + 明细行拼进提示词，让 LLM 以易读方式呈现
        传出：交给基座调 LLM 的 messages 列表；查库失败则降级成「请重试」提示
        """
        from app.ai.skills.sql_skill import query_database

        try:
            detail = await query_database(question, max_rows=50)
        except Exception:
            # 查库失败（Vanna 抖动/权限/超时）：降级，交给 LLM 说明无法下钻
            return [ChatMessage.user(
                f"用户想进一步查看明细：{question}\n"
                "但当前无法查询到明细数据，请如实告知用户「明细数据暂不可用」，不要编造名单。"
            )]

        columns = detail.get("columns") or []
        rows = detail.get("rows") or []
        if not columns or not rows:
            return [ChatMessage.user(
                f"用户想进一步查看明细：{question}\n"
                "但查询结果为空，请如实告知用户「未查询到符合条件的明细」，不要编造名单。"
            )]

        # 把明细数据格式化，交给 LLM 用自然语言讲清楚（可顺带提炼规律）
        header = "、".join(str(c) for c in columns)
        body_lines = ["\t".join(str(cell) for cell in row) for row in rows]
        body = "\n".join(body_lines)

        prompt = (
            f"用户对上一份报告做了下钻追问：{question}\n\n"
            f"以下是查到的明细数据（列：{header}）：\n{body}\n\n"
            "请把这份明细数据用易读的方式呈现给用户（例如逐条列出，或按规律归纳）。"
            "数字和姓名必须与上面的数据完全一致，严禁编造数据里没有的内容。"
        )
        return [ChatMessage.user(prompt)]

    def _build_followup_prompt(self, last_report: dict[str, Any], question: str) -> list[ChatMessage]:
        """多轮追问：把上次报告的关键内容拼成上下文，让 LLM 回答用户追问。

        传进：last_report（内存里存的上次报告）+ question（用户追问）
        干了：从上次报告里抽出「结论 + 风险 + 关键数字」拼成上下文，附上用户问题
        传出：交给基座调 LLM 的 messages 列表

        说明：追问只做「解释/答疑」，不重新查库、不算风险、不生成图表，
        因此直接把上次报告的文字信息塞进 prompt 即可。
        """
        # 从上次报告里挑出有价值的信息拼成上下文
        ctx_parts: list[str] = []

        conclusions = last_report.get("conclusions") or []
        if conclusions:
            ctx_parts.append("【报告结论】\n" + "\n".join(f"- {c}" for c in conclusions))

        risks = last_report.get("risk_list") or last_report.get("risks") or []
        if risks:
            risk_lines = []
            for r in risks:
                name = r.get("name") or r.get("risk") or ""
                pheno = r.get("phenomenon") or ""
                priority = r.get("priority") or r.get("level") or ""
                risk_lines.append(f"- [{priority}] {name}：{pheno}")
            ctx_parts.append("【报告风险】\n" + "\n".join(risk_lines))

        suggestions = last_report.get("suggestion_p0") or []
        suggestions += last_report.get("suggestion_p1") or []
        if suggestions:
            ctx_parts.append("【报告建议】\n" + "\n".join(f"- {s}" for s in suggestions))

        # 如果上面啥都没抽到，退回用报告的原始数据兜底
        context = "\n\n".join(ctx_parts) if ctx_parts else "（上次报告无结构化摘要）"

        prompt = (
            f"以下是上一次人才决策报告的核心内容：\n{context}\n\n"
            f"用户现在追问：{question}\n"
            f"请基于上述报告内容回答用户的问题。只回答与报告相关的内容，"
            f"报告里没有的信息请明确说明「报告未涉及」，严禁编造。"
        )
        return [ChatMessage.user(prompt)]

    @staticmethod
    def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
        """把 run_analysis 返回的 report 压成「供追问复用的摘要」。

        传进：report（run_analysis 的完整返回）
        干了：只挑追问需要的字段（结论/风险/建议/关键数字），丢弃图表明细等大字段
        传出：精简 dict，JSON 序列化后存进会话记忆
        """
        data = report.get("data") or {}
        conclusions = data.get("report", {}).get("conclusions") or []
        risks = data.get("risks") or []
        return {
            "question": report.get("question", ""),
            "conclusions": conclusions,
            "risks": risks,
            "data_scope": data.get("data_scope", ""),
            "row_count": data.get("report", {}).get("row_count", 0),
        }

    @staticmethod
    def _report_from_memory(recent: list[dict[str, Any]]) -> dict[str, Any] | None:
        """从会话记忆的消息列表里，找回最近一份「报告摘要」。

        传进：recent（ConversationMemory.get_recent 返回的消息 dict 列表）
        干了：倒序找 role=assistant 且 meta.type=talent_report 的那条，解析出报告摘要
        传出：报告摘要 dict，找不到返回 None
        """
        for item in reversed(recent or []):
            meta = item.get("meta") or {}
            if meta.get("type") != "talent_report":
                continue
            content = item.get("content") or ""
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and (parsed.get("conclusions") or parsed.get("risks")):
                return parsed
        return None

    async def _build_business_meta(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """第二次 LLM 调用：把取数结果交给大模型做业务推理，产出结构化 report_meta。

        传进：run_analysis 返回的 report（其中 report["prompt"] 已是拼好的业务推理提示词）
        干了：组装 system+user 消息 -> 调 llm_manager.chat -> 清洗 markdown -> 解析 JSON
        传出：解析成功的 report_meta dict；任何失败（网络/超时/脏 JSON）返回 None，
              上层据此降级到旧版报告，绝不阻断主流程。

        说明：真实业务判断来源只有这里这一次调用；基座 BaseAgent.run 的第三次 chat
              仅是架构流程上必须走过的形式，其输出不作为主数据源。
        """
        from app.ai.core.llm_manager import llm_manager  # 局部导入，避免模块级循环依赖风险

        # 直接用 run_analysis 已拼好的业务推理 prompt 作为 user 内容，不重复拼装
        user_content = report.get("prompt") or ""
        if not user_content:
            return None

        messages = [
            ChatMessage.system(
                "你是人才数据分析师。请基于给定数据输出严格 JSON，"
                "只返回 JSON，不要输出任何解释、注释或 markdown 代码块。"
            ),
            ChatMessage.user(user_content),
        ]

        try:
            result = await llm_manager.chat(
                messages,
                purpose="talent_report_business_reasoning",
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                user_id=self.context.user_id,
                conversation_id=self.context.conversation_id,
                trace_id=self.context.trace_id,
            )
            raw = result.content
        except Exception:
            # 网络异常 / 超时 / 供应商报错：降级，返回 None 走旧版报告
            return None

        if not raw:
            return None

        # 清洗：剥离可能的 markdown ```json ... ``` 代码块标记
        cleaned = self._clean_json_text(raw)
        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None

        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _clean_json_text(raw: str) -> str:
        """清洗 LLM 返回文本：剥离 ```json / ``` 等 markdown 围栏，只留 JSON 本体。"""
        text = raw.strip()
        # 去掉开头的 ```json 或 ``` 和结尾的 ```
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        # 若仍有多余围栏，取第一个 { 到最后一个 } 之间
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        return text.strip()

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """解析基座返回的结构化 JSON 输出（conclusions + suggestions）。

        传进：raw（基座调 LLM 返回的文本）
        干了：容错解析 JSON，失败则降级保留原文
        传出：{answered, question, answer, conclusions, suggestions, ...}
        """
        report = getattr(self, "_report", {})

        # 多轮追问路径：直接返回 LLM 的文字回答，不做结构化解析
        if getattr(self, "_followup", False):
            return {
                "answered": True,
                "question": report.get("question", ""),
                "answer": raw or "",
                "mode": "drilldown" if getattr(self, "_drilldown", False) else "followup",
                "conversation_id": getattr(self, "_conversation_id", None),
            }

        # 拒答路径：没有走 LLM，raw 就是拒答文本
        if not report.get("answered"):
            return {
                "answered": False,
                "question": report.get("question", ""),
                "answer": raw or "",
            }

        # 【核心保护】真实业务来源是第二次调用的 self._report_meta；
        # 基座第三次 chat 只是架构流程必须走过的形式，其返回 raw 只做备用兜底。
        report_meta = getattr(self, "_report_meta", None)
        if isinstance(report_meta, dict) and report_meta:
            try:
                raw = json.dumps(report_meta, ensure_ascii=False)
            except Exception:
                # 序列化失败（极端情况 meta 内含不可序列化对象）：放弃使用 meta，回退基座原始输出
                pass

        # 解析结构化 JSON，失败降级保留原文（不炸流程）
        conclusions: list[str] = []
        suggestions: list[str] = []
        suggestion_p0: list[str] = []
        suggestion_p1: list[str] = []
        warn_if_no_action: str = ""
        group_summaries: dict[str, str] = {}
        risk_list: list[dict[str, Any]] = []
        chart_display: dict[str, str] = {}
        key_talent_top3: list[str] = []
        report_audience: str = "leader"
        meta: dict[str, Any] = {}
        appendix_note: str = ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                conclusions = [str(x) for x in (parsed.get("conclusions") or [])]

                # meta 元信息：报告用途 / 数据截止 / 数据范围 / 样本提示 / 阈值说明 / KPI 目标
                raw_meta = parsed.get("meta")
                if isinstance(raw_meta, dict):
                    meta = {str(k): v for k, v in raw_meta.items()}

                # suggestions 兼容两种结构：旧的["字符串"] 与 新的{p0/p1/warn}
                raw_sug = parsed.get("suggestions")
                if isinstance(raw_sug, dict):
                    suggestion_p0 = [str(x) for x in (raw_sug.get("p0") or [])]
                    suggestion_p1 = [str(x) for x in (raw_sug.get("p1") or [])]
                    warn_if_no_action = str(raw_sug.get("warn_if_no_action") or "")
                    suggestions = suggestion_p0 + suggestion_p1
                elif isinstance(raw_sug, list):
                    suggestions = [str(x) for x in raw_sug]

                gs = parsed.get("group_summaries")
                if isinstance(gs, dict):
                    group_summaries = {str(k): str(v) for k, v in gs.items()}

                rl = parsed.get("risk_list")
                if isinstance(rl, list):
                    risk_list = [
                        {
                            "name": str(r.get("name", "")),
                            "priority": str(r.get("priority", "")),
                            "phenomenon": str(r.get("phenomenon", "")),
                            "business_impact": str(r.get("business_impact", "")),
                            "drill_hint": str(r.get("drill_hint", "")),
                        }
                        for r in rl if isinstance(r, dict)
                    ]

                cd = parsed.get("chart_display")
                if isinstance(cd, dict):
                    chart_display = {str(k): str(v) for k, v in cd.items()}

                kt = parsed.get("key_talent_top3")
                if isinstance(kt, list):
                    key_talent_top3 = [str(x) for x in kt]

                if isinstance(parsed.get("report_audience"), str):
                    report_audience = parsed["report_audience"]

                if isinstance(parsed.get("appendix_note"), str):
                    appendix_note = parsed["appendix_note"]
        except (json.JSONDecodeError, TypeError):
            pass

        result: dict[str, Any] = {
            "answered": True,
            "question": report.get("question", ""),
            "answer": raw or "",
            "conclusions": conclusions,
            "suggestions": suggestions,
            "suggestion_p0": suggestion_p0,
            "suggestion_p1": suggestion_p1,
            "warn_if_no_action": warn_if_no_action,
            "group_summaries": group_summaries,
            "risk_list": risk_list,
            "chart_display": chart_display,
            "key_talent_top3": key_talent_top3,
            "report_audience": report_audience,
            "meta": meta,
            "appendix_note": appendix_note,
        }
        # 周期报告：把类型与周期标签透出，供前端展示（如「2026年第45周」）
        rt = getattr(self, "_report_type", None)
        if rt:
            result["report_type"] = rt
            result["period_label"] = report.get("period_label", "")
        # 附带报告元信息，供前端/导出模块拿原始数据 + 图表做渲染
        data = report.get("data") or {}
        for key in ("row_count", "sql", "analysis_type", "data_scope"):
            if key in data:
                result[key] = data[key]

        # 图表列表：数据驱动多图（有几组数据出几张图）
        result["charts"] = data.get("charts") or []
        result["chart_count"] = len(result["charts"])        # 分组图表：按「分析类型」归类后的结构，供前端按行分组渲染（每行配一句总结）
        result["chart_groups"] = data.get("chart_groups") or []

        # 风险清单：detect_risks 用规则算出的风险（数字），LLM 补充 priority + business_impact
        result["risks"] = data.get("risks") or []
        result["risk_count"] = len(result["risks"])

        # 缺失清单：哪些维度没查到数据
        result["missing"] = data.get("missing") or []
        return result
