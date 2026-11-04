# AI 能力层与业务层分组开发总计划（6 组协同 + 跨组契约）

> 版本：**v2.0.0** ｜ 制定依据：本仓库当前真实基线（公共层、25+6 表 ORM、架构组 AI 底座与 8 大 Skills 均已落地 ✅）+ 六大研发组严格边界划分
> 适用范围：`[架构组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]`、`[业务组]`
> **必读配套**：[六大研发组职责边界与防重复开发总规范](group_boundaries_and_tasks.md) ｜ [需求规格说明书](requirements_specification.md) ｜ [系统总体与详细设计文档](system_design_document.md) ｜ [分组代码实现规范](team_code_spec.md)
> 本文是**跨组总索引与契约约定**；每组的详细计划（人员 / D1–D20 每日任务 / 逐文件开发清单 / 严禁重复红线）已拆分到 `docs/plan/`。

---

## 目录

1. [组织与既有资产](#sec-org)
2. [时间安排总表](#sec-time)
3. [各组计划（已按组拆分为独立文档）](#sec-teams)
4. [跨组契约](#sec-contract)
5. [统一验收清单](#sec-accept)
6. [风险与预案](#sec-risk)
7. [每日站会与产出节奏](#sec-standup)

---

<a id="sec-org"></a>

## 一、组织与既有资产

### 1.1 六大研发组划分

| 组别 | 标签 | 方向 | 人力 | 当前状态与边界定位 |
| --- | --- | --- | --- | --- |
| 架构组 | `[架构组]` | 公共层 / LLM 底座 / 8 大 Skills / 集成仲裁 | 2 人 + 各组长 | **✅ 100 个文件已全部实现冻结**；后续负责契约守护、`router.py`/`settings.py` 合入与防重复 Review |
| 1 组 | `[RAG组]` | RAG 检索增强 + Milvus 四集合适配 + 文档加载切分 | 3 人 | 🔧 独占向量库适配、Embedding 缓存、多格式文档加载与 `/api/v1/knowledge/*` 全链路 |
| 2 组 | `[Agent组]` | LangGraph 工作流 + 6 个业务 Agent + 会话中心 | 4 人 | 🔧 独占 `graph_builder`、3 条工作流、6 个 `BaseAgent` 子类、`ConversationRepository` 与 `/api/v1/agents/*` |
| 3 组 | `[Vanna组]` | Vanna.ai NL2SQL + SQL 安全校验 + 宽表 ETL | 4 人 | 🔧 独占 `sql_validator`、`vanna_adapter`、MySQL→PgSQL 同步脚本与 `/api/v1/analytics/*` |
| 4 组 | `[Dify组]` | Dify AI 应用中台适配与 SSE 转换 | 2 人 | 🔧 独占 `adapters/dify/*`、同步脚本与 `AgentService` 可选双模接入分支 |
| 5 组 | `[业务组]` | 核心业务 CRUD 与领域状态机编排 | 4 人 | ✅ 认证/用户/组织 + 25 表 ORM 已落地；🔧 独占岗位/人才/简历/匹配/测评/培训/文件/审计 8 模块实现 |

### 1.2 已就绪冻结资产（**严禁任何组重写，一律直接 `import` 调用！**）

| 能力 | 位置 | 组外调用须知 |
| --- | --- | --- |
| 统一响应 / 异常 / 分页 / 权限 | `app/core/{response,exceptions,pagination,dependencies,permissions}.py` | Router 必挂 `require_permission`，返回 `success()`，抛 `BusinessException` 子类 |
| MySQL / PgSQL / Redis / 健康诊断 | `app/infrastructure/{database,cache}/` | `get_db` / `session_scope` / `pg_session` / `get_pg_session_factory` / `get_json` / `set_json` |
| 全部 ORM 模型（MySQL 25 表 + PgSQL 6 表） | `app/models/*.py` | **已通过 SQL 约束验收，严禁新建重复 Model 或破坏字段** |
| 通用 CRUD 基类 `BaseRepository` | `app/repositories/base.py` | 自带 17 个异步方法，子仓库只写业务特有查询 |
| 注册表与匹配三策略 | `app/patterns/{registry,strategy}.py` | `agent_registry / skill_registry / llm_registry`；人岗匹配必须复用 `MatchStrategyFactory` |
| AI 安全上下文 `AIContext` | `app/ai/context.py` | `set_context / get_context / context_scope`，全平台唯一 AI 身份权限来源 |
| 大模型门面与配额 | `app/ai/{core,utils}/*` + `app/adapters/llm/*` | 统一调 `llm_manager.chat / stream / embed`；输出解析用 `parse_json`；重试用 `ai_retry` |
| 8 大标准 Skills 组件 | `app/ai/skills/*` | 含离线可用的 `compute_match_score` 与 `extract_skills`，依赖业务/RAG/Vanna 实现后自动接通 |
| Agent 基类、调度器、会话记忆与图状态 | `app/ai/{agent.py,agents/base_agent.py,memory/*}` | **Agent 组直接继承 `BaseAgent`、复用 `ConversationMemory` 与 `state_manager`，严禁重写！** |
| 物理文件存储与异步事件总线 | `app/adapters/storage/*` + `app/infrastructure/messaging/event_bus.py` | 文件落盘统一调 `get_storage()`，异步审计统一走 `event_bus` |

### 1.3 本计划的三条硬约束

1. **AI 层不写 SQL、不碰 ORM 明细**：Agent / Skill / RAG / NL2SQL 一律通过 `app/services/*` 或 Repository 公开方法取数；
2. **权限来自上下文，不来自提示词**：所有过滤条件从 `app/ai/context.py` 的 `AIContext` 读取，`skill` 忽略 LLM 传入的 user/role 参数；
3. **外部依赖可在缺失时降级**：Milvus / Redis / Dify / Vanna 未部署时，服务必须能启动、接口返回明确错误码，不能 500 崩溃（与现有 `/health/ready` 的 `degraded` 语义一致）。

### 1.4 依赖开关（`.env`，各组本地必须能跑通无外部依赖的用例）

```bash
LLM_PROVIDER=qwen
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-xxx
MILVUS_ENABLED=true            # RAG 组
MILVUS_URI=http://127.0.0.1:19530
REDIS_ENABLED=true             # 2 组状态持久化 / 4 组 SSE 会话
REDIS_URL=redis://127.0.0.1:6379/0
DIFY_BASE_URL=http://127.0.0.1/v1   # 4 组
DIFY_API_KEY=app-xxx
PGSQL_ENABLED=true             # 3 组 NL2SQL 只读分析库
```

---

---

<a id="sec-time"></a>

## 二、时间安排总表（4 周 / 20 个工作日）

| 阶段 | 日期块 | 里程碑 | 验收标准 |
| --- | --- | --- | --- |
| S0 契约冻结 | D1–D2 | 5 组接口与数据结构对齐 | `app/adapters/llm/base_llm.py`、`app/ai/context.py`、`app/patterns/registry.py` 三类契约定稿，各组可并行 |
| S1 通道打通 | D3–D5 | LLM 能调通、Milvus 能连通、Dify 能调通 | 各组「hello world」集成用例绿 |
| S2 单模块闭环 | D6–D12 | RAG 问答可用 / 简历解析可用 / NL2SQL 可用 | 每个方向至少 1 条端到端接口返回真实结果 |
| S3 编排与联动 | D13–D17 | LangGraph 工作流 + Skills 被 Agent 调用 | Agent 能调用 Skill、能走多节点工作流 |
| S4 加固交付 | D18–D20 | 压测、降级、文档、演示 | 见本文第五节验收清单 |

> 依赖关系（谁等谁）：
> `架构组 S0 的 BaseLLM / AIContext / Registry` → 全部组；
> `1 组 retriever + embedding` → `Skills 组 knowledge_skill` → `2 组 assistant_agent`；
> `2 组 graph_builder` → `matching / resume / analysis workflow`；
> `3 组 sql_validator + vanna_adapter` → `Skills 组 sql_skill` → `2 组 analytics 相关 Agent`；
> `4 组 dify_adapter` 独立，但 **SSE 输出格式**要与 `2 组 dispatch_stream` 对齐。

---

---

<a id="sec-teams"></a>

## 三、各组计划（已按组拆分为独立文档）

每个组一份独立文件，可直接发给对应组；组内的人、天、代码清单都在自己那份里。

| 组别 | 独立文档 | 人力 | 负责方向 | 关键交付 |
| --- | --- | --- | --- | --- |
| 架构组 | [plan/00-架构组.md](plan/00-架构组.md) | 2 人 + 组长 | ✅ 已全部交付冻结；负责契约仲裁与集成守门 | `AIContext`、`LLMManager`、8 大 `Skills`、`BaseAgent`、`dispatch`、`MatchStrategyFactory` |
| 1 组 | [plan/01-RAG组.md](plan/01-RAG组.md) | 3 人 | RAG 检索增强 + Milvus 四集合网关 + 文档解析 | `document_loader/splitter`、`MilvusAdapter`、`EmbeddingService`、`Retriever`、`/knowledge/*` |
| 2 组 | [plan/02-Agent组.md](plan/02-Agent组.md) | 4 人 | AI Agent 多智能体（LangGraph） | `graph_builder`、3 条工作流、6 个 `BaseAgent` 子类、`ConversationRepository`、`/agents/*` |
| 3 组 | [plan/03-Vanna组.md](plan/03-Vanna组.md) | 4 人 | Vanna.ai NL2SQL + 宽表同步 ETL | `sql_validator`、`vanna_adapter`、`sync_analytics.py`、图表+解读、`/analytics/*` |
| 4 组 | [plan/04-Dify组.md](plan/04-Dify组.md) | 2 人 | Dify AI 应用中台适配 | `dify_adapter`、`sse.py`、`health.py`、`sync_dify_workflows.py` |
| 5 组 | [plan/06-业务组.md](plan/06-业务组.md) | 4 人 | 8 大核心业务模块 CRUD 与领域编排 | 岗位 / 人才 / 简历 / 匹配 / 测评 / 培训 / 文件 / 审计 全链路 `Repo → Schema → Service → Router` |

> 拆分原则：**组内可执行的内容放各组文件**（人员、D1–D20 每日任务、要写的代码）；
> **跨组内容留在本文**（契约、里程碑、依赖顺序、统一验收、风险、站会节奏），避免同一份内容两处维护。

---

---

<a id="sec-contract"></a>

## 四、跨组契约（S0 冻结，改动需架构组签字）

```python
# 1) LLM 契约 —— app/adapters/llm/base_llm.py
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

class LLMResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int
    raw: dict | None

class BaseLLM(ABC):
    async def chat(self, messages: list[ChatMessage], *, temperature: float = 0.3,
                   max_tokens: int | None = None) -> LLMResult: ...
    async def generate(self, prompt: str, **kw) -> LLMResult: ...
    async def stream(self, messages: list[ChatMessage], **kw) -> AsyncIterator[str]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

# 2) 上下文契约 —— app/ai/context.py
@dataclass
class AIContext:
    user_id: int | None
    roles: list[str]
    organization_id: int | None
    trace_id: str
    conversation_id: str | None
    extra: dict

def set_context(user, **extra) -> AIContext: ...
def get_context() -> AIContext: ...          # 未设置返回匿名，不抛异常
def clear_context() -> None: ...

# 3) Skill 契约 —— app/ai/skills/base.py
class BaseSkill(ABC):
    name: str
    description: str
    parameters: dict                         # JSON Schema
    async def execute(self, **kwargs) -> Any: ...
    def to_openai_tool(self) -> dict: ...    # {"type": "function", "function": {...}}

# 4) Agent 契约 —— app/ai/agents/base_agent.py
class BaseAgent(ABC):
    name: str
    system_prompt: str
    def __init__(self, db: AsyncSession, context: AIContext | None = None): ...
    async def build_prompt(self, payload: dict) -> list[ChatMessage]: ...
    async def parse_output(self, raw: str) -> dict: ...
    async def persist(self, result: dict, payload: dict) -> Any: ...
    async def run(self, payload: dict) -> dict: ...

# 5) 向量库契约 —— app/adapters/vector_store/milvus_adapter.py
class BaseVectorStore(ABC):
    async def create_collection(self, name: str, dim: int) -> None: ...
    async def upsert(self, collection: str, ids: list[str],
                     vectors: list[list[float]], metadata: list[dict]) -> None: ...
    async def search(self, collection: str, vector: list[float],
                     top_k: int = 10, expr: str | None = None) -> list[dict]: ...
    async def delete(self, collection: str, ids: list[str] | None = None,
                     expr: str | None = None) -> None: ...
    async def drop(self, collection: str) -> None: ...

# 6) NL2SQL 契约 —— app/adapters/nl2sql/vanna_adapter.py
class NL2SQLResponse:
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    chart: dict | None                       # {type, x_field, y_field, title, series}
    insight: str | None
    elapsed_ms: int
    cached: bool
```

**SSE 统一格式**（2 组 / 4 组 / 1 组流式问答共用）：

```
event: message
data: {"delta": "根据《职级晋升管理办法》"}

event: citation
data: {"doc_id": 3, "doc_title": "职级晋升管理办法", "page": 2}

event: done
data: {"conversation_id": "c-20261103-0001", "elapsed_ms": 1830}

event: error
data: {"code": 5002, "message": "模型调用超时，请重试"}
```

---

---

<a id="sec-accept"></a>

## 五、统一验收清单（D20 每组逐项打勾）

- [ ] 新增/修改文件都在自己组的分工表内，跨组文件已由对应组 review；
- [ ] 每个文件开头有中文模块注释（职责 / 思路 / 对外方法 / 待办）；
- [ ] 无裸 SQL、无硬编码密钥与地址、无 `print`、无 `raise Exception`；
- [ ] 外部依赖（LLM / Milvus / Redis / Dify / Vanna）关闭时服务可启动，接口返回 5001/5002 而非 500；
- [ ] 接口已挂 `require_permission`，越权返回 403/2004；
- [ ] 所有列表接口统一分页，`page_size` 上限 200；
- [ ] `pytest -q` 全绿（无 MySQL/Milvus 环境应 skip 而非 fail）；
- [ ] `/docs` 中接口有中文 summary、示例、错误说明；
- [ ] 关键路径有 trace_id 与耗时日志，可定位到具体组；
- [ ] 演示脚本 `scripts/demo_ai.py` 中本组链路可一键跑通；
- [ ] 文档已更新（本计划 + `team_code_spec.md` + `公共层使用手册` AI 章节）。

---

---

<a id="sec-risk"></a>

## 六、风险与预案

| 风险 | 影响 | 预案 |
| --- | --- | --- |
| 大模型额度/限流 | 各组联调阻塞 | 架构组 D3 提供 `ollama_adapter` 本地兜底 + mock LLM 测试夹具 |
| Milvus 资源不足 | RAG 无法演示 | 降级为内存向量检索（`MILVUS_ENABLED=false` 时走 numpy 余弦） |
| Vanna 准确率不达标 | NL2SQL 体验差 | 保留「LLM 直出 + 校验器」备用通路；扩大 few-shot |
| LangGraph 版本 API 变动 | 工作流重写 | `graph_builder` 做薄封装，业务工作流不直接 import langgraph 内部 API |
| Dify 与自建能力重叠 | 重复建设 | D1–D2 明确边界并签字（Dify 管 Prompt/低代码发布，自建管业务 Agent 与 RAG 精度） |
| 5 组并行改同一文件 | 冲突频繁 | 文件级分工表 + 每日站会 + 跨组文件 PR 强制 review |

---

---

<a id="sec-standup"></a>

## 七、每日站会与产出节奏

| 时间 | 内容 | 产出 |
| --- | --- | --- |
| 每日 09:30（15 min） | 昨天完成 / 今天计划 / 阻塞项 | 阻塞项当天指派责任人 |
| 每周五 16:00 | 集成日：合并、跑全量测试、更新 `docs/team_code_spec.md` 状态列 | 集成报告 |
| D12 / D20 | 大节点验收 | 报告 + 演示 |

**Git 约定**（沿用 `docs/development.md`）：分支 `feature/<组>-<模块>`；提交 `feat(rag): 新增混合检索`；PR 必须写「改了哪些文件 / 动了哪些表 / 是否需要其他人同步改 Schema」。
