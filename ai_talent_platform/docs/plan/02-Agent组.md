# 02. 2 组（AI Agent 多智能体，4 人）— 20 个工作日计划

> 隶属：[AI 能力层与业务层分组开发总计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md) ｜ **必读边界红线**：[六大研发组职责边界与防重复开发总规范 §5.2](../group_boundaries_and_tasks.md#52-2-组agent-组-agent组)
> 人力：4 人（AG-A 图引擎与对话服务 / AG-B 三大工作流 / AG-C Agent 1 / AG-D Agent 2）

---

## 本组速览与严格边界（防重复必读！）

- **组别**：2 组 `[Agent组]`（标签：`标签：[Agent组] [P0|P1|P2]`）
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **⚠️ 已落地冻结底座警告（以下文件已由 `[架构组]` 100% 实现并通过单测，本组直接 `import`/继承，严禁重写或覆盖！）**：
  - ✅ `app/ai/agents/base_agent.py`（`BaseAgent` 基类与 `@register_agent` 装饰器已就绪）
  - ✅ `app/ai/agent.py`（`AGENT_TYPES`、`dispatch`、`dispatch_stream` 统一调度器已就绪）
  - ✅ `app/ai/memory/conversation_memory.py`（`ConversationMemory` 短期 Redis + DB 回填 + 摘要压缩已就绪）
  - ✅ `app/ai/memory/state_manager.py`（`StateManager` / `state_manager` 工作流状态持久化已就绪）
  - ✅ `app/models/conversation.py`（`Conversation` / `ChatMessage` ORM 模型已就绪）
  - ✅ `app/ai/skills/*`（8 大标准技能已就绪，含 `compute_match_score` 与 `extract_skills`）
- **⚠️ 跨组严禁越界清单**：
  - ❌ **严禁**编写或修改 `app/services/{resume,talent,position,matching,assessment,training}_service.py`（这 6 个业务 Service **归属 `[业务组]`**！本组只负责 `app/services/agent_service.py`，其余业务 Service 只准调用其公开方法）；
  - ❌ **严禁**在工作流或 Agent 中自己写 PDF/Word 解析（统一调用 `[RAG组] app.ai.rag.document_loader.load`）；
  - ❌ **严禁**重写人岗匹配打分逻辑（统一复用 `[架构组] compute_match_score` / `MatchStrategyFactory`，`matching_agent` 只生成可解释评语，不得篡改规则总分）；
  - ❌ **严禁**在 `analysis_workflow` 或 `assistant_agent` 中写裸 SQL 或直连 PgSQL/Milvus（查制度走 `knowledge_search` 技能，查统计数据走 `sql_query` 技能或 `[Vanna组] AnalyticsService`）。

---

## 一、人员分工

| 角色 | 独占负责文件（禁止跨界改已落地底座） |
| --- | --- |
| AG-A（引擎与服务接口） | `app/ai/workflows/graph_builder.py`、`app/repositories/conversation_repository.py`、`app/services/agent_service.py`、`app/routers/agent_router.py`、新增 `app/schemas/agent.py` |
| AG-B（工作流） | `app/ai/workflows/resume_workflow.py`、`app/ai/workflows/matching_workflow.py`、`app/ai/workflows/analysis_workflow.py` |
| AG-C（Agent 1） | `app/ai/agents/resume_agent.py`、`app/ai/agents/talent_agent.py`（继承 `[架构组] BaseAgent`） |
| AG-D（Agent 2） | `app/ai/agents/assistant_agent.py`、`app/ai/agents/matching_agent.py`、`app/ai/agents/assessment_agent.py`、`app/ai/agents/training_agent.py` |

## 二、每日任务（D1–D20，每行末尾是当日验收标准）

| 天 | AG-A 引擎 | AG-B 工作流 | AG-C Agent | AG-D Agent | 当日验收 |
| --- | --- | --- | --- | --- | --- |
| D1 | `WorkflowState` TypedDict 规范（各工作流统一 errors/retries/status） | 画出 3 条工作流的节点图（与架构组评审） | 与架构组对齐 `BaseAgent` 契约 | 与架构组对齐 `BaseAgent` 契约 | 节点图 + state 定义签字 |
| D2 | `build_graph()` 封装（nodes/edges/entry_point/conditional_edges） | `resume_workflow` 节点骨架（extract→clean→llm→validate→persist） | `resume_agent.build_prompt`（读文件→文本→Prompt） | `assistant_agent` 骨架（依赖 1 组 RAG，先 fake） | `build_graph` 单测：3 节点直连跑通 |
| D3 | 条件边 + 重试节点工具（`route_by_status`） | resume_workflow 条件边（validate 失败→retry ≤2 次） | `resume_agent.parse_output`（JSON 容错 + Pydantic） | `talent_agent.build_prompt`（人才档案→画像 Prompt） | 简历文本 → 结构化 JSON 成功 10 条 |
| D4 | `run_workflow(graph, initial_state, thread_id)` | resume_workflow 全链路跑通（mock LLM） | `resume_agent.persist`（写 `resumes.parsed_data`、`talents` 主档） | `talent_agent.parse_output/persist`（写 `talent_portraits`） | 简历落库 + 画像落库 |
| D5 | checkpointer 装配（Memory → Redis → MySQL 三级降级） | matching_workflow：岗位解析→硬性过滤→向量召回 | `matching_agent` 骨架 | `assessment_agent` 骨架 | 断点续跑演示：中断后从 checkpoint 恢复 |
| D6 | `state_manager` save/load/delete + 序列化 | matching_workflow：打分→解释→落库（`matching_records`） | `resume_agent` 接真 LLM（qwen-plus）验证 | `assessment_agent`：题目生成 + 结果研判 | 真 LLM 简历解析耗时/准确率记录 |
| D7 | 并发控制（`asyncio.Semaphore`）+ 超时降级 | matching_workflow 解释失败→规则化说明 | `talent_agent` 接真 LLM | `training_agent`（课程/计划推荐） | 匹配 100 对批量跑通，失败率 < 5% |
| D8 | 人工介入节点（`interrupt`）设计评审 | analysis_workflow：人才研判 / 团队结构 / 决策建议三节点 | `resume_agent` 边界用例（扫描件、空简历） | `assistant_agent` 接 1 组 RAG 真链路 | 问答带 citations |
| D9 | LangGraph 子图（Agent 即节点）封装 | analysis_workflow 与 `analytics_service` 对接 | Agent 注册到 `agent_registry` 全量 | Agent 注册补齐 + 单测（mock LLM 固定 JSON） | `registry.keys()` 6 个 agent |
| D10 | 与架构组联调 `ai/agent.py dispatch` | resume/matching 工作流串联（简历→人才→匹配） | `dispatch("resume")` 端到端 | `dispatch("assistant")` 端到端 | `POST /agents/tasks` 可用 |
| D11 | `dispatch_stream`（SSE，逐 token） | analysis_workflow 输出报告结构（JSON → 前端卡片） | `resume_agent` 性能优化（长简历分段） | `matching_agent` + `matching_skill` 复用 | SSE 首字节 < 2s |
| D12 | 集成报告 #1：工作流成功率 | 集成报告 #1 | 集成报告 #1 | 集成报告 #1 | 报告合并 |
| D13 | 会话记忆接入工作流 | 工作流幂等（同一请求重复执行不重复落库） | Agent 失败兜底（返回 5002 + 可重试标识） | Agent 权限校验（谁能触发哪个 Agent） | 幂等用例 + 越权用例 |
| D14 | 多智能体协作（Supervisor 路由：assistant→talent→matching） | 协作流程节点 | 参与协作节点 | 参与协作节点 | 一句话触发跨 Agent 任务 |
| D15 | 性能：图执行耗时埋点 | 批量匹配并发（限流 5） | token 成本统计接入 | token 成本统计接入 | 单次简历解析成本记录 |
| D16 | 降级演练（LLM 超时 / 子 Agent 挂） | 降级演练 | 降级演练 | 降级演练 | 无 500，全为业务错误码 |
| D17 | 文档：工作流如何新增节点 | 文档：三条工作流说明 | Agent 新增指南 | Agent 新增指南 | 文档评审 |
| D18 | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | 覆盖率 ≥ 70% | `pytest tests/test_workflows.py` |
| D19 | 演示：简历→画像→匹配一键跑 | 演示：分析报告 | 演示：Agent 对话 | 演示：测评 | 演示脚本 |
| D20 | 终验 | 终验 | 终验 | 终验 | 总计划 §九 验收清单 |

## 三、本组要写的代码（文件级，已剔除架构组已交付底座与业务组专属文件）

| 文件 | 归属状态 | 关键实现与边界约束 |
| --- | --- | --- |
| `app/ai/workflows/graph_builder.py` | 🔧 本组实现（P0） | `WorkflowState`、`build_graph(nodes, edges, *, entry_point, conditional_edges)`、`run_workflow(graph, state, *, thread_id)`（内部自动调 `[架构组] state_manager.save` 持久化状态，未装 `langgraph` 时提供兼容图执行器降级） |
| `app/ai/workflows/resume_workflow.py` | 🔧 本组实现（P0） | 节点：`extract(调 [RAG组] document_loader.load) → clean → structure(调 ResumeAgent) → validate(失败重试≤2次 + 叠加 [架构组] extract_skills) → persist(调 [业务组] ResumeService.save_parse_result)` |
| `app/ai/workflows/matching_workflow.py` | 🔧 本组实现（P1） | 串联 `[业务组] MatchingService`（底层复用 `[架构组] MatchStrategyFactory`）+ Top 候选人限流调用 `MatchingAgent` 生成解释，失败自动退化规则化说明 |
| `app/ai/workflows/analysis_workflow.py` | 🔧 本组实现（P2） | 人才数据决策工作流：节点 `collect_metrics`（调 `sql_query` 技能与 `[Vanna组] AnalyticsService`，禁写 SQL）`→ talent_review → team_structure → recommendation` |
| `app/ai/memory/{state_manager,conversation_memory}.py` | ✅ 架构组已实现 | **只读调用，严禁重写！** |
| `app/ai/agents/base_agent.py`、`app/ai/agent.py` | ✅ 架构组已实现 | **只读继承/调用，严禁重写！** 子类统一用 `@register_agent("xxx")` 注册 |
| `app/ai/agents/{resume,talent,matching,assessment,training,assistant}_agent.py` | 🔧 本组实现（P0–P2） | 各自继承 `BaseAgent` 实现 `build_prompt / parse_output / persist`；数据读写一律调用 `[业务组]` Service 或 8 大 Skills |
| `app/repositories/conversation_repository.py` | 🔧 本组实现（P1） | 继承 `BaseRepository[Conversation]`，实现 `create_conversation / list_by_user / append_message / get_recent_messages / paginate_messages`（精确对齐 `ConversationMemory` 回调签名） |
| `app/services/agent_service.py` | 🔧 本组实现（P0，含 `[Dify组]` 分支） | `chat / chat_stream / list_conversations / list_messages / submit_task / get_task_status`（入口注入 `AIContext` + 检查 `TokenManager` 配额；预留 `DIFY_APP_ID_CHAT` 委托 `DifyAdapter` 的可选分支） |
| `app/routers/agent_router.py` + 新增 `app/schemas/agent.py` | 🔧 本组实现（P1） | 6 个接口：`POST /agents/chat`、`POST /agents/chat/stream`(SSE)、`GET /agents/conversations`、`GET /agents/conversations/{id}/messages`、`POST /agents/tasks`、`GET /agents/tasks/{task_id}` |
| `tests/test_workflows.py`、`tests/test_agents.py` | 🔧 本组实现 | mock LLM；记忆/状态用内存兜底（**平台不使用 SQLite，测试也不引入**；新增测试文件必须加 `标签：[Agent组] [P1]`） |

---

## 四、本组依赖开关与第一个 PR

```bash
LLM_PROVIDER=qwen
LLM_API_KEY=sk-xxx
AGENT_STATE_BACKEND=memory   # 本地先用 memory，集成环境切 redis
REDIS_ENABLED=false
CONVERSATION_WINDOW=10
AGENT_MAX_STEPS=8
```

**第一个 PR（D2–D3 即可开）**：`feat(agent): 工作流构建器骨架`
内容：`ai/workflows/graph_builder.py`、`ai/memory/state_manager.py`（memory 后端）+ 单测，预计 300 行。

**最容易被卡的两处**：
1. `BaseAgent` / `dispatch` 与架构组共管 → D6 前必须完成接口对齐，否则 5 个业务 Agent 无法开工；
2. `assistant_agent` 依赖 1 组 RAG → D2 起用 fake retriever 并行开发，D8 再切真链路。
