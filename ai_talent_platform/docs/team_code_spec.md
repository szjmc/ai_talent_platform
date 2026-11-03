# 分组代码实现规范（每组每个文件写什么、怎么验）

> 配套：[分组开发计划索引](team_sprint_plan.md) ｜ 各组独立计划：[架构组](plan/00-架构组.md)、[RAG](plan/01-RAG组.md)、[Agent](plan/02-Agent组.md)、[Vanna](plan/03-Vanna组.md)、[Dify](plan/04-Dify组.md) ｜ 版本 v1.0.0
> 原则：**本文给出每个文件的方法签名、返回结构、关键代码骨架与验收命令**。复制骨架即可开工，禁止另起命名/结构。
> 与现有代码冲突时：以 `app/core/*`、`app/config/*`、`app/patterns/registry.py` 为准。

---

## 零、开工前置（5 组共用，架构组 D1 落地）

### 0.1 依赖（按组启用，不用的保持注释）

```txt
# requirements.txt —— P2 AI 能力层
httpx>=0.27                  # 全组：LLM / Dify / Vanna HTTP 调用
langchain-core>=0.3          # 1 组：Prompt / Runnable 组合
langchain-text-splitters>=0.3
langchain-community>=0.3     # 1 组：文档加载（可选，也可自研）
pymilvus>=2.4                # 1 组
langgraph>=0.2               # 2 组
redis>=5.0                   # 2 组 / 4 组
vanna>=0.7                   # 3 组
sqlglot>=25.0                # 3 组
pdfplumber>=0.11             # 1 组
python-docx>=1.1             # 1 组
openpyxl>=3.1                # 1 组
```

### 0.2 配置补充（`app/config/settings.py`，架构组统一提交，一次加完避免 5 人打架）

```python
    # ------------------------------------------------------------------ AI：检索
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 64
    RAG_TOP_K: int = 10
    RAG_SCORE_THRESHOLD: float = 0.35
    RAG_USE_RERANK: bool = False
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024

    # ------------------------------------------------------------------ AI：Agent
    AGENT_MAX_STEPS: int = 8
    AGENT_STATE_BACKEND: Literal["memory", "redis", "mysql"] = "memory"
    CONVERSATION_WINDOW: int = 10
    TOKEN_DAILY_QUOTA: int = 200_000

    # ------------------------------------------------------------------ AI：NL2SQL
    NL2SQL_MAX_ROWS: int = 1000
    NL2SQL_TIMEOUT_SECONDS: int = 30
    NL2SQL_ALLOWED_SCHEMA: str = "public"

    # ------------------------------------------------------------------ AI：Dify
    DIFY_TIMEOUT: int = 60
    DIFY_MAX_RETRIES: int = 2
    DIFY_APP_ID_CHAT: str = ""
    DIFY_APP_ID_RESUME: str = ""
    DIFY_DATASET_ID: str = ""
```

### 0.3 全组共用初始化（架构组 `app/ai/bootstrap.py`，**新增**）

```python
"""模块：app/ai/bootstrap.py
职责：AI 能力层装配入口（幂等）。

实现思路：
1. 导入 llm 适配器 / skills / agents 模块，触发 @registry.register 装饰器注册；
2. 打印可用清单，便于 /system/info 与排障；
3. FastAPI lifespan 中调用一次：ai_bootstrap()。
"""

def ai_bootstrap() -> dict[str, list[str]]:
    import app.adapters.llm.openai_adapter   # noqa: F401  触发注册
    import app.adapters.llm.qwen_adapter     # noqa: F401
    import app.adapters.llm.ollama_adapter   # noqa: F401
    import app.ai.skills.registry_bootstrap  # noqa: F401
    import app.ai.agents                     # noqa: F401  6 个 Agent
    from app.patterns.registry import agent_registry, llm_registry, skill_registry
    return {"llm": llm_registry.keys(), "skills": skill_registry.keys(), "agents": agent_registry.keys()}
```

> **各组都必须遵守**：模块被 import 即完成注册，禁止在 Router 里手动 `register()`。

---

## 一、架构组代码规范

### 1.1 `app/ai/context.py`

```python
from contextvars import ContextVar
from dataclasses import dataclass, field

@dataclass(slots=True)
class AIContext:
    user_id: int | None = None
    roles: list[str] = field(default_factory=list)
    organization_id: int | None = None
    trace_id: str = ""
    conversation_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_superuser(self) -> bool:
        return "super_admin" in self.roles

    def data_scope(self) -> str:      # all / dept_sub / dept / self
        ...

_current: ContextVar[AIContext | None] = ContextVar("ai_context", default=None)

def set_context(user=None, *, roles=None, organization_id=None, **extra) -> AIContext:
    """从 User ORM 对象（或显式传参）构造上下文；roles 为空时按 user.roles 取 code。"""

def get_context() -> AIContext:
    """未设置时返回 AIContext()（匿名），**不抛异常**，避免单测与后台任务崩。"""

def clear_context() -> None:
    _current.set(None)
```

