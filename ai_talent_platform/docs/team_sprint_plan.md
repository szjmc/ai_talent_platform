# AI 能力层分组开发计划（按天任务 + 每组代码清单）

> 版本：**v1.0.0** ｜ 制定依据：本仓库现有骨架（`app/ai/**`、`app/adapters/**` 全部为 TODO 桩）+ 架构组 5 个方向的分工
> 适用范围：RAG 组、Agent 组、Skills 组（架构组）、Vanna 组、Dify 组
> 配套文档：[分组代码实现规范](team_code_spec.md)（每组每个文件的方法签名、返回结构、验收命令）
> 本文是**索引 + 跨组约定**；每组的详细计划（人员 / D1–D20 每日任务 / 代码清单）已拆分到 `docs/plan/`。

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

### 1.1 分组

| 组别 | 方向 | 人力 | 说明 |
| --- | --- | --- | --- |
| 架构组 | 公共层 / 契约 / Skills 组件库 | 2 人 + 4 组长 | 已交付公共层（认证/用户/组织/异常/响应/Repository/健康检查）；后续负责 Skills 体系与跨组契约仲裁 |
| 1 组 | RAG 检索增强生成（Milvus + LangChain） | 3 人 | 知识库索引、混合检索、引用式问答 |
| 2 组 | AI Agent 多智能体（LangGraph） | 4 人 | 工作流引擎、6 个业务 Agent、会话与状态持久化 |
| 3 组 | Vanna.ai NL2SQL 数据分析 | 4 人 | 训练数据、SQL 安全、图表推断、数据权限 |
| 4 组 | Dify AI 应用中台 | 2 人 | 模型/Prompt/知识库运维统一封装、SSE 转发 |

### 1.2 已就绪（**不要重写，直接调用**）

| 能力 | 位置 | 组外需知 |
| --- | --- | --- |
| 统一响应 `success()/api_response()` | `app/core/response.py` | 所有 Router 返回值 |
| 业务异常体系（code 1001–5003） | `app/core/exceptions.py` | AI 组用 `AIError`(5002) / `ExternalServiceError`(5001) |
| 权限依赖 `require_permission` | `app/core/dependencies.py` | Router 必须挂 |
| 分页 `PageParams / build_page_result` | `app/core/pagination.py` | 列表接口 |
| 注册表 `Registry / agent_registry / skill_registry / llm_registry` | `app/patterns/registry.py` | **三组共用，禁止另起注册表** |
| 配置单例 `settings` | `app/config/settings.py` | 已有 `LLM_* / DIFY_* / MILVUS_* / REDIS_* / PGSQL_*`，新增配置只能加在这里 |
| 提示词仓库 `PROMPT_VERSION` + 模板 | `app/config/prompt_config.py` | 提示词禁止写死在业务代码 |
| 日志 `get_logger` + trace_id | `app/config/logging_config.py` | 禁止 `print` |
| MySQL 会话 / PgSQL 会话 / 事务 | `app/infrastructure/database/` | `get_db` / `pg_session` / `transaction` |
| 健康检查 `check_databases()` | `app/infrastructure/database/health.py` | AI 组启动自检 LLM/Milvus/Dify 时参考其返回结构 |
| Repository 基类 + 13 个业务仓库 | `app/repositories/` | AI 层**禁止直接写 SQL**，只调 Service |

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
| 架构组 | [plan/00-架构组.md](plan/00-架构组.md) | 2 人 + 4 组长 | 契约 / LLM 适配 / Skills / 集成验收 | `AIContext`、`BaseLLM`、`BaseSkill`、`BaseAgent`、`dispatch` |
| 1 组 | [plan/01-RAG组.md](plan/01-RAG组.md) | 3 人 | RAG 检索增强（Milvus + LangChain） | 文档加载/切片、混合检索、引用式问答 |
| 2 组 | [plan/02-Agent组.md](plan/02-Agent组.md) | 4 人 | AI Agent 多智能体（LangGraph） | `graph_builder`、3 条工作流、6 个 Agent |
| 3 组 | [plan/03-Vanna组.md](plan/03-Vanna组.md) | 4 人 | Vanna.ai NL2SQL | `sql_validator`、`vanna_adapter`、图表+解读 |
| 4 组 | [plan/04-Dify组.md](plan/04-Dify组.md) | 2 人 | Dify AI 应用中台 | `dify_adapter`、SSE 解析、知识库同步 |

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
