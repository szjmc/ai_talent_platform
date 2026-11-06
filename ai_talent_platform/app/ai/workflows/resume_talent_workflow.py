"""
模块：app/ai/workflows/resume_workflow.py
职责：简历解析工作流（上传 -> 文本提取 -> 清洗 -> LLM 结构化 -> 校验 -> 落库）。

标签：[Agent组] [P0]  依赖：[架构组] resume_* 技能（上传 → 解析 → 查重 → 入库，幂等）

============================ 实现思路与代码要点 ============================
节点编排：
    extract_text   PDF/Word/图片 -> 文本（pdfplumber / python-docx / OCR）
        ↓
    clean_text     去除页眉页脚、多余空行、乱码
        ↓
    parse_llm      resume_agent（结构化 JSON）
        ↓
    validate       校验必填字段与格式（时间、手机号），失败 -> retry（最多 2 次）
        ↓
    persist        ResumeService 写回 parsed_data，绑定/创建 talent 档案
        ↓
    portrait       触发 talent Agent 生成画像（可并行）
状态字段：resume_id, file_path, text, parsed, warnings, status, retries
注意：
- 全流程写日志（trace_id 贯通），并在 resume 表记录阶段耗时；
- 单节点失败不影响已完成阶段，可从检查点续跑。
待实现清单：
    - [ ] 各节点函数实现
    - [ ] 条件边（validate -> retry / persist）
    - [ ] 状态持久化与失败恢复
=========================================================================
"""

"""gxd-沿用LangGraph编排简历提取、结构化、保存、查重与档案绑定。"""
from typing import TypedDict
from langgraph.graph import StateGraph, END
from app.services.talent_resume_service import ResumeService
from app.ai.agents.resume_agent import ResumeAgent
from app.core.exceptions import AIError

class ResumeState(TypedDict, total=False):
    text: str
    parsed: dict
    meta: dict
    result: dict

async def run_resume_workflow(db, resume_id, context, force=False, talent_id=None):
    """gxd-请求内按顺序执行工作流，失败由Service保存点回滚并记录解析错误。"""
    resumes = ResumeService(db, context=context)
    reused = await resumes.prepare_parse(resume_id, force)
    if reused:
        return reused

    async def extract(state):
        """gxd-读取已鉴权的原文件并保存按页原文。"""
        return {"text": await resumes.source_text(resume_id)}

    async def parse(state):
        """gxd-运行解析Agent并拒绝未通过结构校验的模型结果。"""
        result = await ResumeAgent(db=db, context=context).run({"text": state["text"]})
        if not result.get("ok"):
            raise AIError("简历结构化失败，请检查模型输出或稍后重试")
        return {"parsed": result["result"], "meta": {"model": result.get("model"), "prompt_version": "gxd-v1.0.0"}}

    async def persist(state):
        """gxd-保存可信字段及异常提示，再决定创建、绑定或等待复核。"""
        return {"result": await resumes.finalize_parse(resume_id, state["parsed"], state["meta"], talent_id)}

    graph = StateGraph(ResumeState)
    graph.add_node("extract", extract)
    graph.add_node("parse", parse)
    graph.add_node("persist", persist)
    graph.set_entry_point("extract")
    graph.add_edge("extract", "parse")
    graph.add_edge("parse", "persist")
    graph.add_edge("persist", END)
    result = await graph.compile().ainvoke({"text":""})
    return result["result"]