验收：`pytest tests/test_ai_context.py -q`（3 例：默认匿名 / 有用户 / 角色推导数据范围）

### 1.2 `app/adapters/llm/base_llm.py`

```python
@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

@dataclass(slots=True)
class LLMResult:
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: int = 0
    raw: dict | None = None

class BaseLLM(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(self, messages: list[ChatMessage], *, temperature: float = 0.3,
                   max_tokens: int | None = None) -> LLMResult: ...

    async def generate(self, prompt: str, **kw) -> LLMResult:
        return await self.chat([ChatMessage("user", prompt)], **kw)

    @abstractmethod
    def stream(self, messages: list[ChatMessage], **kw) -> AsyncIterator[str]: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

### 1.3 `app/adapters/llm/openai_adapter.py`（qwen/ollama 同构，只改 base_url 与鉴权）

```python
import httpx
from app.adapters.llm.base_llm import BaseLLM, ChatMessage, LLMResult
from app.ai.utils.retry_handler import ai_retry
from app.config.settings import settings
from app.core.exceptions import AIError
from app.patterns.registry import llm_registry

@llm_registry.register("openai")
class OpenAIAdapter(BaseLLM):
    name = "openai"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.LLM_BASE_URL.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=settings.LLM_TIMEOUT,
        )

    @ai_retry(max_attempts=3)
    async def chat(self, messages, *, temperature=0.3, max_tokens=None) -> LLMResult:
        payload = {"model": settings.LLM_MODEL, "temperature": temperature,
                   "messages": [m.__dict__ for m in messages]}
        if max_tokens: payload["max_tokens"] = max_tokens
        r = await self._client.post("/chat/completions", json=payload)
        if r.status_code >= 400:
            raise AIError(f"LLM 调用失败({r.status_code})：{r.text[:200]}")
        data = r.json()
        usage = data.get("usage", {})
        return LLMResult(content=data["choices"][0]["message"]["content"],
                         model=data.get("model", settings.LLM_MODEL),
                         prompt_tokens=usage.get("prompt_tokens", 0),
                         completion_tokens=usage.get("completion_tokens", 0))

    async def stream(self, messages, **kw):     # SSE
        ...

    async def embed(self, texts):
        r = await self._client.post("/embeddings", json={"model": settings.EMBEDDING_MODEL, "input": texts})
        return [d["embedding"] for d in r.json()["data"]]
```

**注意**：`chat/stream/embed` 里禁止 `print`，统一 `get_logger(__name__)`。

### 1.4 `app/ai/utils/retry_handler.py`

```python
def ai_retry(max_attempts: int = 3, base_delay: float = 0.5,
             retry_on: tuple[type[BaseException], ...] = (httpx.TimeoutException, httpx.ConnectError)):
    """指数退避重试。只重试可恢复错误（超时、连接、429、5xx）；AIError/ParamError 不重试。"""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for i in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except retry_on as exc:
                    if i == max_attempts - 1:
                        raise
                    await asyncio.sleep(base_delay * 2 ** i)
        return wrapper
    return decorator

class SimpleCircuitBreaker:
    def __init__(self, fail_threshold: int = 5, reset_seconds: int = 30): ...
    async def call(self, fn, *args, **kwargs): ...   # 打开状态直接抛 ExternalServiceError
```

### 1.5 `app/ai/utils/output_parser.py`

```python
def parse_json(text: str) -> dict:
    """容错顺序：直接 loads → 去掉 json 代码围栏 → 截取首个 { 到末个 } → 单引号换双引号。
    全部失败抛 AIError('模型输出不是合法 JSON')。"""

def parse_model(text: str, schema: type[BaseModel]) -> BaseModel:
    """parse_json 之后做 schema.model_validate；校验失败信息写进异常 message，便于前端提示。"""
```

### 1.6 `app/ai/skills/base.py`（**新增**，Skills 体系地基）

```python
class BaseSkill(ABC):
    name: str = "base"
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    async def execute(self, **kwargs) -> Any: ...

    def to_openai_tool(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}

def get_skill(name: str) -> BaseSkill: ...
#    从 skill_registry 取实例；未注册抛 NotFoundError

