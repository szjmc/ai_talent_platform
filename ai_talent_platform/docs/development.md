# 开发规范与协作约定

## 一、统一规范（多人并行开发前必须达成一致）

| 项目 | 统一要求 | 示例 |
| --- | --- | --- |
| API 前缀 | `/api/v1` | `settings.API_PREFIX` |
| 文件命名 | 小写英文 + 下划线 | `talent_router.py` |
| 类名 | 大驼峰 | `TalentService` |
| 函数名 / 变量 | 小写下划线 | `create_talent` |
| 数据库字段 | 小写下划线 | `created_at` |
| Schema 命名 | `XxxCreate/Update/Query/Response` | `TalentCreate` |
| Model 命名 | 单数大驼峰 | `Talent` |
| Repository | `XxxRepository` | `TalentRepository` |
| Service | `XxxService` | `TalentService` |
| Router | `xxx_router.py` 内的 `router` | `talent_router.py` |
| 响应结构 | `{code, message, data, trace_id}` | `core.response.success` |
| 错误处理 | 统一抛业务异常 | `NotFoundError("人才不存在")` |
| 数据库访问 | 统一 AsyncSession + ORM | 禁止裸 SQL |
| 权限控制 | 依赖注入 | `Depends(require_permission(...))` |
| 配置管理 | 环境变量 + settings | 禁止硬编码 |
| 异步 | 全部 `async def` | 禁止同步阻塞调用 |

## 二、三个核心契约（先定死，后期不返工）

### 1. 数据库契约

- 所有业务表继承 `app.models.base.BaseModel`（id / 时间戳 / 逻辑删除 / 乐观锁 / 操作人）；
- 字段一律小写下划线；金额用 `Numeric(12,2)`；枚举用 `String` + 中文 comment，不用 MySQL ENUM；
- 逻辑删除字段 `is_deleted`，唯一约束要配合 `is_deleted` 处理（或删除时改写唯一字段）;
- 变更表结构必须：改 model → 在 `models/__init__.py` 注册 → 生成 Alembic 迁移 → 通知全组。

### 2. 接口契约

```http
POST /api/v1/talents
Content-Type: application/json
Authorization: Bearer <access_token>

{ "name": "张三", "education": "本科", "work_years": 3 }
```

```json
{ "code": 200, "message": "创建成功", "data": { "id": 1, "name": "张三" }, "trace_id": "..." }
```

约定：

- 列表接口统一分页：`?page=1&page_size=20&keyword=x&order_by=created_at&order=desc`，
  返回 `data = {items, total, page, page_size, pages}`；
- 时间格式 `YYYY-MM-DD HH:mm:ss`（响应由 Pydantic 序列化为 ISO8601，前端自行格式化）；
- 布尔字段用 `is_xxx` / `has_xxx`；
- 所有 ID 类路径参数用 `int`（>0），在 Schema 层 `gt=0` 校验。

### 3. Service 调用契约

- 跨模块协作只调用对方 **Service 的公开方法**，禁止 import 别人的 Repository；
- Service 方法签名：`async def xxx(self, data: SchemaType, *, operator: User | None = None) -> Model | PageResult`；
- 事务边界在 Service；Repository 不 commit；
- 失败即抛业务异常，返回 `None` 表示「查询未命中」仅限 Repository 层。

## 三、五大研发组职责边界（严格按文件标签认领，禁止跨组重复编码）

> **强制执行标准**：全仓库 195 个 `.py` 文件、4 个根配置文件与 5 个 MySQL 测试 SQL 已全量打上组标签并由 `tests/test_code_tags.py` 实时守门。全平台严格划分为 `[架构组]`、`[RAG组]`、`[Agent组]`、`[Vanna组]`、`[Dify组]` 五大研发组：每个业务模块按领域相关性垂直归口至某一组，由该组端到端闭环负责 `Model → Repository → Schema → Service → Router`。详细方法级契约与防重复红线见 **[五大研发组职责边界与防重复开发总规范](group_boundaries_and_tasks.md)**。

