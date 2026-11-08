# AI 数字化人才平台 · 后端

企业级 AI 原生人才智能平台后端。技术栈：**FastAPI（全异步）+ SQLAlchemy 2.0 ORM（全对象映射，无裸 SQL）
+ MySQL 8（业务主库）+ PgSQL（分析/时序库）+ Redis（缓存）+ Milvus（向量）+ LangChain / LangGraph + Dify + Vanna**。

> 数据库说明：**不使用 SQLite**。业务主数据走 MySQL（`mysql+aiomysql`），
> 海量履历/测评历史/分析宽表走 PgSQL（`postgresql+asyncpg`，也是 Vanna NL2SQL 的查询库）。

当前进度：**公共层（P0）已完成并通过验收**，认证、用户、组织三大模块可跑通；
ORM 层已完整落地：MySQL 25 张业务表 + PgSQL 6 张分析表，全局映射见 `docs/orm_mapping.md`；
架构组（AI 公共层 + Skills + 集成）已完整落地：LLM 适配器（qwen / OpenAI / DeepSeek / Ollama，模型全部配置驱动）
/ LLMManager / Agent 基类与调度 / 上下文 / 记忆 / 文件存储（本地 / MinIO / 阿里云 OSS / S3）/ 事件总线 / 匹配策略，
见 `docs/plan/05-代码标签与优先级总表.md` 与 `docs/storage_and_llm_adapters.md`；全局 ORM 映射由 `python scripts/dump_orm_mapping.py` 自动生成；
PgSQL 分析库另有独立初始化脚本 `scripts/init_pgsql.py`（含分区父表和 DEFAULT 分区，数据同步/ETL 仍未实现）；
其余业务模块（人才、分析已实现；岗位、简历、测评、培训、匹配、知识库、文件等）已建好骨架，
每个文件开头写明实现思路与待写代码，按 TODO 补齐即可。

---

## 快速开始