def list_tools() -> list[dict]: ...
#    供 Agent function-calling 使用
```

**Skill 的硬规则**（架构组 review 重点）：

1. `execute` 里**只允许**从 `get_context()` 取 `user_id/roles/organization_id`，**不接受** LLM 传入的 `user_id/role` 参数（即使 schema 里有也忽略）；
2. 数据访问走 Service 或 Repository，禁止 SQL；
3. 返回结构必须是可 JSON 序列化的 dict/list，且带来源元信息（供引用、审计）；
4. 失败抛业务异常（`BusinessRuleError` / `NotFoundError` / `AIError`），不返回 `None` 让上层猜。

### 1.7 `app/ai/skills/knowledge_skill.py`

```python
@skill_registry.register("knowledge_search")
class KnowledgeSearchSkill(BaseSkill):
    name = "knowledge_search"
    description = "在企业知识库中检索与问题相关的制度/流程片段"
    parameters = {"type": "object",
                  "properties": {"question": {"type": "string"},
                                 "top_k": {"type": "integer", "default": 5}},
                  "required": ["question"]}

    async def execute(self, question: str, top_k: int = 5, **_) -> list[dict]:
        ctx = get_context()                     # 权限来自上下文
        from app.ai.rag.retriever import Retriever
        chunks = await Retriever().hybrid_search(question, top_k=top_k, scope=ctx)
        return [{"content": c.content, "doc_id": c.doc_id, "doc_title": c.doc_title,
                 "page": c.page, "score": c.score} for c in chunks]
```

`matching_skill` / `resume_skill` / `sql_skill` 同构，工具描述见 [架构组计划 §三](plan/00-架构组.md)。

### 1.8 `app/ai/agents/base_agent.py`

```python
class BaseAgent(ABC):
    name: str = "base"
    system_prompt: str = ""

    def __init__(self, db: AsyncSession, context: AIContext | None = None) -> None:
        self.db = db
        self.context = context or get_context()

    @abstractmethod
    async def build_prompt(self, payload: dict) -> list[ChatMessage]: ...
    @abstractmethod
    async def parse_output(self, raw: str) -> dict: ...
    @abstractmethod
    async def persist(self, result: dict, payload: dict) -> Any: ...

    async def run(self, payload: dict) -> dict:
        started = time.perf_counter()
        messages = [ChatMessage("system", self.system_prompt), *await self.build_prompt(payload)]
        llm = get_llm(self.name)
        last_exc = None
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            try:
                res = await llm.chat(messages)
                result = await self.parse_output(res.content)
                break
            except Exception as exc:            # 解析失败也重试，最多 N 次
                last_exc = exc
                log.warning("agent=%s 第 %s 次失败: %s", self.name, attempt + 1, exc)
        else:
            raise AIError(f"{self.name} 执行失败：{last_exc}")
        await TokenManager().record(self.context.user_id, res.model,
                                    res.prompt_tokens, res.completion_tokens, self.name)
        if payload.get("persist", True):
            await self.persist(result, payload)
        result["_meta"] = {"agent": self.name, "elapsed_ms": int((time.perf_counter() - started) * 1000),
                           "model": res.model, "tokens": res.prompt_tokens + res.completion_tokens}
        return result
```

### 1.9 `app/ai/agent.py`

```python
AGENT_TYPES = ("assistant", "resume", "talent", "matching", "assessment", "training")

async def dispatch(agent_type: str, payload: dict, db: AsyncSession) -> dict:
    """取类 → 实例化（注入 db + 当前 context）→ run。未注册抛 NotFoundError 并列出可用类型。"""

def dispatch_stream(agent_type: str, payload: dict) -> AsyncIterator[str]:
    """按 team_sprint_plan.md §四 的 SSE 统一格式逐条 yield（event + data 两行 + 空行），正常结束发 done，异常发 error。"""
```

---

## 二、1 组（RAG）代码规范

### 2.1 `app/ai/rag/document_loader.py`

```python
@dataclass(slots=True)
class Document:
    content: str
    metadata: dict      # {"source": 文件名, "page": 3, "type": "pdf", "chunk_index": 0}

LOADERS = {".pdf": load_pdf, ".docx": load_docx, ".doc": load_docx,
           ".xlsx": load_excel, ".xls": load_excel, ".txt": load_txt, ".md": load_txt}

def load(file_path: str) -> list[Document]:
    """按扩展名分派；不支持抛 FileError(5003)。"""

def load_pdf(path) -> list[Document]:
    """pdfplumber 逐页提取；空页跳过；页码写进 metadata['page']。"""
```

要点：编码兜底（`utf-8 → gbk`）；单文件异常不影响其它页；返回至少 1 个 Document 或抛 `FileError`。

### 2.2 `app/ai/rag/document_splitter.py`

```python
def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """优先按中文句末标点/换行切，长度超限再硬切；overlap 不跨句。"""

def split_by_headings(text: str) -> list[tuple[str, str]]:
    """返回 [(heading, body)]，用正则 ^第[一二三四五六七八九十]+[章节条]|^#{1,6} 。"""

def split_document(doc: Document) -> list[Document]:
    """为每个 chunk 补 metadata['chunk_index'] 与 'heading'。"""
