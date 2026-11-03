# AI 数字化人才平台 · 后端

企业级 AI 原生人才智能平台后端。技术栈：**FastAPI（全异步）+ SQLAlchemy 2.0 ORM（全对象映射，无裸 SQL）
+ MySQL / SQLite + Redis（可选）+ Milvus（可选）+ LangChain / LangGraph + Dify + Vanna**。

当前进度：**公共层（P0）已完成并通过验收**，认证、用户、组织三大模块可跑通；
其余业务模块（人才、岗位、简历、测评、培训、匹配、知识库、分析、Agent、文件）已建好骨架，
每个文件开头写明实现思路与待写代码，按 TODO 补齐即可。

---

## 快速开始

```bash
cd ai_talent_platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 默认 SQLite，零外部依赖即可启动
python scripts/init_db.py     # 建表 + 内置角色
python scripts/create_admin.py -u admin -p Admin@12345
python scripts/seed_data.py   # 可选：演示部门树 + 测试账号（密码 Test@12345）

python main.py                # http://127.0.0.1:8000/docs
```

| 地址 | 用途 |
| --- | --- |
| `/docs` | Swagger（右上角 Authorize 填 access_token） |
| `/redoc` | ReDoc |
| `/health` `/health/ready` | 存活 / 就绪探针 |
| `/api/v1/system/info` | 框架装配自检（数据库、ORM 表、已实现模块） |

切换 MySQL：改 `.env` 中 `DB_DRIVER=mysql` 与 `DB_*` 后重跑 `scripts/init_db.py`，代码无需改动。

测试：`pytest -q`（临时 SQLite，不污染开发库）。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/公共层使用手册.md](docs/公共层使用手册.md) | **必读**：响应/异常/数据库/Repository/认证权限/新增模块全流程/FAQ |
| [docs/architecture.md](docs/architecture.md) | 六层架构图（**内联 SVG，离线可打开**）、依赖规则、调用链路 |
| [docs/development.md](docs/development.md) | 开发规范与协作分工、接口契约 |
| [docs/database_design.md](docs/database_design.md) | 数据库设计与表结构约定 |

---

## 分层结构

```
routers   → 接口层：收请求、调 Service、包统一响应
schemas   → 校验层：Create / Update / Query / Response
services  → 业务层：业务规则、流程编排、事务边界
repositories → 数据访问层：BaseRepository 通用 CRUD + 业务特有查询
models    → ORM 层：Base + Timestamp/SoftDelete/Operator Mixin + BaseModel
ai        → AI 能力层：llm_manager / agents / workflows / rag / memory / skills
adapters  → 外部适配：llm / dify / nl2sql / vector_store / storage
infrastructure → 基础设施：database / cache / vector / storage / messaging
core      → 公共能力：application / lifespan / dependencies / security /
            permissions / exceptions / exception_handlers / middleware /
            response / pagination
config    → 配置：settings / logging / prompt
```

依赖方向严格自上而下；Router 不碰数据库、Service 不写 SQL、Repository 不做业务判断。

## 统一响应

```json
{ "code": 200, "message": "success", "data": {}, "trace_id": "0a10f00426934e71" }
```

失败统一抛 `BusinessException` 子类（`NotFoundError` / `DuplicateError` / `PermissionDeniedError` …），
由全局处理器转换为相同结构，并同步 HTTP 状态码。

## 已实现模块

| 模块 | 接口 |
| --- | --- |
| 认证 | `POST /api/v1/auth/register` `POST /api/v1/auth/login` `POST /api/v1/auth/refresh` `GET /api/v1/auth/me` `POST /api/v1/auth/change-password` `POST /api/v1/auth/logout` |
| 用户 | `GET/POST /api/v1/users` `GET/PUT/DELETE /api/v1/users/{id}` `PATCH /{id}/status` `PUT /{id}/roles` `POST /{id}/reset-password` |
| 组织 | `GET /api/v1/organizations/tree` `GET/POST /api/v1/organizations` `GET/PUT/DELETE /{id}` `PATCH /{id}/move` |
| 系统 | `GET /api/v1/system/info` |

其余模块当前为占位路由（返回 501 + 负责人提示），实现后替换 `app/routers/router.py` 中的 include 即可。

## 目录速览

```
ai_talent_platform/
├── main.py                 # 启动入口
├── app/                    # 应用代码（config/core/models/repositories/services/schemas/routers/ai/adapters/infrastructure/patterns/utils）
├── scripts/                # init_db / create_admin / seed_data
├── tests/                  # conftest + 公共层验收测试（8 个用例）
├── docs/                   # 架构 / 手册 / 规范 / 数据库设计
├── requirements.txt
└── .env.example
```
