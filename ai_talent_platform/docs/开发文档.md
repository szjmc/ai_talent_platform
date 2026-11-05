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

## 三、分工建议（三天节奏，按文件划分，避免冲突）

| 角色 | 负责目录 | 主要产出 |
| --- | --- | --- |
| A（公共层，已完成） | `config/ core/ infrastructure/ repositories/base.py models/base.py routers/router.py` | 框架、认证、异常、响应、Repository 基类 |
| B 人才与岗位 | `models/{talent,position,resume}.py` `repositories/{talent,position,resume}_repository.py` `schemas/*` `services/*` `routers/*` | 人才档案、简历上传解析入口、岗位管理 |
| C 测评与培训 | `models/{assessment,training}.py` + 对应 repo/service/schema/router | 测评流程、培训计划 |
| D 匹配与分析 | `models/{matching,knowledge}.py` + matching/analytics/knowledge 全链路 | 人岗匹配、知识库、统计 |
| E AI 能力 | `ai/**` `adapters/llm/**` `adapters/vector_store/**` | LLM 适配、Agent、RAG、工作流 |

每个模块的开发顺序固定：**Model → Repository → Schema → Service → Router → 注册 router → 补测试**。

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