```

### 2.3 `app/adapters/vector_store/milvus_adapter.py`

```python
class BaseVectorStore(ABC):
    async def create_collection(self, name: str, dim: int) -> None: ...
    async def upsert(self, collection: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict]) -> None: ...
    async def search(self, collection: str, vector: list[float], top_k: int = 10,
                     expr: str | None = None) -> list[dict]: ...
    async def delete(self, collection: str, ids=None, expr: str | None = None) -> None: ...
    async def drop(self, collection: str) -> None: ...

@vector_registry.register("milvus")
class MilvusAdapter(BaseVectorStore):
    """MILVUS_ENABLED=false 时构造即抛 ExternalServiceError('Milvus 未启用')。
    集合 schema：id(VARCHAR 主键=chunk 唯一键) / vector(FLOAT_VECTOR, dim) / metadata(JSON)。
    索引：IVF_FLAT, metric_type=COSINE, nlist=1024；search 时 nprobe=16。"""
```

集合命名（固定，不得另起）：`knowledge_vectors`、`talent_vectors`、`position_vectors`、`question_vectors`。

### 2.4 `app/ai/rag/retriever.py`

```python
@dataclass(slots=True)
class RetrievedChunk:
    content: str; doc_id: int | None; doc_title: str = ""
    page: int | None = None; score: float = 0.0; source: str = ""

class Retriever:
    def __init__(self, store=None, embedder=None, db=None): ...

    async def semantic_search(self, query, top_k=10, scope: AIContext | None = None) -> list[RetrievedChunk]:
        """embed_query → store.search(expr=权限表达式) → 组装（回查 knowledge_chunks 取 doc_title）。"""

    async def keyword_search(self, query, top_k=10, scope=None) -> list[RetrievedChunk]:
        """MySQL 侧 LIKE/全文；只对已索引文档。"""

    async def hybrid_search(self, query, top_k=10, scope=None) -> list[RetrievedChunk]:
        """语义 top_k*2 + 关键词 top_k*2 → RRF 融合：score = Σ 1/(60+rank) → 取 top_k。"""

    async def rerank(self, query, chunks, top_k) -> list[RetrievedChunk]:
        """settings.RAG_USE_RERANK=false 时原样返回（降级路径必须存在）。"""
```

权限表达式（Milvus expr）示例：`organization_id in [3] and (permission_scope == "public" or ...)`；
**关键**：`scope` 必须来自 `get_context()`，不能来自检索入参。

### 2.5 `app/ai/rag/rag_pipeline.py`

```python
class RAGPipeline:
    async def index_document(self, doc_id: int) -> dict:
        """doc→文件→load→split→embed_texts(batch=16)→store.upsert→回写
        knowledge_docs(status=indexed, chunk_count, indexed_at, embedding_model)，
        失败写 index_error 且 status=failed（不抛到 Router，返回 {status,error}）。"""

    async def ask(self, question: str, *, top_k: int = 5, use_rerank: bool = True) -> dict:
        """hybrid_search → 空则直接返回 {'answer': '知识库中未找到依据…', 'citations': []}（不调 LLM）
        → 组装 Prompt（片段编号 [1][2]，要求只依据片段作答并标注编号）→ llm.chat
        → 解析引用的编号 → 返回 {'answer', 'citations':[{doc_id,doc_title,page,chunk,score}], 'elapsed_ms'}。"""

    async def ask_stream(self, question: str, **kw) -> AsyncIterator[dict]:
        """同样流程，改用 llm.stream；先 yield citation 事件再逐 token。"""
```

### 2.6 `app/services/knowledge_service.py` + `app/routers/knowledge_router.py`

服务方法固定为：`create_text_doc`、`upload_document`、`index_document`、`retrieve`、`ask`、`ask_stream`、`page_list`、`delete_document`、`retrieve_eval`。

接口与 `docs/api_document.md` 第十五节**逐条对应**（共 7 个，权限 `knowledge:read` / `knowledge:manage`）：

| # | 方法与路径 | 权限 | Service 方法 |
| --- | --- | --- | --- |
| 1 | `POST /knowledge/docs` | `knowledge:manage` | `create_text_doc` |
| 2 | `POST /knowledge/docs/upload` | `knowledge:manage` | `upload_document`（返回后异步 `index_document`） |
| 3 | `GET /knowledge/docs` | `knowledge:read` | `page_list` |
| 4 | `DELETE /knowledge/docs/{id}` | `knowledge:manage` | `delete_document`（连带删 Milvus 向量） |
| 5 | `POST /knowledge/retrieve` | `knowledge:read` | `retrieve` |
| 6 | `POST /knowledge/ask` | `knowledge:read` | `ask` / `ask_stream` |
| 7 | `GET /knowledge/retrieve-eval` | `knowledge:manage` | `retrieve_eval` |

```python
@router.post("/knowledge/ask", summary="知识库智能问答",
             dependencies=[Depends(require_permission(PermissionEnum.KNOWLEDGE_READ))])
