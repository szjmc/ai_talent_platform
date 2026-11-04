# 系统架构说明

> 本文档的架构图使用**内联 SVG 手写**，不依赖任何 CDN / 网络字体 / 外部脚本，
> 离线用浏览器直接打开即可完整显示。

## 一、六层架构总览

<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 720" width="960" height="720" font-family="-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/>
    </marker>
    <style>
      .layer-title { font-size: 15px; font-weight: 700; fill: #0f172a; }
      .box-text { font-size: 12.5px; fill: #1e293b; }
      .sub-text { font-size: 11px; fill: #475569; }
      .tag { font-size: 10.5px; fill: #ffffff; }
      .arrow { stroke: #64748b; stroke-width: 1.6; fill: none; marker-end: url(#arrow); }
      .layer-rect { rx: 10; ry: 10; }
      .card { rx: 8; ry: 8; }
    </style>
  </defs>

  <!-- 背景 -->
  <rect x="0" y="0" width="960" height="720" fill="#f8fafc"/>

  <!-- 标题 -->
  <text x="32" y="42" font-size="20" font-weight="700" fill="#0f172a">AI 数字化人才平台 · 六层架构</text>
  <text x="32" y="64" class="sub-text">分层解耦 · 独立可扩展 · FastAPI 全异步 · ORM 全对象映射</text>

  <!-- ============ 1 业务应用层 ============ -->
  <g>
    <rect x="28" y="86" width="904" height="96" class="layer-rect" fill="#eff6ff" stroke="#bfdbfe"/>
    <text x="44" y="110" class="layer-title">① 业务应用层</text>
    <rect x="44" y="122" width="124" height="44" class="card" fill="#3b82f6"/>
    <text x="106" y="149" class="tag" text-anchor="middle">人才智能档案</text>
    <rect x="180" y="122" width="110" height="44" class="card" fill="#3b82f6"/>
    <text x="235" y="149" class="tag" text-anchor="middle">AI 测评</text>
    <rect x="302" y="122" width="110" height="44" class="card" fill="#3b82f6"/>
    <text x="357" y="149" class="tag" text-anchor="middle">智能匹配</text>
    <rect x="424" y="122" width="110" height="44" class="card" fill="#3b82f6"/>
    <text x="479" y="149" class="tag" text-anchor="middle">智能培训</text>
    <rect x="546" y="122" width="110" height="44" class="card" fill="#3b82f6"/>
    <text x="601" y="149" class="tag" text-anchor="middle">数据决策</text>
    <rect x="668" y="122" width="110" height="44" class="card" fill="#3b82f6"/>
    <text x="723" y="149" class="tag" text-anchor="middle">智能问答</text>
    <rect x="790" y="122" width="126" height="44" class="card" fill="#3b82f6"/>
    <text x="853" y="149" class="tag" text-anchor="middle">移动端 / 自动化</text>
  </g>
  <path class="arrow" d="M 480 182 L 480 204"/>

  <!-- ============ 2 服务接口层 ============ -->
  <g>
    <rect x="28" y="206" width="904" height="128" class="layer-rect" fill="#f0fdf4" stroke="#bbf7d0"/>
    <text x="44" y="230" class="layer-title">② 服务接口层（FastAPI 全异步 · 统一前缀 /api/v1）</text>

    <rect x="44" y="242" width="176" height="78" class="card" fill="#ffffff" stroke="#86efac"/>
    <text x="132" y="264" class="box-text" text-anchor="middle" font-weight="700">routers/ 接口层</text>
    <text x="132" y="283" class="sub-text" text-anchor="middle">auth / user / organization</text>
    <text x="132" y="300" class="sub-text" text-anchor="middle">talent / position / …</text>

    <rect x="232" y="242" width="176" height="78" class="card" fill="#ffffff" stroke="#86efac"/>
    <text x="320" y="264" class="box-text" text-anchor="middle" font-weight="700">schemas/ 数据校验</text>
    <text x="320" y="283" class="sub-text" text-anchor="middle">Create / Update</text>
    <text x="320" y="300" class="sub-text" text-anchor="middle">Query / Response</text>

    <rect x="420" y="242" width="176" height="78" class="card" fill="#ffffff" stroke="#86efac"/>
    <text x="508" y="264" class="box-text" text-anchor="middle" font-weight="700">services/ 业务层</text>
    <text x="508" y="283" class="sub-text" text-anchor="middle">业务规则 · 流程编排</text>
    <text x="508" y="300" class="sub-text" text-anchor="middle">事务边界</text>

    <rect x="608" y="242" width="146" height="78" class="card" fill="#ffffff" stroke="#86efac"/>
    <text x="681" y="264" class="box-text" text-anchor="middle" font-weight="700">core/ 公共能力</text>
    <text x="681" y="283" class="sub-text" text-anchor="middle">响应 · 异常 · 权限</text>
    <text x="681" y="300" class="sub-text" text-anchor="middle">依赖 · 中间件 · 分页</text>

    <rect x="766" y="242" width="150" height="78" class="card" fill="#ffffff" stroke="#86efac"/>
    <text x="841" y="264" class="box-text" text-anchor="middle" font-weight="700">config/ 配置</text>
    <text x="841" y="283" class="sub-text" text-anchor="middle">settings · logging</text>
    <text x="841" y="300" class="sub-text" text-anchor="middle">prompt</text>
  </g>
  <path class="arrow" d="M 480 334 L 480 352"/>

  <!-- ============ 3 AI 中台层 ============ -->
  <g>
    <rect x="28" y="354" width="904" height="132" class="layer-rect" fill="#faf5ff" stroke="#e9d5ff"/>
    <text x="44" y="378" class="layer-title">③ AI 中台层 / 智能能力层</text>

    <rect x="44" y="390" width="130" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="109" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/core</text>
    <text x="109" y="431" class="sub-text" text-anchor="middle">llm_manager</text>
    <text x="109" y="448" class="sub-text" text-anchor="middle">model_selector</text>
    <text x="109" y="465" class="sub-text" text-anchor="middle">token_manager</text>

    <rect x="184" y="390" width="150" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="259" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/agents 多智能体</text>
    <text x="259" y="431" class="sub-text" text-anchor="middle">档案 / 测评 / 匹配</text>
    <text x="259" y="448" class="sub-text" text-anchor="middle">培训 / 决策 / 助手</text>
    <text x="259" y="465" class="sub-text" text-anchor="middle">LangGraph 有状态</text>

    <rect x="344" y="390" width="140" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="414" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/workflows</text>
    <text x="414" y="431" class="sub-text" text-anchor="middle">简历解析流</text>
    <text x="414" y="448" class="sub-text" text-anchor="middle">匹配流</text>
    <text x="414" y="465" class="sub-text" text-anchor="middle">综合分析流</text>

    <rect x="494" y="390" width="136" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="562" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/rag</text>
    <text x="562" y="431" class="sub-text" text-anchor="middle">loader → splitter</text>
    <text x="562" y="448" class="sub-text" text-anchor="middle">embedding → retriever</text>
    <text x="562" y="465" class="sub-text" text-anchor="middle">rag_pipeline</text>

    <rect x="640" y="390" width="130" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="705" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/skills</text>
    <text x="705" y="431" class="sub-text" text-anchor="middle">简历 / 匹配</text>
    <text x="705" y="448" class="sub-text" text-anchor="middle">SQL / 知识库</text>
    <text x="705" y="465" class="sub-text" text-anchor="middle">可编排组件</text>

    <rect x="780" y="390" width="136" height="82" class="card" fill="#ffffff" stroke="#d8b4fe"/>
    <text x="848" y="412" class="box-text" text-anchor="middle" font-weight="700">ai/memory</text>
    <text x="848" y="431" class="sub-text" text-anchor="middle">conversation_memory</text>
    <text x="848" y="448" class="sub-text" text-anchor="middle">state_manager</text>
    <text x="848" y="465" class="sub-text" text-anchor="middle">多轮上下文</text>
  </g>
  <path class="arrow" d="M 480 486 L 480 504"/>

  <!-- ============ 4 数据访问层 ============ -->
  <g>
    <rect x="28" y="506" width="904" height="96" class="layer-rect" fill="#fff7ed" stroke="#fed7aa"/>
    <text x="44" y="530" class="layer-title">④ 数据访问层（Repository · Model）</text>

    <rect x="44" y="542" width="200" height="46" class="card" fill="#f97316"/>
    <text x="144" y="562" class="tag" text-anchor="middle" font-weight="700">repositories/ 通用 CRUD</text>
    <text x="144" y="579" class="tag" text-anchor="middle" font-size="10">BaseRepository</text>

    <rect x="256" y="542" width="200" height="46" class="card" fill="#f97316"/>
    <text x="356" y="562" class="tag" text-anchor="middle" font-weight="700">具体仓库</text>
    <text x="356" y="579" class="tag" text-anchor="middle" font-size="10">user / organization / talent …</text>

    <rect x="468" y="542" width="200" height="46" class="card" fill="#f97316"/>
    <text x="568" y="562" class="tag" text-anchor="middle" font-weight="700">models/ ORM Base</text>
    <text x="568" y="579" class="tag" text-anchor="middle" font-size="10">时间戳 · 逻辑删除 · 乐观锁</text>

    <rect x="680" y="542" width="236" height="46" class="card" fill="#f97316"/>
    <text x="798" y="562" class="tag" text-anchor="middle" font-weight="700">infrastructure/database</text>
    <text x="798" y="579" class="tag" text-anchor="middle" font-size="10">AsyncEngine · Session · 事务</text>
  </g>
  <path class="arrow" d="M 480 602 L 480 620"/>

  <!-- ============ 5 存储层 ============ -->
  <g>
    <rect x="28" y="622" width="904" height="76" class="layer-rect" fill="#f1f5f9" stroke="#cbd5e1"/>
    <text x="44" y="646" class="layer-title">⑤ 数据存储层（四库协同）</text>

    <rect x="44" y="656" width="200" height="32" class="card" fill="#475569"/>
    <text x="144" y="677" class="tag" text-anchor="middle">MySQL · 业务主数据</text>
    <rect x="256" y="656" width="200" height="32" class="card" fill="#475569"/>
    <text x="356" y="677" class="tag" text-anchor="middle">PgSQL · 时序 / 分析宽表</text>
    <rect x="468" y="656" width="200" height="32" class="card" fill="#475569"/>
    <text x="568" y="677" class="tag" text-anchor="middle">Milvus · 语义向量检索</text>
    <rect x="680" y="656" width="236" height="32" class="card" fill="#475569"/>
    <text x="798" y="677" class="tag" text-anchor="middle">Redis · 缓存 / 会话 / 限流</text>
  </g>

  <!-- 右侧外部能力标注 -->
  <g>
    <rect x="28" y="708" width="904" height="1" fill="#e2e8f0"/>
  </g>
</svg>

## 二、外部能力适配层（adapters /）

```
adapters/
├── llm/            base_llm.py（统一接口）+ provider_config（供应商目录）
│                   → openai / qwen / deepseek / ollama 适配器（模型由配置决定）
├── dify/           Dify 工作流与应用调用
├── nl2sql/         Vanna 适配 + sql_validator 安全校验（只读、限行、表白名单）
├── vector_store/   Milvus 向量库适配（统一向量库接口）
└── storage/        BaseStorage 契约 → local / minio（S3 SigV4）/ oss（Signature V1）/ s3
```

**职责边界（容易混淆，务必区分）**

| 位置 | 只负责 |
| --- | --- |
| `infrastructure/vector/milvus.py` | 建立 Milvus 客户端连接 |
| `adapters/vector_store/milvus_adapter.py` | 把客户端封装成统一的「向量库接口」 |
| `ai/rag/*` | RAG 业务流程（切片、检索、重排、组装上下文） |

## 三、依赖方向（只允许自上而下）

```
业务应用层
   ↓
routers（接口）
   ↓
schemas（校验）      core（响应 / 异常 / 权限 / 依赖）
   ↓
services（业务）  →  ai（智能编排）  →  adapters（第三方能力）
   ↓
repositories（数据访问）
   ↓
models（ORM 映射） + infrastructure（连接资源）
   ↓
MySQL / PgSQL / Milvus / Redis / LLM / Dify
```

禁止事项：

- Router 直接访问数据库或写业务规则；
- Service 里写大量 ORM 查询（交给 Repository）；
- Repository 做业务判断（例如「是否满足岗位要求」）；
- AI 绕过 Service 的权限校验直接查敏感数据；
- Adapter 里出现人才、岗位等业务概念；
- Infrastructure 里出现简历解析、人岗匹配等业务流程。

## 四、一次请求的完整链路（以登录为例）

```
POST /api/v1/auth/login
  → TraceIdMiddleware 生成 trace_id（贯穿日志）
  → AccessLogMiddleware 记录耗时
  → auth_router.login()：Schema 校验入参
  → AuthService.login()：查用户 → 校验状态 → 校验密码 → 更新登录信息
  → UserRepository（BaseRepository）：SQLAlchemy 查询 users / roles
  → core.security：签发 access_token / refresh_token
  → core.response.success()：统一 {code, message, data, trace_id}
  → get_db()：请求结束自动 commit（异常则 rollback）
```

## 五、数据流与 AI 链路（简历解析示例）

```
① POST /api/v1/resumes/upload
② resume_router → ResumeService：校验类型 / 大小 → sha256 秒传判断 → 落盘 → 建 resume 记录
③ 返回 resume_id（status=uploaded），前端轮询或 SSE 订阅
④ ai/workflows/resume_workflow：extract_text → clean_text → LLM 结构化 → validate → persist
⑤ adapters/llm/qwen_adapter：统一模型调用（记录 token / 耗时）
⑥ resume_repository 写回 parsed_data；talent_service 创建 / 更新人才档案
⑦ ai/agents/talent_agent 生成画像 → talent_portraits
⑧ 返回统一结构，前端展示解析结果
```
