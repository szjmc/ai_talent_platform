"""
模块：app/ai/__init__.py
职责：AI 能力层入口。

标签：[架构组] [P1]（AI 能力层入口与硬性约束）

分层（与文档一致）：
    ai/core/      llm_manager（模型实例管理）、model_selector（按任务选模型）、token_manager（成本统计）
    ai/agents/    base_agent + 6 个业务 Agent（人才/简历/匹配/测评/培训/助手）
    ai/workflows/ LangGraph 工作流（简历处理、匹配、综合分析）
    ai/rag/       文档加载 -> 切分 -> 向量化 -> 检索 -> 组装上下文
    ai/memory/    会话记忆与工作流状态
    ai/skills/    Agent 可调用的技能（内部调用各业务 Service，保持权限一致）
    ai/prompts/   提示词（也可统一放 config/prompt_config.py，二选一并保持一致）
    ai/utils/     输出解析、重试

硬性约束：
1. Agent / Skill 不得直接访问数据库或绕过 Service 的权限校验；
2. 所有提示词必须版本化（记录 prompt_version 到结果表，便于复现）；
3. 外部模型调用统一经过 adapters/llm，不在业务代码里直接使用 SDK。
"""