async def ask(body: KnowledgeAskRequest, service: KnowledgeService = Depends(get_service)):
    return success(data=await service.ask(body.question, top_k=body.top_k), message="OK")
```

### 2.7 验收命令

```bash
pytest tests/test_rag_loader.py tests/test_rag_splitter.py tests/test_retriever.py -q
python scripts/init_milvus.py --check          # 4 个集合存在
curl -X POST localhost:8000/api/v1/knowledge/ask -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"question":"测评结果有效期多久？","top_k":5}'
```

---

## 三、2 组（LangGraph Agent）代码规范

### 3.1 `app/ai/workflows/graph_builder.py`

```python
class WorkflowState(TypedDict, total=False):
    payload: dict
    data: dict             # 中间结果
    errors: list[str]
    retries: int
    status: str            # running / done / failed / need_human

def build_graph(nodes: dict[str, Callable], edges: list[tuple[str, str]],
                entry_point: str, conditional_edges: list[tuple[str, Callable, dict[str, str]]] = (),
                *, use_checkpoint: bool = False):
    """把 dict 形式的节点装配成 LangGraph StateGraph 并 compile。
    use_checkpoint=True 时挂 checkpointer（见 state_manager）。"""

async def run_workflow(graph, initial_state: dict, *, thread_id: str | None = None) -> WorkflowState:
    """ainvoke；异常写进 state['errors'] 并把 status 置 failed（不向上抛裸异常）。"""

def route_by_status(state: WorkflowState) -> str:
    return state.get("status", "running")
