# 02. 2 组（AI Agent 多智能体，4 人）— 20 个工作日计划

> 隶属：[AI 能力层分组开发计划](../team_sprint_plan.md) ｜ 配套：[分组代码实现规范](../team_code_spec.md)
> 人力：4 人（AG-A 引擎 / AG-B 工作流 / AG-C Agent 1 / AG-D Agent 2）

---

## 本组速览

- **组别**：2 组
- **周期**：D1–D20（4 周），阶段划分见 [总计划 §二](../team_sprint_plan.md#sec-time)
- **每日 15 分钟站会**，每周五集成日；D12 / D20 为大节点验收
- **跨组契约不可私改**：LLM / Skill / Agent / 向量库 / NL2SQL / SSE 见 [总计划 §八](../team_sprint_plan.md)
- **代码骨架与验收命令**：[分组代码实现规范](../team_code_spec.md)
- **组内约定**：每个 PR 描述必须包含「改了什么 / 表结构有无变更 / 需要谁 review / 如何验证」

---

## 一、人员分工

| 角色 | 负责 |
| --- | --- |
| AG-A（引擎） | `workflows/graph_builder.py`、`memory/state_manager.py`、checkpointer |
| AG-B（工作流） | `resume_workflow`、`matching_workflow`、`analysis_workflow` |
| AG-C（Agent 1） | `base_agent` 落地 + `resume_agent`、`talent_agent` |
| AG-D（Agent 2） | `matching_agent`、`assessment_agent`、`training_agent`、`assistant_agent` |

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

## 三、本组要写的代码（文件级）

| 文件 | 依赖包 | 关键实现 |
| --- | --- | --- |
| `app/ai/workflows/graph_builder.py` | `langgraph` | `WorkflowState`、`build_graph(nodes, edges, entry_point, conditional_edges)`、`run_workflow(graph, state, thread_id)`、条件边/循环/重试工具 |
| `app/ai/workflows/resume_workflow.py` | `langgraph` | 节点：`extract_text → clean → llm_structure → validate → persist`；条件边 `validate` 失败→`llm_structure`（≤2 次）→`fail` |
| `app/ai/workflows/matching_workflow.py` | `langgraph` | 节点：`parse_position → hard_filter → vector_recall → score → explain → persist`；解释失败走规则化说明 |
| `app/ai/workflows/analysis_workflow.py` | `langgraph` | 节点：`collect_metrics → talent_review → team_structure → recommendation`；调 `analytics_service` |
| `app/ai/memory/state_manager.py` | `redis`（可选） | `save/load/delete`，Redis 不可用降级 MySQL JSON 列/进程内 dict |
| `app/ai/memory/conversation_memory.py` | `redis` | `append/get_recent(limit=10)/clear/summarize_if_needed` |
| `app/ai/agents/base_agent.py` | — | （与架构组共同）`run()` 骨架 |
| `app/ai/agents/{resume,talent,matching,assessment,training,assistant}_agent.py` | — | 各自 `build_prompt/parse_output/persist` + `@agent_registry.register("xxx")` |
| `app/ai/agent.py` | — | （与架构组共同）`dispatch/dispatch_stream` |
| `app/services/{resume,talent,matching,assessment,training,agent}_service.py` | — | 补全 Service 方法（Agent 只调这些） |
| `app/routers/agent_router.py` | — | 6 个接口：`POST /agents/chat`、`POST /agents/chat/stream`(SSE)、`GET /agents/conversations`、`GET /agents/conversations/{id}/messages`、`POST /agents/tasks`、`GET /agents/tasks/{task_id}` |
| `tests/test_workflows.py`、`test_agents.py` | — | mock LLM；记忆/状态用内存 fake 或 Redis（**平台不使用 SQLite，测试也不引入**） |

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