| 组标签 | 当前状态 | 独占负责目录 / 模块 | 严禁越界与防重复红线 |
| --- | --- | --- | --- |
| `[架构组]` | ✅ 公共层与 AI 底座冻结<br>🔧 文件/审计待补齐 | `config/` `core/` `patterns/` `utils/` `ai/{context,core,utils,skills,memory,agent}` `ai/agents/base_agent.py` `adapters/{llm,storage}` `infrastructure/{database,cache,storage,messaging}` + 认证/用户/角色/组织（✅）+ `files` / `audit` 横向服务 | 对外提供冻结底座；其余 4 组只读 `import` 复用，**严禁任何组重写**；认证链路安全红线不得旁路 |
| `[RAG组]` | 🔧 待开发 | `infrastructure/vector/` `adapters/vector_store/` `ai/rag/` + `knowledge`（知识库）+ `talents`（人才档案）+ `resumes`（简历管理）全链路 | 全平台唯一 Milvus 网关、唯一 `EmbeddingService`、唯一 `document_loader`；禁改 `knowledge_skill.py`；严禁编写岗位/匹配/测评/培训 Service |
| `[Agent组]` | 🔧 待开发 | `ai/workflows/` `ai/agents/*_agent.py`（6 个子类）`conversation_repository` `agent_service` `agent_router` + `positions`（岗位）+ `matchings`（人岗匹配）全链路 | 继承 `BaseAgent`、复用 `ConversationMemory/StateManager`；**严禁重写 `[架构组] MatchStrategyFactory` 打分公式、严禁写 SQL、严禁重建 ORM** |
| `[Vanna组]` | 🔧 待开发 | `adapters/nl2sql/` + `analytics`（看板问数）+ `assessments`（在线测评）全链路 + 新增 `scripts/sync_analytics.py` | 全平台唯一 NL2SQL 出口与 MySQL→PgSQL 宽表同步；禁改 `models/analytics.py` 与 `sql_skill.py`；严禁绕过 `sql_validator` |
| `[Dify组]` | 🔧 待开发 | `adapters/dify/{dify_adapter,sse,health}.py` + `trainings`（培训与督办）全链路 + 新增 `scripts/sync_dify_workflows.py` + `AgentService` 双模分支 | 严禁另建 `/api/v1/dify/*` 重复路由，严禁接管 `/knowledge/*` 或绕过 `sql_validator` |

每个待开发业务模块的开发顺序固定：**Repository（继承 BaseRepository）→ Schema → Service（兼容 Skill 无参会话桥接）→ Router → 提 PR 注册到 `router.py` → 补带标签测试**。

## 四、代码质量约定

1. 每个文件开头写中文模块注释：职责 / 实现思路 / 对外方法 / 待办清单（参考现有文件）；
2. 关键函数写 docstring（用途 + 参数 + 异常）；
3. 异常只抛业务异常，禁止 `raise Exception("xxx")`；
4. 日志用 `get_logger(__name__)`，禁止 `print`；
5. 不在循环里查库（N+1），批量场景用 `in_` 查询或 `selectinload`；
6. 敏感字段（密码、身份证、手机号）禁止进日志，必要时用 `mask_sensitive`；
7. 提交前跑 `pytest -q`，新增功能必须补集成用例（参考 `tests/test_common_layer.py`）。

## 五、Git 约定

- 分支：`feature/<模块名>`，禁止直接推 main；
- 提交信息：`feat(talent): 新增人才创建接口`、`fix(auth): 修复登录失败计数未清零`；
- PR 必须说明：改了哪些文件、动了哪些表、是否需要其他人同步改 Schema。

## 六、联调 Checklist

- [ ] `/docs` 中接口可见，响应模型正确、字段有中文说明；
- [ ] 无 Token → 401；权限不足 → 403；资源不存在 → 404；重复 → 409；
- [ ] 参数错误 → 422 且 `data.errors` 明确指出字段；
- [ ] 分页参数越界被收敛（page_size 上限 200）；
- [ ] 写操作有审计或日志；
- [ ] 响应不含 `password_hash` 等敏感字段；
- [ ] 表已在 `/api/v1/system/info` 的 `orm_tables` 中出现；
- [ ] `pytest -q` 全绿。