```bash
# 0. 前置：MySQL 8（127.0.0.1:3306）与 PgSQL 15+（127.0.0.1:5432）已启动
#    没有本地数据库？一条命令拉起（账号密码与 .env.example 一致）：
#    docker compose up -d            # MySQL 8 + PgSQL 16
#    docker compose --profile cache up -d   # 额外启动 Redis
#    docker compose --profile storage up -d # 额外启动 MinIO（STORAGE_BACKEND=minio 时使用）
cd ai_talent_platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 按本地实际修改 DB_* / PGSQL_* 账号密码
python scripts/init_db.py     # MySQL 建库建表 + 内置角色 + PgSQL 建分析库/表
python scripts/init_pgsql.py  # 可选：只初始化 PgSQL 分析库/表（--check 检查，--print-ddl 看 DDL）
python scripts/dump_orm_mapping.py --output docs/orm_mapping.md  # 可选：导出全局 ORM 映射关系
python scripts/demo_ai.py           # 可选：一键演示 AI 能力链路（默认离线，--with-llm 走真实模型）
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

数据库连接自检：`GET /health/ready` 会分别返回 MySQL / PgSQL 的状态、失败原因与修复建议
（例如 `connection_refused` / `auth_failed` / `database_missing`）。

测试：`pytest -q`（使用独立的 MySQL 测试库 `ai_talent_platform_test`，不污染开发库；
库不可用时用例会自动 skip 并打印连接目标）。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| **[docs/group_boundaries_and_tasks.md](docs/group_boundaries_and_tasks.md)** | **★防重复开发必读总纲**：十条防重复铁律、已落地冻结资产表、九大跨组重叠区切分裁定、四库读写权限矩阵、**5 大研发组逐文件详细代码开发说明书（业务模块全部垂直归口各组）** |
| **[docs/requirements_specification.md](docs/requirements_specification.md)** | **★需求规格说明书（SRS）**：角色与权限、五大 AI 能力量化指标、15 大业务模块功能规格、非功能指标、**需求—研发组唯一归属追踪矩阵（RTM）** |
| **[docs/system_design_document.md](docs/system_design_document.md)** | **★系统总体与详细设计文档（SDD）**：六层单向依赖架构、四库与 Redis 键空间设计、5 条端到端业务与 AI 调用时序、Skill↔Service 桥接与安全降级设计 |
| [docs/公共层使用手册.md](docs/公共层使用手册.md) | **必读**：响应/异常/数据库/Repository/认证权限/新增模块全流程/FAQ |
| **[docs/storage_and_llm_adapters.md](docs/storage_and_llm_adapters.md)** | **对象存储与多模型适配手册**：MinIO / 阿里云 OSS / DeepSeek 配置、预签名下载、模型按用途选择与降级、扩展新厂商 |
| [docs/architecture.md](docs/architecture.md) | 六层架构图（**内联 SVG，离线可打开**）、依赖规则、调用链路 |
| [docs/development.md](docs/development.md) | 开发规范与五大组协作约定 |
| [docs/api_document.md](docs/api_document.md) | **接口契约（全模块）**：统一响应/错误码/JWT/RBAC 权限矩阵 + 15 个模块的 method/path/权限/请求字段/示例/错误码 |
| [docs/database_design.md](docs/database_design.md) | **数据库设计（完整明确）**：内联 SVG ER 图 + 25 张表字段字典 + 可直接执行的 MySQL 8 DDL + PgSQL/Milvus/Redis 设计 |
| [docs/orm_mapping.md](docs/orm_mapping.md) | **全局 ORM 映射字典**：MySQL 25 张表 + PgSQL 6 张表自动导出清单 |
| [docs/已实现功能清单.md](docs/已实现功能清单.md) | 平台当前实际已落地能力与待开发骨架逐项对照清单 |
| [docs/team_sprint_plan.md](docs/team_sprint_plan.md) | **5 组协同开发总计划**：职责划分、里程碑、跨组契约、验收清单、站会节奏 |
| [docs/plan/](docs/plan/) 各组独立计划 | [00 架构组](docs/plan/00-架构组.md) ｜ [01 RAG 组](docs/plan/01-RAG组.md) ｜ [02 Agent 组](docs/plan/02-Agent组.md) ｜ [03 Vanna 组](docs/plan/03-Vanna组.md) ｜ [04 Dify 组](docs/plan/04-Dify组.md) ｜ [05 代码标签总表](docs/plan/05-代码标签与优先级总表.md) |
| [docs/team_code_spec.md](docs/team_code_spec.md) | **分组代码实现规范**：每个文件的方法签名、返回结构、代码骨架、验收命令、文件唯一所有权表 |

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
| 智能体 | `GET /api/v1/agents` `POST /api/v1/agents/chat` `POST /api/v1/agents/chat/stream` `GET /api/v1/agents/conversations` `GET /api/v1/agents/conversations/{id}/messages` `POST /api/v1/agents/tasks` `GET /api/v1/agents/tasks/{task_id}` |
| 系统 | `GET /api/v1/system/info` |

其余 8 个模块当前为占位路由（返回 501 + 主责研发组提示，见 `data.owner`），实现后替换 `app/routers/router.py` 中的 include 即可。


## 目录速览

```
ai_talent_platform/
├── main.py                 # 启动入口
├── app/                    # 应用代码（config/core/models/repositories/services/schemas/routers/ai/adapters/infrastructure/patterns/utils）
├── scripts/                # init_db / init_pgsql / dump_orm_mapping / create_admin / seed_data
├── tests/                  # conftest + 公共层验收测试（8 个用例）
├── docs/                   # 架构 / 手册 / 规范 / 数据库设计
├── requirements.txt
└── .env.example
```

## 邀约链接与摄像头配置

笔试、测评监考和视频面试需要浏览器的安全上下文：候选人应通过**可访问、证书受信任的 HTTPS 域名**打开前端。`http://192.168.x.x` 等内网 HTTP 地址不能使用摄像头/麦克风；`http://localhost` 只在访问设备本机有效，不能发给其他人。不能通过代码绕过浏览器限制。

1. 将前端部署到 HTTPS 域名，反向代理 `/api`（包括 WebSocket 升级）和旧版笔试入口 `/exam` 到后端，所有接收邀约的人都要能访问该域名。生产环境由可信代理设置 `Host`、`X-Forwarded-Host` 和 `X-Forwarded-Proto`，并清除客户端伪造的转发头。
2. 在后端 `.env` 设置 `INVITE_BASE_URL=https://你的前端域名`（不要带 `/invite`），重启后端。未配置时回退请求 Host 的警告表示链接可能指向内网或错误协议，并非邮件发送失败。已发送的旧链接不会自动更新；重新发送之前请从邀约记录复制更新后的链接，并通过安全渠道通知候选人。
3. 确认邮件/短信发送结果、候选人端能打开链接，并允许浏览器访问设备。面试官使用单独的面试官入口；该入口和候选人入口使用同一令牌，请勿将面试官链接发给候选人。

开发环境也可让 Vite 直接使用证书：准备覆盖访问域名或 IP 的证书及私钥，并在每台访问设备信任签发该证书的 CA。然后在 `frontend` 目录执行：

```bash
VITE_HTTPS_CERT=/path/to/cert.pem VITE_HTTPS_KEY=/path/to/key.pem npm run dev
```

使用 `https://证书对应的域名或IP:5173` 打开，并将后端 `INVITE_BASE_URL` 配成相同地址。自签证书默认不被其他设备信任；仅在浏览器点“继续访问”不保证安全上下文或设备权限。不要将私钥提交到仓库。生产环境应使用可信证书和 HTTPS 反向代理，而非 Vite 开发服务器。