```

### 3.2 `app/ai/workflows/resume_workflow.py`（模板，另两条同构）

```python
async def node_extract(state):   return {"data": {**state["data"], "text": await _read_text(state["payload"])}}
async def node_clean(state):     return {"data": {**state["data"], "text": _clean(state["data"]["text"])}}
async def node_structure(state):
    prompt = RESUME_PARSE_PROMPT.format(text=state["data"]["text"][:12000])
    res = await get_llm("resume").chat([ChatMessage("user", prompt)])
    return {"data": {**state["data"], "raw": res.content},
            "_usage": {"prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens}}
async def node_validate(state):
    try:
        return {"data": {**state["data"], "parsed": parse_json(state["data"]["raw"])}, "status": "validated"}
    except AIError as exc:
        return {"errors": [*state["errors"], str(exc)], "retries": state["retries"] + 1,
                "status": "need_retry" if state["retries"] + 1 <= 2 else "failed"}
async def node_persist(state):   return {"data": {**state["data"], "resume_id": await _save(state["data"]["parsed"])}}

GRAPH = build_graph(
    nodes={"extract": node_extract, "clean": node_clean, "structure": node_structure,
           "validate": node_validate, "persist": node_persist},
    edges=[("extract", "clean"), ("clean", "structure"), ("persist", END)],
    entry_point="extract",
    conditional_edges=[("validate", route_by_status, {"validated": "persist", "need_retry": "structure", "failed": END}),
                       ("structure", lambda s: "__next__", {"__next__": "validate"})],
    use_checkpoint=True,
)
```

`matching_workflow` 节点：`parse_position → hard_filter → vector_recall → score → explain → persist`；
`analysis_workflow` 节点：`collect_metrics → talent_review → team_structure → recommendation`。

**硬要求**：节点内只用 Service / Skill / LLM；**禁止**在节点里写 SQL；每个节点必须幂等（同一 `thread_id` 重跑不重复落库，用 `matching_records` 唯一键或先查后写）。

### 3.3 `app/ai/memory/state_manager.py`

```python
class StateManager:
    def __init__(self, backend: str | None = None): ...   # 默认 settings.AGENT_STATE_BACKEND
    async def save(self, thread_id: str, state: dict) -> None: ...
    async def load(self, thread_id: str) -> dict | None: ...
    async def delete(self, thread_id: str) -> None: ...
    # 序列化：datetime 转 isoformat、Decimal 转 str、set 转 list；读取时原样返回（不做时间反解）
```

后端优先级：`redis`（REDIS_ENABLED）→ `mysql`（`conversations` JSON 列）→ `memory`（进程内 dict，只用于开发/测试）。

### 3.4 `app/ai/agents/*_agent.py`（6 个，同一模板）

```python
@agent_registry.register("matching")
class MatchingAgent(BaseAgent):
    name = "matching"
    system_prompt = MATCHING_PROMPT          # 来自 app/config/prompt_config.py

    async def build_prompt(self, payload):
        position = await PositionService(self.db).get(payload["position_id"])
        talent = await TalentService(self.db).get(payload["talent_id"])
        return [ChatMessage("user", f"【岗位要求】{position.requirements}\n【人才画像】{talent.portrait}")]

    async def parse_output(self, raw):       return MatchingResult.model_validate(parse_json(raw)).model_dump()
    async def persist(self, result, payload): await MatchingService(self.db).save_record(payload, result)
```

6 个 Agent 的 `name`：`assistant / resume / talent / matching / assessment / training`（与 `AGENT_TYPES` 一致）。

### 3.5 `app/routers/agent_router.py` 的 SSE 实现

```python
@router.post("/agents/chat/stream", summary="Agent 对话（SSE）")
async def chat_stream(body: AgentChatRequest, db: AsyncSession = Depends(get_db)):
    async def gen():
        try:
            async for event in dispatch_stream("assistant", body.model_dump(), db):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        except BusinessException as exc:
            yield f"event: error\ndata: {json.dumps({'code': exc.code, 'message': exc.message}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### 3.6 验收命令

```bash
pytest tests/test_workflows.py tests/test_agents.py -q          # 全部 mock LLM
curl -N -X POST localhost:8000/api/v1/agents/chat/stream -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"query":"帮我找 3 个后端骨干"}'
curl -X POST localhost:8000/api/v1/agents/tasks -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"agent_type":"resume","payload":{"file_id":12}}'
```

---

## 四、3 组（Vanna NL2SQL）代码规范

### 4.1 `app/adapters/nl2sql/sql_validator.py`（**先写它，安全第一**）

```python
# 白名单与分析库（docs/database_design.md 第七节）严格对应，共 6 张
ALLOWED_TABLES = {"dim_organization", "dim_talent_wide",
                  "fact_talent_snapshot", "fact_assessment_history",
                  "fact_training_track", "fact_behavior_event"}

class SQLValidationError(BusinessRuleError):
    code, status_code, message = 1002, 400, "SQL 校验未通过"

def validate_select_sql(sql: str, allowed_tables: set[str] | None = None) -> str:
    """用 sqlglot 解析（read='postgres'）：
    1) 只允许单条 SELECT/WITH，出现 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/GRANT → 拒；
    2) 表名必须在白名单（含 schema 前缀）；
    3) 禁止 information_schema / pg_catalog / system.* ；
    4) 禁止 ; 多语句、禁止 -- / /* 以外的可疑注释逃逸；
    5) 返回 re-generate 后的规范化 SQL（用解析树重新生成，天然消除注入）。"""

def inject_limit(sql: str, max_rows: int = 1000) -> str:
    """无 LIMIT 或有 LIMIT > max_rows 时包一层：SELECT * FROM (原 SQL) t LIMIT n。"""

def inject_data_scope(sql: str, scope_sql: str | None) -> str:
    """scope_sql 形如 organization_id IN (3,5) 或 1=1；
    用 sqlglot 在 WHERE 上 AND 追加，无 WHERE 则新增。"""
```

单测必须覆盖的 15 个反例：`DROP TABLE`、`; DELETE`、`UNION SELECT pg_sleep`、`information_schema`、非白名单表、子查询里非白名单表、`LIMIT 1000000`、注释截断等。

### 4.2 `app/adapters/nl2sql/vanna_adapter.py`

```python
@dataclass(slots=True)
class NL2SQLResponse:
    question: str; sql: str; columns: list[str]; rows: list[list]
    chart: dict | None = None; insight: str | None = None
    elapsed_ms: int = 0; cached: bool = False

class VannaAdapter:
    def __init__(self) -> None:
        """vanna 初始化（模型 = settings.LLM_*，向量库 = 本地 chromadb 或 PgSQL），训练数据在 scripts/train_vanna.py。"""

    async def ask(self, question: str, *, user_scope: str | None = None) -> NL2SQLResponse:
        sql = await self._generate(question)
        sql = validate_select_sql(sql, ALLOWED_TABLES)
        sql = inject_data_scope(sql, user_scope)
        sql = inject_limit(sql, settings.NL2SQL_MAX_ROWS)
        columns, rows = await self._execute_readonly(sql)      # pg_session，statement_timeout
        chart = self._infer_chart(columns, rows)
        insight = await self._summarize(question, columns, rows)
        return NL2SQLResponse(...)

    def _infer_chart(self, columns, rows) -> dict | None:
        """单行单列 → None（数字卡片）；首列为日期/年月 → line；含'占比/率'→ pie；其余 → bar。
        输出固定 {type, x_field, y_field, title, series}。"""
```

### 4.3 `scripts/train_vanna.py`

```bash
python scripts/train_vanna.py --ddl docs/sql/ddl_analytics.sql \
       --docs docs/sql/metrics.md \
       --sql docs/sql/examples.sql \
       --terms docs/sql/terms.yml \
       --reset
```

训练四类语料（缺一不可）：**DDL（分析库 6 张表）**、**指标口径文档**、**示例 SQL ≥ 40 条**、**业务术语（骨干/流失率/达标率/继任者）**。

分析库 6 张表（与分析文档一致，不得改名）：`fact_talent_snapshot`、`fact_assessment_history`、
`fact_training_track`、`fact_behavior_event`、`dim_organization`、`dim_talent_wide`。

### 4.4 `app/services/analytics_service.py`

```python
class AnalyticsService:
    async def overview(self) -> dict: ...                               # GET  /analytics/overview
    async def distribution(self, type_: str) -> dict: ...               # GET  /analytics/distribution
    async def trend(self, metric: str, granularity: str) -> dict: ...   # GET  /analytics/trend
    async def nl2sql_ask(self, question: str, user) -> NL2SQLResponse: ...  # POST /analytics/nl2sql
    async def create_report(self, fmt: str, user) -> dict: ...          # POST /analytics/report
    async def list_reports(self, page) -> dict: ...                     # GET  /analytics/reports
```

### 4.5 验收命令

```bash
pytest tests/test_nl2sql.py -q                       # 校验器 30 例 + 适配器 mock 10 例
python scripts/train_vanna.py --reset ...            # 训练成功率 100%
curl -X POST localhost:8000/api/v1/analytics/nl2sql -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"question":"各部门近三年流失率趋势"}'
# 期望：sql 为 SELECT 且含 organization_id 条件；chart.type = line
```

---

## 五、4 组（Dify）代码规范

### 5.1 `app/adapters/dify/dify_adapter.py`

```python
class DifyAdapter:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=(base_url or settings.DIFY_BASE_URL).rstrip("/"),
            headers={"Authorization": f"Bearer {api_key or settings.DIFY_API_KEY}"},
            timeout=settings.DIFY_TIMEOUT)

    @ai_retry(max_attempts=settings.DIFY_MAX_RETRIES)
    async def chat(self, query: str, *, app_id: str | None = None,
                   conversation_id: str | None = None, user: str = "system") -> dict:
        """POST /chat-messages；返回 {answer, conversation_id, message_id, metadata{usage}}。"""

    async def chat_stream(self, query, *, app_id=None, conversation_id=None, user="system") -> AsyncIterator[dict]:
        """POST /chat-messages (response_mode=streaming)，逐事件 yield {event, data}：
        message / agent_message / workflow_finished / message_end / error。"""

    @ai_retry(max_attempts=settings.DIFY_MAX_RETRIES)
    async def run_workflow(self, inputs: dict, *, workflow_id: str | None = None, user="system") -> dict:
        """POST /workflows/run (blocking)；返回 {task_id, outputs, status, elapsed_time}。"""

    async def upload_document(self, dataset_id: str, filename: str, content: bytes) -> dict:
        """POST /datasets/{id}/document/create-by-file（multipart）。"""

    async def list_documents(self, dataset_id: str, page: int = 1, limit: int = 20) -> dict: ...
    async def delete_document(self, dataset_id: str, doc_id: str) -> bool: ...

    async def _request(self, method, path, **kw) -> dict:
        """统一错误映射：401/403→ExternalServiceError('Dify 鉴权失败')，
        404→ExternalServiceError('应用不存在')，429/5xx→可重试；日志带 request_id。"""
```

### 5.2 `app/adapters/dify/sse.py`（**新增**）

```python
async def iter_sse(resp: httpx.Response) -> AsyncIterator[dict]:
    """解析 Dify SSE：按行读，'event: x' 记事件名，'data: {...}' 产出 {'event': x, 'data': json}。
    兼容 'data: [DONE]' 与空行结束。"""
```

### 5.3 落地页

- `POST /api/v1/agents/chat/stream` 可切到 Dify 后端（`settings.DIFY_APP_ID_CHAT` 非空时）；
- `GET /api/v1/system/info` 返回 `dify_enabled` 与 `dify_apps`；
- `/health/ready` 增加 `dify: {status, reason, elapsed_ms}`。

### 5.4 验收命令

```bash
pytest tests/test_dify.py -q                     # MockTransport，忽略真实网络
python -c "
import asyncio; from app.adapters.dify.dify_adapter import DifyAdapter
print(asyncio.run(DifyAdapter().chat('你好')))"
curl -N -X POST localhost:8000/api/v1/agents/chat/stream -d '{"query":"你好"}' \
     -H "Authorization: Bearer $T" -H 'Content-Type: application/json'
```

---

## 六、测试规范（全组统一）

| 类型 | 位置 | 要求 |
| --- | --- | --- |
| 单元测试 | `tests/test_<组>_*.py` | 不依赖 MySQL / Milvus / Redis / 真实 LLM；外部一律 mock |
| 契约测试 | `tests/test_ai_contract.py`（架构组） | LLM/Skill/Agent 签名、返回结构、错误码 |
| 集成测试 | `tests/test_integration_*.py` | 标记 `@pytest.mark.integration`，无外部服务时 skip（参考现有 `mysql_ready` fixture 写法） |
| 安全测试 | `tests/test_nl2sql_security.py`、`tests/test_rag_permission.py` | 越权/注入反例 |

```python
# tests/conftest.py 追加（架构组维护）
@pytest.fixture
def fake_llm(monkeypatch):
    from app.ai.core import llm_manager
    class FakeLLM:
        async def chat(self, messages, **kw):
            from app.adapters.llm.base_llm import LLMResult
            return LLMResult(content='{"name":"张三","work_years":3}', model="fake",
                             prompt_tokens=10, completion_tokens=5)
        async def embed(self, texts): return [[0.1] * settings.EMBEDDING_DIM for _ in texts]
    monkeypatch.setattr(llm_manager, "get_llm", lambda purpose=None: FakeLLM())
```

**禁止**：为了让测试通过而把业务库切回 SQLite（本项目已明确不使用 SQLite）；测试需要库时用 skip。

---

## 七、Code Review Checklist（架构组逐 PR 检查）

- [ ] 文件头有中文注释（职责 / 思路 / 对外方法 / 待办）
- [ ] 只 import 公共层与登记在案的契约（`BaseLLM` / `BaseSkill` / `BaseAgent` / `BaseVectorStore`）
- [ ] AI 层无裸 SQL、无 ORM 明细、无 `app.repositories.*_repository` 直连（必须经 Service）
- [ ] 权限只从 `get_context()` 取；Skill 忽略 LLM 传入的用户参数
- [ ] 外部调用有超时 + 重试 + 熔断，失败抛 `ExternalServiceError/AIError`
- [ ] 无 `print`、无硬编码密钥/URL/模型名（全部走 `settings`）
- [ ] Token 与耗时埋点到 `TokenManager` / 审计
- [ ] 新增配置项已同步 `settings.py` + `.env.example`
- [ ] 新增依赖已加进 `requirements.txt` 并注明归属组
- [ ] `pytest -q` 全绿；无外部环境时 skip 不 fail

---

## 八、文件所有权总表（避免并行改同一文件）

| 文件/目录 | 归属 | 其他组的改动方式 |
| --- | --- | --- |
| `app/config/settings.py`、`app/config/prompt_config.py` | 架构组 | 提 PR 由架构组合并 |
| `app/patterns/registry.py`、`app/core/*` | 架构组 | 只读 |
| `app/ai/context.py`、`app/ai/core/*`、`app/ai/utils/*` | 架构组 | 只读 |
| `app/adapters/llm/*` | 架构组 | 只读（新增 provider 走 PR） |
| `app/ai/skills/*` | 架构组 | 各组只读调用 |
| `app/ai/agents/base_agent.py`、`app/ai/agent.py` | 架构组 + 2 组 | 双人 review |
| `app/ai/rag/*`、`app/adapters/vector_store/*` | 1 组 | 只读 |
| `app/services/knowledge_service.py`、`app/routers/knowledge_router.py` | 1 组 | — |
| `app/ai/workflows/*`、`app/ai/memory/*`、`app/ai/agents/*_agent.py` | 2 组 | — |
| `app/services/{resume,talent,matching,assessment,training,agent}_service.py` | 2 组 | 模型字段变更需通知 1/3 组 |
| `app/adapters/nl2sql/*`、`app/services/analytics_service.py`、`app/routers/analytics_router.py` | 3 组 | 只读 |
| `app/adapters/dify/*` | 4 组 | 只读 |
| `app/routers/agent_router.py` | 2 组 + 4 组 | 双方 review |
| `tests/conftest.py` | 架构组 | 提 PR |

---

## 九、每组的第一个 PR 建议（D2–D3 就能开）

| 组 | 第一个 PR | 内容 | 预期行数 |
| --- | --- | --- | --- |
| 架构组 | `feat(ai): 上下文档位 + LLM 适配器 + Skill 基类` | context / base_llm / openai_adapter / retry / output_parser / skills/base | 400–600 |
| 1 组 | `feat(rag): 文档加载与切片` | document_loader / document_splitter + 单测 | 300 |
| 2 组 | `feat(agent): 工作流构建器骨架` | graph_builder / state_manager（memory 后端）+ 单测 | 300 |
| 3 组 | `feat(nl2sql): SQL 安全校验器` | sql_validator + 30 条反例测试 | 250 |
| 4 组 | `feat(dify): Dify 适配器与 SSE 解析` | dify_adapter / sse.py + MockTransport 测试 | 300 |

> 每个 PR 描述模板：**改了什么 / 表结构有无变更 / 需要谁 review / 如何验证（可复制的命令）**。
