# API 接口文档（统一规范 + 全模块契约）

> 版本：**v1.0.0** ｜ 基础路径：`/api/v1` ｜ 交互式文档：`/docs`（Swagger）、`/redoc`
> 本文档是前后端联调的**唯一契约来源**。凡与代码冲突，以本文档为准并提 issue 修正。
> 文档状态标记：`✅ 已实现` / `🚧 待实现（接口契约已定，返回 501 占位）`
>
> 相关文档：[公共层使用手册](公共层使用手册.md) ｜ [数据库设计](database_design.md) ｜ [架构说明](architecture.md) ｜ 在线调试 `/docs`

---

## 目录

1. [全局约定](#api-global)
2. [统一响应与错误码](#api-response)
3. [认证机制](#api-authn)
4. [权限矩阵](#api-rbac)
5. [接口清单总览](#api-index)
6. [认证模块 Auth](#api-auth)
7. [用户模块 Users](#api-users)
8. [组织模块 Organizations](#api-organizations)
9. [岗位模块 Positions](#api-positions)
10. [人才模块 Talents](#api-talents)
11. [简历模块 Resumes](#api-resumes)
12. [测评模块 Assessments](#api-assessments)
13. [培训模块 Trainings](#api-trainings)
14. [匹配模块 Matchings](#api-matchings)
15. [知识库模块 Knowledge](#api-knowledge)
16. [分析模块 Analytics](#api-analytics)
17. [智能体模块 Agents](#api-agents)
18. [文件模块 Files](#api-files)
19. [审计模块 Audit](#api-audit)
20. [系统模块 System](#api-system)
21. [附录：枚举字典 / 时间与分页](#api-appendix)

---

<a id="api-global"></a>

## 一、全局约定

| 项目 | 约定 |
| --- | --- |
| Base URL | 开发：`http://127.0.0.1:8000`；网关后：`https://{host}` |
| 接口前缀 | `/api/v1`（除 `/health`、`/health/ready`、`/` 探针外，全部在此前缀下） |
| 请求格式 | `Content-Type: application/json; charset=utf-8`；上传类为 `multipart/form-data` |
| 响应格式 | 统一 `application/json`，结构见第二节 |
| 字段命名 | 请求与响应均为 **snake_case**（`real_name`、`page_size`） |
| 时间格式 | 响应为 ISO8601（`2026-11-03T10:20:30`）；请求日期参数用 `YYYY-MM-DD` |
| ID 类型 | 整数（BIGINT），路径参数必须 > 0 |
| 分页 | `page`（从 1 开始）、`page_size`（默认 20，上限 200） |
| 排序 | `order_by`（字段白名单）、`order`（`asc` / `desc`，默认 `desc`） |
| 字符集 | UTF-8；数据库 `utf8mb4` |
| 幂等 | `GET/PUT/DELETE` 幂等；`POST` 创建类接口非幂等（简历上传按 `file_hash` 秒传） |
| 链路追踪 | 请求头可传 `X-Trace-Id`，响应头必回 `X-Trace-Id`、`X-Process-Time-Ms` |
| 限流 | 默认 300 次 / 60 秒 / IP，超限返回 `429`（`code=429`） |

---

<a id="api-response"></a>

## 二、统一响应与错误码

### 2.1 成功响应

```json
{
  "code": 200,
  "message": "success",
  "data": { "id": 1, "name": "张三" },
  "trace_id": "0a10f00426934e71"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 业务状态码，**与 HTTP 状态码同源**，200 表示成功 |
| `message` | string | 中文提示语，可直接展示 |
| `data` | object / array / null | 业务数据；分页时为 `{items,total,page,page_size,pages}` |
| `trace_id` | string | 链路 ID，报障时提供 |

### 2.2 失败响应

```json
{
  "code": 3002,
  "message": "登录名已被占用",
  "data": null,
  "trace_id": "f21be3d9efc84e38"
}
```

参数校验失败（422）时 `data` 携带字段级明细：

```json
{
  "code": 422,
  "message": "请求参数校验失败",
  "data": {
    "errors": [
      { "field": "password", "message": "密码必须同时包含字母和数字", "type": "value_error" }
    ]
  },
  "trace_id": "9dd1c3a2b7f04e11"
}
```

### 2.3 错误码总表

| code | HTTP | 异常类 | 含义 | 前端处理建议 |
| --- | --- | --- | --- | --- |
| 200 | 200 | — | 成功 | — |
| 1001 | 422 | `ParamError` | 业务层参数校验失败 | 提示 message |
| 1002 | 400 | `BusinessRuleError` | 违反业务规则 | 提示 message |
| 2001 | 401 | `AuthError` | 未登录 / 凭证无效 | 跳转登录 |
| 2002 | 401 | `TokenExpiredError` | 令牌过期 | 用 refresh_token 换新令牌 |
| 2003 | 401 | `CredentialsError` | 用户名或密码错误 | 提示重试 |
| 2004 | 403 | `PermissionDeniedError` | 权限不足 | 提示无权限 |
| 2005 | 401 | `AccountDisabledError` | 账号被禁用 | 联系管理员 |
| 2006 | 401 | `AccountLockedError` | 登录失败次数过多锁定 | 提示解锁时间 |
| 3001 | 404 | `NotFoundError` | 资源不存在 | 提示或返回列表 |
| 3002 | 409 | `DuplicateError` | 唯一键冲突（重名/重号） | 提示已存在 |
| 3003 | 409 | `DataConflictError` | 乐观锁冲突（并发修改） | 刷新后重试 |
| 422 | 422 | `RequestValidationError` | Pydantic 校验失败 | 高亮 `data.errors` |
| 429 | 429 | 限流中间件 | 请求过于频繁 | 退避重试 |
| 5000 | 500 | `SystemError` | 系统内部错误 | 提示稍后重试 |
| 5001 | 502 | `ExternalServiceError` | 外部服务失败（LLM/Dify/Milvus） | 提示稍后重试 |
| 5002 | 502 | `AIError` | AI 能力异常 | 提示重试或人工处理 |
| 5003 | 400 | `FileError` | 文件类型/大小/写入失败 | 提示重新上传 |
| 501 | 501 | 占位路由 | 模块尚未实现 | 灰置功能入口 |

---

<a id="api-authn"></a>

## 三、认证机制

### 3.1 令牌

- 采用 **JWT（HS256）**，双令牌：`access_token`（默认 1440 分钟）、`refresh_token`（默认 10080 分钟）；
- 请求头：`Authorization: Bearer <access_token>`；
- access_token 载荷包含 `sub`(用户 ID)、`username`、`roles`、`type=access`、`iat`、`exp`、`jti`；
- `roles` 变更是即时生效于后续**新签发**的令牌；如需强制失效，后续接入 Redis 黑名单（记录 `jti`）。

### 3.2 典型流程

```
POST /api/v1/auth/login            → access_token + refresh_token + user
GET  /api/v1/auth/me               → 角色 / 权限码（渲染菜单按钮）
业务接口                            → Authorization: Bearer <access_token>
access_token 过期（2001/2002）      → POST /api/v1/auth/refresh（用 refresh_token）
POST /api/v1/auth/logout           → 前端丢弃令牌（服务端无状态）
```

### 3.3 登录安全策略

| 策略 | 说明 |
| --- | --- |
| 密码存储 | bcrypt 哈希 + 随机盐，不可逆；任何响应都不返回 `password_hash` |
| 失败计数 | 连续失败 5 次（`LOGIN_MAX_FAIL_TIMES`）锁定 15 分钟（`LOGIN_LOCK_MINUTES`） |
| 错误提示 | 用户不存在与密码错误统一返回 `2003`，防止用户名枚举 |
| 记住我 | `remember_me=true` 时 access_token 有效期延长至 7 天 |
| 审计 | 登录成功/失败、改密、重置密码均写审计日志 |

---

<a id="api-rbac"></a>

## 四、权限矩阵（RBAC + 数据范围）

### 4.1 角色

| 角色 code | 名称 | 默认数据范围 |
| --- | --- | --- |
| `super_admin` | 超级管理员 | 全部（通配符 `*`，跳过所有权限校验） |
| `admin` | 系统管理员 | 全部 |
| `hr` | 人力资源 | 全部 |
| `manager` | 部门管理者 | 本部门及下级（`dept_sub`） |
| `employee` | 普通员工 | 仅本人（`self`） |

> `is_superuser=true` 的账号视同超级管理员。

### 4.2 权限码与接口对应

| 权限码 | 含义 | 涉及接口 |
| --- | --- | --- |
| `user:read` | 查看用户 | `GET /users`、`GET /users/{id}` |
| `user:create` | 创建用户 | `POST /users` |
| `user:update` | 修改用户 | `PUT /users/{id}`、`PATCH /users/{id}/status`、`PUT /users/{id}/roles`、`POST /users/{id}/reset-password` |
| `user:delete` | 删除用户 | `DELETE /users/{id}` |
| `organization:read` | 查看组织 | `GET /organizations/tree`、`GET /organizations`、`GET /organizations/{id}` |
| `organization:manage` | 维护组织 | `POST/PUT/DELETE /organizations*`、`PATCH /organizations/{id}/move` |
| `talent:read` | 查看人才 | `GET /talents*`、语义检索 |
| `talent:create` | 新建人才 | `POST /talents` |
| `talent:update` | 修改人才 | `PUT /talents/{id}`、画像刷新、档案合并 |
| `talent:delete` | 删除人才 | `DELETE /talents/{id}` |
| `position:read` | 查看岗位 | `GET /positions*` |
| `position:manage` | 维护岗位 | `POST/PUT/DELETE /positions*` |
| `resume:upload` | 上传简历 | `POST /resumes/upload` |
| `resume:parse` | 解析简历 | `POST /resumes/{id}/parse` |
| `assessment:read` | 查看测评 | `GET /assessments*` |
| `assessment:manage` | 管理测评 | `POST /assessments`、提交答案 |
| `training:read` | 查看培训 | `GET /trainings*` |
| `training:manage` | 管理培训 | `POST /trainings/*` |
| `matching:read` | 查看匹配 | `GET /matchings*` |
| `matching:run` | 执行匹配 | `POST /matchings/run` |
| `knowledge:read` | 知识库检索 | `POST /knowledge/retrieve`、`/knowledge/ask` |
| `knowledge:manage` | 知识库维护 | 上传/删除文档 |
| `analytics:read` | 数据查询 | `GET /analytics/*`、`POST /analytics/nl2sql` |
| `analytics:export` | 导出报告 | `POST /analytics/report` |
| `agent:use` | 使用 AI 助手 | `POST /agents/*` |

### 4.3 数据范围（行级权限）

| scope | 可见数据 | 落地方式 |
| --- | --- | --- |
| `all` | 全部 | 无附加条件 |
| `dept_sub` | 本部门及所有下级部门 | `organization_id IN (子树)`，子树由 `organizations.path LIKE` 求得 |
| `dept` | 本部门 | `organization_id = 我的部门` |
| `self` | 仅本人创建/本人数据 | `created_by = 我` 或 `talent_id = 我` |

---

<a id="api-index"></a>

## 五、接口清单总览

| 模块 | 前缀 | 状态 | 接口数 |
| --- | --- | --- | --- |
| 认证 | `/auth` | ✅ | 6 |
| 用户 | `/users` | ✅ | 8 |
| 组织 | `/organizations` | ✅ | 7 |
| 岗位 | `/positions` | 🚧 | 7 |
| 人才 | `/talents` | 🚧 | 9 |
| 简历 | `/resumes` | 🚧 | 6 |
| 测评 | `/assessments` | 🚧 | 6 |
| 培训 | `/trainings` | 🚧 | 9 |
| 匹配 | `/matchings` | 🚧 | 5 |
| 知识库 | `/knowledge` | 🚧 | 7 |
| 分析 | `/analytics` | 🚧 | 6 |
| 智能体 | `/agents` | 🚧 | 6 |
| 文件 | `/files` | 🚧 | 5 |
| 审计 | `/audit` | 🚧 | 3 |
| 系统 | `/system` + 探针 | ✅ | 4 |

---

<a id="api-auth"></a>

## 六、认证模块 Auth — ✅ 已实现

### 6.1 注册

`POST /api/v1/auth/register` ｜ 公开 ｜ 无需登录

**请求体**

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| username | string | 是 | 4-32 位，字母/数字/下划线，唯一 |
| password | string | 是 | 8-64 位，必须同时包含字母和数字 |
| real_name | string | 否 | ≤ 64 |
| mobile | string | 否 | `^1[3-9]\d{9}$`，唯一 |
| email | string | 否 | ≤ 128，唯一 |

```json
{ "username": "zhangsan", "password": "Passw0rd123", "real_name": "张三", "mobile": "13800000001" }
```

**响应** `200`（`data` 为 `UserResponse`，不含密码字段）

```json
{
  "code": 200,
  "message": "注册成功",
  "data": {
    "id": 12,
    "username": "zhangsan",
    "real_name": "张三",
    "mobile": "13800000001",
    "email": null,
    "avatar": null,
    "organization_id": null,
    "organization_name": null,
    "position_title": null,
    "is_active": true,
    "is_superuser": false,
    "roles": [{ "id": 5, "code": "employee", "name": "普通员工" }],
    "last_login_at": null,
    "created_at": "2026-11-03T10:20:30"
  },
  "trace_id": "a1b2c3d4e5f60718"
}
```

**错误**：`409 / code=3002`（登录名或手机号已占用）、`422`（格式不合规）

> 注册接口**不接受** `roles` / `is_superuser` 字段（`extra="forbid"` 会直接 422），防止越权。默认角色 `employee`。

### 6.2 登录

`POST /api/v1/auth/login` ｜ 公开

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| username | string | 是 | 登录名 |
| password | string | 是 | 密码 |
| remember_me | bool | 否 | 默认 false；true 时 access_token 有效期 7 天 |

**响应**

```json
{
  "code": 200,
  "message": "登录成功",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 86400,
    "user": {
      "id": 1,
      "username": "admin",
      "real_name": "系统管理员",
      "organization_id": null,
      "organization_name": null,
      "position_title": null,
      "is_superuser": true,
      "roles": ["super_admin", "admin"],
      "permissions": ["*"]
    }
  },
  "trace_id": "75d180fa97bd4d03"
}
```

**错误**：`401 / 2003` 用户名或密码错误；`401 / 2005` 账号禁用；`401 / 2006` 账号锁定

### 6.3 刷新令牌

`POST /api/v1/auth/refresh` ｜ 公开

```json
{ "refresh_token": "eyJhbGciOiJIUzI1NiIs..." }
```

响应 `200`：`data = {access_token, refresh_token, token_type, expires_in}`。
错误：`401 / 2001` 令牌无效、`401 / 2002` 已过期、`401 / 2005` 账号被禁用。

### 6.4 当前用户

`GET /api/v1/auth/me` ｜ 需登录

响应 `data`（`LoginUserInfo`）：

```json
{
  "id": 1,
  "username": "admin",
  "real_name": "系统管理员",
  "avatar": null,
  "organization_id": 3,
  "organization_name": "技术研发部",
  "position_title": "研发经理",
  "is_superuser": true,
  "roles": ["super_admin", "admin"],
  "permissions": ["*"]
}
```

> 前端据此渲染菜单与按钮：`permissions` 含 `*` 表示拥有全部权限。

### 6.5 修改密码

`POST /api/v1/auth/change-password` ｜ 需登录

```json
{ "old_password": "Passw0rd123", "new_password": "NewPassw0rd456", "confirm_password": "NewPassw0rd456" }
```

响应：`{ "code": 200, "message": "密码修改成功", "data": null }`
错误：`422 / 1001` 两次密码不一致；`400 / 1002` 原密码不正确 / 新旧密码相同。

### 6.6 退出登录

`POST /api/v1/auth/logout` ｜ 需登录

响应 `{ "code": 200, "message": "已退出登录", "data": null }`。JWT 无状态，服务端不强制失效。

---

<a id="api-users"></a>

## 七、用户模块 Users — ✅ 已实现

> 全部接口需登录；写操作需对应 `user:*` 权限。响应 `data` 为 `UserResponse`（永不含 `password_hash`）。

### 7.1 用户列表（分页）

`GET /api/v1/users` ｜ 权限 `user:read`

| 查询参数 | 类型 | 说明 |
| --- | --- | --- |
| page | int | 默认 1 |
| page_size | int | 默认 20，最大 200 |
| keyword | string | 用户名/姓名/手机号/邮箱 模糊搜索 |
| organization_id | int | 按部门筛选 |
| is_active | bool | 按启用状态筛选 |
| order_by | string | `id` / `created_at` / `updated_at` / `last_login_at` / `username` |
| order | string | `asc` / `desc` |

```json
{
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [
      {
        "id": 4,
        "username": "emp01",
        "real_name": "普通员工",
        "mobile": null,
        "email": null,
        "avatar": null,
        "organization_id": 5,
        "organization_name": "后端组",
        "position_title": null,
        "is_active": true,
        "is_superuser": false,
        "roles": [{ "id": 5, "code": "employee", "name": "普通员工" }],
        "last_login_at": null,
        "created_at": "2026-11-03T05:56:37"
      }
    ],
    "total": 4,
    "page": 1,
    "page_size": 20,
    "pages": 1
  },
  "trace_id": "3f7a9c1d2e4b5067"
}
```

### 7.2 用户详情

`GET /api/v1/users/{user_id}` ｜ 权限 `user:read` → `data` 为 `UserResponse`。
错误：`404 / 3001`。

### 7.3 创建用户

`POST /api/v1/users` ｜ 权限 `user:create`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| username | string | 是 | 4-32 位字母数字下划线 |
| password | string | 是 | 8-64 位，含字母与数字 |
| real_name | string | 否 | ≤ 64 |
| mobile | string | 否 | 11 位手机号，唯一 |
| email | string | 否 | ≤ 128，唯一 |
| organization_id | int | 否 | 必须存在 |
| position_title | string | 否 | ≤ 64 |
| is_active | bool | 否 | 默认 true |
| role_codes | string[] | 否 | 默认 `["employee"]`，角色必须存在 |

```json
{
  "username": "hr02",
  "password": "Hr1234567",
  "real_name": "人事专员",
  "organization_id": 2,
  "role_codes": ["hr"]
}
```

响应 `200`，`message="创建成功"`。错误：`409/3002` 重名或手机号重复、`404/3001` 部门/角色不存在。

### 7.4 更新用户

`PUT /api/v1/users/{user_id}` ｜ 权限 `user:update`

请求体（全部可选，仅传需要变更的字段）：`real_name`、`mobile`、`email`、`avatar`、`organization_id`、`position_title`。

```json
{ "real_name": "张三（HR）", "organization_id": 3, "position_title": "招聘主管" }
```

响应 `data` 为更新后的 `UserResponse`。错误：`409/3002` 手机号被占用、`404/3001` 部门不存在。

### 7.5 启用 / 禁用

`PATCH /api/v1/users/{user_id}/status` ｜ 权限 `user:update`

```json
{ "is_active": false, "reason": "离职" }
```

响应 `message="状态已更新"`。规则：不能修改自己（`400/1002`）、不能禁用超级管理员（`403/2004`）。

### 7.6 分配角色

`PUT /api/v1/users/{user_id}/roles` ｜ 权限 `user:update`

```json
{ "role_codes": ["hr", "manager"] }
```

覆盖式赋值（先清空再绑定）。错误：`404/3001` 角色不存在。

### 7.7 重置密码（管理员）

`POST /api/v1/users/{user_id}/reset-password` ｜ 权限 `user:update`

```json
{ "new_password": "Reset123456" }
```

响应 `message="密码已重置"`；同时清零失败计数、解除锁定。

### 7.8 删除用户

`DELETE /api/v1/users/{user_id}` ｜ 权限 `user:delete`

逻辑删除：用户名改写为 `原用户名#deleted#<id>` 以释放唯一键，清空手机号/邮箱与角色绑定。
规则：不能删自己（`400/1002`）、不能删超级管理员（`403/2004`）。响应 `message="删除成功"`。

---

<a id="api-organizations"></a>

## 八、组织模块 Organizations — ✅ 已实现

> 部门树使用 `path` 前缀（`/1/3/`）维护层级，移动部门会级联修正子孙的 `path` 与 `level`。

### 8.1 组织树

`GET /api/v1/organizations/tree` ｜ 权限 `organization:read`

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": 1,
      "parent_id": null,
      "code": "HQ",
      "name": "总公司",
      "leader_id": null,
      "sort": 1,
      "is_active": true,
      "user_count": 0,
      "children": [
        {
          "id": 3,
          "parent_id": 1,
          "code": "RD",
          "name": "技术研发部",
          "leader_id": null,
          "sort": 2,
          "is_active": true,
          "user_count": 1,
          "children": [
            { "id": 4, "parent_id": 3, "code": "RD_ALGO", "name": "算法组", "sort": 1, "is_active": true, "user_count": 0, "children": [] }
          ]
        }
      ]
    }
  ],
  "trace_id": "8c1f2a3b4d5e6f70"
}
```

排序规则：同级按 `sort` 升序，再按 `id` 升序。

### 8.2 部门列表（分页）

`GET /api/v1/organizations?page=1&page_size=20&keyword=研发` ｜ 权限 `organization:read`

`data` 为分页结构，`items` 元素为 `OrgResponse`：`id/parent_id/code/name/level/path/leader_id/leader_name/sort/is_active/remark/user_count/created_at`。

### 8.3 部门详情

`GET /api/v1/organizations/{org_id}` ｜ 权限 `organization:read` → `data` 为 `OrgResponse`（含 `user_count`）。

### 8.4 创建部门

`POST /api/v1/organizations` ｜ 权限 `organization:manage`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| parent_id | int | 否 | 为空表示根部门；必须存在 |
| code | string | 是 | 2-64 位，全局唯一 |
| name | string | 是 | 2-128 位，同一父级下唯一 |
| leader_id | int | 否 | 负责人用户 ID |
| sort | int | 否 | ≥ 0，默认 0 |
| is_active | bool | 否 | 默认 true |
| remark | string | 否 | ≤ 255 |

```json
{ "parent_id": 1, "code": "RD", "name": "技术研发部", "sort": 2 }
```

响应 `data`（节选）：`level=2`、`path="/1/"`（`path` 为**父级**路径前缀）。
错误：`404/3001` 上级不存在、`409/3002` 同级重名或编码重复。

### 8.5 更新部门

`PUT /api/v1/organizations/{org_id}` ｜ 权限 `organization:manage`

可更新：`name`、`leader_id`、`sort`、`is_active`、`remark`（`code` 与 `parent_id` 不可通过本接口修改）。
错误：`409/3002` 同级重名。

### 8.6 移动部门

`PATCH /api/v1/organizations/{org_id}/move` ｜ 权限 `organization:manage`

```json
{ "new_parent_id": 2 }
```

规则：不能移动到自身或自己的子孙下（`400/1002`）；`new_parent_id=null` 表示提升为根部门。
副作用：重算自身 `level/path`，并批量修正所有子孙的 `path` 与 `level`。

### 8.7 删除部门

`DELETE /api/v1/organizations/{org_id}` ｜ 权限 `organization:manage`

规则：存在子部门 → `400/1002`「请先删除或移走该部门下的子部门」；存在在职用户 → `400/1002`。
成功后为逻辑删除。响应 `message="删除成功"`。

---

<a id="api-positions"></a>

## 九、岗位模块 Positions — 🚧 待实现

> 负责人：岗位与人才组 ｜ 代码位置：`routers/position_router.py`、`services/position_service.py`、`models/position.py`
> 当前访问返回 `501`，契约如下（前端可按此先联调 mock）。

### 数据对象 `PositionResponse`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int | 主键 |
| title | string | 岗位名称 |
| code | string | 岗位编码（唯一） |
| organization_id | int | 所属部门 |
| organization_name | string | 部门名称（服务端补充） |
| level | string | `junior` / `middle` / `senior` / `expert` |
| headcount | int | 编制人数 |
| headcount_filled | int | 已到位人数（服务端统计） |
| status | string | `open` / `paused` / `closed` |
| description | string | 岗位说明书全文 |
| requirements | object | 结构化要求，见下 |
| skill_tags | string[] | 技能标签 |
| salary_min / salary_max | decimal | 薪资范围（元/月） |
| publish_at / close_at | datetime | 发布 / 关闭时间 |
| created_at | datetime | 创建时间 |

`requirements` 结构（用于硬性过滤与 AI 匹配）：

```json
{
  "education": "本科",
  "experience_years": 3,
  "skills": ["Python", "FastAPI", "SQL"],
  "certificates": ["PMP"],
  "soft_skills": ["沟通", "跨部门协作"],
  "weights": { "skill": 0.4, "experience": 0.3, "education": 0.2, "quality": 0.1 }
}
```

### 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/positions` | `position:manage` | 创建岗位 |
| GET | `/positions` | `position:read` | 分页列表（`keyword` / `organization_id` / `level` / `status` / 排序） |
| GET | `/positions/{id}` | `position:read` | 详情（含 `requirements`、`headcount_filled`） |
| PUT | `/positions/{id}` | `position:manage` | 更新（`code` 不可改） |
| PATCH | `/positions/{id}/status` | `position:manage` | 上架 / 暂停 / 关闭 |
| DELETE | `/positions/{id}` | `position:manage` | 逻辑删除（有进行中的匹配任务时禁止） |
| POST | `/positions/{id}/parse` | `position:manage` | 上传岗位说明书（JD）→ AI 解析出 `requirements`，异步返回任务号 |

**创建请求示例**

```json
{
  "title": "高级后端工程师",
  "code": "POS_BACKEND_SENIOR_01",
  "organization_id": 3,
  "level": "senior",
  "headcount": 2,
  "description": "负责人才平台后端服务设计与开发……",
  "requirements": {
    "education": "本科",
    "experience_years": 5,
    "skills": ["Python", "FastAPI", "MySQL", "Milvus"],
    "certificates": [],
    "soft_skills": ["owner 意识"]
  },
  "skill_tags": ["Python", "FastAPI", "微服务"],
  "salary_min": 25000,
  "salary_max": 40000
}
```

**错误约定**：`409/3002` 岗位编码重复；`404/3001` 部门不存在；`400/1002` `closed` 岗位不可直接改回 `open`（需重新发布）。

**状态机**：`open → paused ⇄ open`，`open/paused → closed`（单向）。关闭后不再参与匹配，历史匹配记录保留。

---

<a id="api-talents"></a>

## 十、人才模块 Talents — 🚧 待实现

> 负责人：岗位与人才组 ｜ 代码位置：`routers/talent_router.py`、`models/talent.py`

### 数据对象 `TalentResponse`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int | 主键 |
| name | string | 姓名 |
| gender | string | `male` / `female` / `unknown` |
| birth_date | date | 出生日期（响应可同时返回 `age`） |
| mobile | string | 手机号（非管理员查看时脱敏 `138****0001`） |
| email | string | 邮箱 |
| education | string | `高中及以下` / `大专` / `本科` / `硕士` / `博士` |
| school / major | string | 毕业院校 / 专业 |
| work_years | int | 工作年限 |
| current_company / current_position | string | 现公司 / 现职位 |
| organization_id | int | 归属部门 |
| skill_tags | string[] | 技能标签（JSON 列） |
| certificates | string[] | 证书 |
| self_evaluation | string | 自我评价 |
| status | string | `active`（在职）/ `left`（离职）/ `reserve`（储备） |
| potential_level | string | `A` / `B` / `C` / `D`（画像生成） |
| portrait_summary | string | 画像摘要 |
| source | string | `manual` / `ai_resume` |
| resume_id | int | 来源简历 ID |
| created_at | datetime | 创建时间 |

### 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/talents` | `talent:create` | 创建人才档案（手机号查重，成功后异步生成画像） |
| GET | `/talents` | `talent:read` | 分页列表（`keyword` / `education` / `organization_id` / `status` / `skill` / `work_years_min` / `work_years_max`） |
| GET | `/talents/{id}` | `talent:read` | 详情（含简历、测评、匹配摘要） |
| GET | `/talents/{id}/portrait` | `talent:read` | 人才画像（维度分 + 标签 + 优劣 + 建议岗位） |
| PUT | `/talents/{id}` | `talent:update` | 更新档案 |
| DELETE | `/talents/{id}` | `talent:delete` | 逻辑删除 |
| POST | `/talents/search/semantic` | `talent:read` | 自然语言语义检索（向量召回，≤500ms） |
| POST | `/talents/{id}/portrait/refresh` | `talent:update` | 触发画像重算（异步） |
| POST | `/talents/merge` | `talent:update` | 合并重复档案（迁移简历/测评/匹配记录） |

**创建请求示例**

```json
{
  "name": "张三",
  "gender": "male",
  "birth_date": "1995-06-18",
  "mobile": "13800000001",
  "email": "zhangsan@example.com",
  "education": "本科",
  "school": "某某大学",
  "major": "计算机科学与技术",
  "work_years": 5,
  "current_company": "某科技有限公司",
  "current_position": "后端工程师",
  "organization_id": 3,
  "skill_tags": ["Python", "FastAPI", "MySQL", "Milvus"],
  "certificates": ["软件设计师"],
  "status": "active"
}
```

**画像响应示例（`portrait`）**

```json
{
  "talent_id": 1,
  "version": 3,
  "dimensions": { "skill": 82, "experience": 75, "quality": 80, "potential": 78 },
  "labels": ["后端", "AI 应用", "高潜", "稳定"],
  "strengths": ["微服务架构经验丰富", "有 RAG 落地经验"],
  "weaknesses": ["缺少团队管理经验"],
  "suggested_positions": ["高级后端工程师", "技术负责人"],
  "potential_level": "A",
  "model_name": "qwen-max",
  "generated_at": "2026-11-03T10:30:00"
}
```

**语义检索请求 / 响应**

**请求**

```json
{ "query": "擅长数字化转型、有 3 年以上 AI 项目经验的骨干", "top_k": 10, "filters": { "education": "本科", "status": "active" } }
```

**响应**

```json
{ "items": [{ "talent_id": 12, "name": "李四", "score": 0.91, "highlights": ["3 年 RAG 项目经验"] }], "total": 1, "elapsed_ms": 186 }
```

**错误约定**：`409/3002` 手机号已存在；`400/1002` 合并时源档案与目标档案相同；`5002` 画像生成失败（可重试）。

---

<a id="api-resumes"></a>

## 十一、简历模块 Resumes — 🚧 待实现

### 数据对象 `ResumeResponse`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int | 主键 |
| talent_id | int | 绑定人才（解析后可空） |
| file_id | int | 关联文件（`files.id`） |
| file_name / file_type / file_size | string / string / int | 文件元信息 |
| file_hash | string | sha256（秒传 / 查重） |
| status | string | `uploaded` / `parsing` / `success` / `failed` |
| accuracy | float | 解析置信度 0-1 |
| parsed_data | object | 结构化简历（见下） |
| parse_error | string | 失败原因 |
| parsed_at | datetime | 解析完成时间 |

`parsed_data` 结构：

```json
{
  "basic_info": { "name": "张三", "mobile": "13800000001", "email": "z@example.com", "birth_date": "1995-06-18" },
  "education": [{ "school": "某某大学", "major": "计算机", "degree": "本科", "start": "2013-09", "end": "2017-06" }],
  "work_experience": [{ "company": "某科技", "title": "后端工程师", "start": "2020-03", "end": "至今", "description": "…" }],
  "projects": [{ "name": "人才平台", "role": "后端负责人", "description": "…", "tech": ["FastAPI"] }],
  "skills": ["Python", "FastAPI", "Milvus"],
  "work_years": 5
}
```

### 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/resumes/upload` | `resume:upload` | `multipart/form-data` 上传（`file` 字段），校验扩展名与 ≤20MB，按 `file_hash` 秒传 |
| POST | `/resumes/{id}/parse` | `resume:parse` | 触发 AI 解析（异步），body：`{force: bool}` |
| GET | `/resumes` | `talent:read` | 分页列表（`status` / `talent_id` / `keyword`） |
| GET | `/resumes/{id}` | `talent:read` | 简历详情（元信息 + 状态） |
| GET | `/resumes/{id}/result` | `talent:read` | 解析结果（`parsed_data` + `warnings`） |
| POST | `/resumes/{id}/bind` | `talent:update` | 绑定到人才档案：`{talent_id}` 或 `{create_talent: true}` |

**上传响应**

```json
{ "code": 200, "message": "上传成功", "data": { "resume_id": 8, "file_id": 21, "file_name": "张三-简历.pdf", "file_size": 348212, "status": "uploaded", "duplicated": false } }
```

**解析结果响应（`status=success`）**

```json
{
  "resume_id": 8,
  "status": "success",
  "parsed": { "basic_info": { "...": "..." }, "education": [], "work_experience": [], "skills": [] },
  "accuracy": 0.96,
  "warnings": ["工作经历第 2 段缺少结束时间，已按「至今」处理"],
  "parsed_at": "2026-11-03T10:31:12"
}
```

**错误约定**：`5003` 文件类型不支持 / 超过 20MB；`400/1002` 简历已在解析中；`5002` AI 解析失败（保留 `parse_error`，可 `force=true` 重试）。

> 解析为异步流程：接口立即返回，前端轮询 `GET /resumes/{id}` 的 `status`，或订阅 `GET /agents/conversations/{id}` 的 SSE 进度。

---

<a id="api-assessments"></a>

## 十二、测评模块 Assessments — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/assessments` | `assessment:manage` | 创建测评任务（按岗位/层级抽题，千人千卷） |
| GET | `/assessments` | `assessment:read` | 分页列表（`talent_id` / `status` / 日期区间） |
| POST | `/assessments/{id}/start` | `assessment:read` | 开始测评，返回题目（**不含标准答案**） |
| POST | `/assessments/{id}/submit` | `assessment:read` | 提交答案，同步规则评分 + 异步 AI 研判 |
| GET | `/assessments/{id}/result` | `assessment:read` | 结果（总分、维度分、等级、AI 报告） |

**创建请求**

```json
{ "title": "2026 年技术序列能力测评", "talent_id": 12, "position_id": 3, "question_count": 20, "deadline": "2026-11-10 18:00:00" }
```

**开始测评响应**

```json
{
  "assessment_id": 5,
  "expire_at": "2026-11-03T11:30:00",
  "questions": [
    { "id": 101, "type": "single", "content": "以下哪种索引最适合……", "options": [{"key": "A", "text": "…"}], "score": 5, "dimension": "数据库" }
  ]
}
```

**提交答案**

```json
{ "answers": [{ "question_id": 101, "answer": "B" }, { "question_id": 102, "answer": "这里回答……" }] }
```

**结果响应**

```json
{
  "assessment_id": 5,
  "total_score": 86.5,
  "dimension_scores": { "专业知识": 90, "工程能力": 85, "沟通协作": 80 },
  "level": "高级",
  "suggestions": ["加强分布式系统设计能力", "建议参加架构培训"],
  "report": { "summary": "……", "highlights": [], "gaps": [] },
  "scored_at": "2026-11-03T11:10:00"
}
```

**状态机**：`pending → in_progress → submitted → scored`；超时提交仍保存但标记 `overtime`。
**错误约定**：`400/1002` 测评已结束 / 不在进行中；`404/3001`。

---

<a id="api-trainings"></a>

## 十三、培训模块 Trainings — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/trainings/courses` | `training:manage` | 创建课程 |
| GET | `/trainings/courses` | `training:read` | 课程列表（`category` / `level` / `keyword`） |
| POST | `/trainings/plans` | `training:manage` | 创建培训计划（可指定 `created_by_ai`） |
| GET | `/trainings/plans` | `training:read` | 计划列表（含 `completion_rate`） |
| GET | `/trainings/plans/{id}` | `training:read` | 计划详情（课程 + 学习进度） |
| POST | `/trainings/records` | `training:read` | 上报学习进度（单调递增） |
| GET | `/trainings/records` | `training:read` | 学习记录分页（`talent_id` / `plan_id` / `status`） |
| POST | `/trainings/recommend` | `training:read` | 根据能力缺口推荐课程（AI） |
| GET | `/trainings/warnings` | `training:read` | 学习滞后预警列表 |

**学习进度上报**

```json
{ "plan_id": 9, "course_id": 31, "talent_id": 12, "progress": 60, "study_minutes": 45 }
```

**推荐请求 / 响应**

**请求**

```json
{ "talent_id": 12, "position_id": 3, "top_k": 5 }
```

**响应**

```json
{
  "gap_dimensions": ["分布式系统", "团队管理"],
  "recommendations": [
    { "course_id": 31, "title": "分布式系统设计实战", "duration_hours": 12, "reason": "补齐「分布式系统」能力缺口", "gap_dimension": "分布式系统" }
  ]
}
```

**错误约定**：`400/1002` 进度回退（不允许从 60 改为 30）；`400/1002` 学习时长与进度比例异常（标记 `suspicious`）。

---

<a id="api-matchings"></a>

## 十四、匹配模块 Matchings — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/matchings/run` | `matching:run` | 执行匹配（同步返回 `batch_id` 与首批结果，或返回 `batch_id` 由前端轮询） |
| GET | `/matchings/positions/{id}` | `matching:read` | 某岗位最新批次结果（按分数倒序） |
| GET | `/matchings/talents/{id}` | `matching:read` | 某人才历史匹配记录 |
| GET | `/matchings/batches/{batch_id}` | `matching:read` | 按批次查询结果 |
| POST | `/matchings/explain` | `matching:read` | 匹配解释（AI 生成，必须引用真实字段） |

**执行匹配请求**

```json
{ "position_id": 3, "strategy": "hybrid", "top_k": 20, "min_score": 60, "talent_ids": [] }
```

`strategy`：`hard`（仅硬性过滤）/ `soft`（仅向量软匹配）/ `hybrid`（硬性过滤 + 加权，默认）。

**响应**

```json
{
  "batch_id": "b7f3c1d2e8a94f60",
  "position_id": 3,
  "position_title": "高级后端工程师",
  "matched_at": "2026-11-03T10:40:00",
  "items": [
    {
      "talent_id": 12,
      "talent_name": "张三",
      "score": 88.5,
      "dimension_scores": { "skill": 92, "experience": 85, "education": 90, "quality": 80 },
      "highlights": ["5 年后端经验，技术栈完全匹配", "有 Milvus 向量检索落地经验"],
      "gaps": ["缺少团队管理经验"],
      "suggestion": "建议进入技术面，重点考察架构设计能力"
    }
  ]
}
```

**匹配解释响应**

```json
{
  "position_id": 3,
  "talent_id": 12,
  "dimensions": [{ "name": "skill", "score": 92, "weight": 0.4, "evidence": "技能标签命中 6/7" }],
  "explanation": "候选人与岗位技术栈高度重合……"
}
```

**错误约定**：`404/3001` 岗位不存在；`400/1002` 岗位已关闭；`400/1001` `strategy` 非法；`5002` AI 解释失败（降级为规则化说明）。

---

<a id="api-knowledge"></a>

## 十五、知识库模块 Knowledge — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/knowledge/docs` | `knowledge:manage` | 新建文本型知识（`title` / `category` / `content` / 权限范围） |
| POST | `/knowledge/docs/upload` | `knowledge:manage` | 上传文档（PDF/Word/Excel/TXT/图片），异步索引 |
| GET | `/knowledge/docs` | `knowledge:read` | 文档列表（`status` / `category` / `keyword`） |
| DELETE | `/knowledge/docs/{id}` | `knowledge:manage` | 删除文档（同步删除 Milvus 向量） |
| POST | `/knowledge/retrieve` | `knowledge:read` | 向量检索（返回 chunk + 得分） |
| POST | `/knowledge/ask` | `knowledge:read` | RAG 问答（答案 + 引用来源） |
| GET | `/knowledge/retrieve-eval` | `knowledge:manage` | 检索效果自检（准确率 / 召回率） |

**文档对象**：`id`、`title`、`category`、`source_type`（`file`/`text`）、`status`（`indexing`/`indexed`/`failed`）、`chunk_count`、`permission_scope`（`public`/`dept`/`role`）、`allowed_roles`、`organization_id`、`embedding_model`、`index_error`、`indexed_at`。

**检索请求 / 响应**

**请求**

```json
{ "question": "人才测评结果的有效期是多久？", "top_k": 5, "score_threshold": 0.35, "use_rerank": true }
```

**响应**

```json
{
  "chunks": [
    { "doc_id": 3, "doc_title": "人才测评管理制度", "content": "测评结果有效期为 12 个月……", "score": 0.86, "page": 4 }
  ],
  "elapsed_ms": 132
}
```

**问答请求 / 响应**

**请求**

```json
{ "question": "晋级到高级工程师需要满足哪些条件？", "conversation_id": null }
```

**响应**

```json
{
  "answer": "根据《职级晋升管理办法》第 3 条，需满足……",
  "citations": [
    { "doc_id": 3, "doc_title": "职级晋升管理办法", "chunk": "第 3 条……", "page": 2, "score": 0.88 }
  ],
  "conversation_id": "c-20261103-0001",
  "prompt_tokens": 812,
  "completion_tokens": 236
}
```

> **无幻觉约束**：检索结果为空时，`answer` 固定返回「知识库中未找到依据」，且 `citations=[]`。

**错误约定**：`5003` 文件类型不支持；`5002` 索引/问答失败；`400/1002` 文档正在索引中不可删除。

---

<a id="api-analytics"></a>

## 十六、分析模块 Analytics — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/analytics/overview` | `analytics:read` | 首页概览（人才总数、在招岗位、匹配率、培训完成率、平均测评分） |
| GET | `/analytics/distribution` | `analytics:read` | 分布统计（`type=education/age/skill/org`） |
| GET | `/analytics/trend` | `analytics:read` | 趋势（`metric=talent_new/turnover/match`，`granularity=month`） |
| POST | `/analytics/nl2sql` | `analytics:read` | 自然语言问数（Vanna → SQL → 数据 + 图表 + 解读） |
| POST | `/analytics/report` | `analytics:export` | 生成报告（`format=pdf/docx`），返回下载地址 |
| GET | `/analytics/reports` | `analytics:read` | 报告任务列表与状态 |

**概览响应**

```json
{
  "total_talents": 1280,
  "active_positions": 86,
  "match_rate": 0.62,
  "training_completion": 0.78,
  "assessment_avg_score": 81.4,
  "monthly_trend": [{ "month": "2026-09", "value": 42 }, { "month": "2026-10", "value": 51 }]
}
```

**NL2SQL 请求 / 响应**

**请求**

```json
{ "question": "统计本年度本科及以上青年人才占比、各部门技能人才分布", "max_rows": 200 }
```

**响应**

```json
{
  "question": "统计本年度本科及以上青年人才占比、各部门技能人才分布",
  "sql": "SELECT o.name, COUNT(*) AS cnt FROM talents t JOIN organizations o ON ... LIMIT 200",
  "columns": ["department", "count"],
  "rows": [["技术研发部", 120], ["市场运营部", 45]],
  "chart": { "type": "bar", "x_field": "department", "y_field": "count", "title": "各部门技能人才分布" },
  "insight": "技术研发部人才占比 62%，但青年人才比例仅 28%，存在梯队断层风险……",
  "elapsed_ms": 1420
}
```

> **安全约束**：生成的 SQL 必须经过 `sql_validator`（只允许单条 SELECT、表白名单、强制 `LIMIT`、禁止系统表），
> 并追加当前用户的数据范围条件（行级权限）；执行走 PgSQL 只读连接。

---

<a id="api-agents"></a>

## 十七、智能体模块 Agents — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/agents/chat` | `agent:use` | 对话（非流式，返回完整答案 + citations） |
| POST | `/agents/chat/stream` | `agent:use` | 对话（SSE 流式，`text/event-stream`） |
| GET | `/agents/conversations` | `agent:use` | 我的会话列表 |
| GET | `/agents/conversations/{id}/messages` | `agent:use` | 会话消息历史（分页） |
| POST | `/agents/tasks` | `agent:use` | 执行指定 Agent 任务（`resume`/`talent`/`matching`/`assessment`/`training`） |
| GET | `/agents/tasks/{task_id}` | `agent:use` | 任务状态与结果 |

**对话请求**

```json
{ "agent_type": "assistant", "conversation_id": "c-20261103-0001", "message": "帮我分析张三名下的人才画像", "stream": false }
```

**对话响应**

```json
{
  "conversation_id": "c-20261103-0001",
  "message_id": 88,
  "answer": "张三的画像显示……",
  "citations": [{ "type": "talent", "talent_id": 12, "field": "portrait.dimensions" }],
  "usage": { "prompt_tokens": 620, "completion_tokens": 210, "model": "qwen-plus" },
  "elapsed_ms": 1830
}
```

**SSE 事件约定**（`POST /agents/chat/stream`）

```
event: start   data: {"conversation_id":"c-...","message_id":88}
event: delta   data: {"text":"张三的"}          // 逐 token / 逐句
event: citation data: {"type":"talent","talent_id":12}
event: done    data: {"usage":{...},"elapsed_ms":1830}
event: error   data: {"code":5002,"message":"模型调用失败"}
```

**任务请求**

```json
{ "agent_type": "resume", "payload": { "resume_id": 8 }, "async": true }
```

**错误约定**：`5002` AI 调用失败；`403/2004` 越权访问他人会话；`429` 超出 token 配额（响应 `code=5002`，`message` 说明配额）。

---

<a id="api-files"></a>

## 十八、文件模块 Files — 🚧 待实现

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/files/upload` | 登录即可 | `multipart/form-data`，字段 `file` + `biz_type`（`resume`/`knowledge`/`avatar`/`export`）；返回文件元数据 |
| GET | `/files/{id}` | 登录即可 | 文件元数据（含下载地址） |
| GET | `/files/{id}/download` | 登录即可 | 下载（`FileResponse`，校验 `biz_type` 与归属人） |
| DELETE | `/files/{id}` | 登录即可 | 删除（软删元数据 + 延迟清理磁盘） |
| POST | `/files/batch-upload` | 登录即可 | 批量上传，返回成功/失败明细 |

**上传响应**

```json
{
  "id": 21,
  "file_name": "张三-简历.pdf",
  "stored_name": "9f2c1a7b8d3e4f50a1b2c3d4e5f60718.pdf",
  "file_size": 348212,
  "content_type": "application/pdf",
  "extension": ".pdf",
  "file_hash": "b1946ac92492d2347c6235b4d2611184…",
  "storage_type": "local",
  "biz_type": "resume",
  "download_url": "/api/v1/files/21/download",
  "duplicated": false
}
```

**错误约定**：`5003` 类型不支持 / 超过 `MAX_UPLOAD_SIZE_MB`；`403/2004` 无权限下载他人文件；`404/3001`。

---

<a id="api-audit"></a>

## 十九、审计模块 Audit — 🚧 待实现

> 审计日志**只增不改**，不提供写接口。仅管理员可查询。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/audit/logs` | `user:read`（管理员） | 分页查询（`user_id` / `module` / `action` / `status` / 时间区间 / `trace_id`） |
| GET | `/audit/logs/{id}` | `user:read` | 单条详情（含 `before_data` / `after_data` 差异） |
| GET | `/audit/stats` | `user:read` | 动作分布与失败率统计 |

**日志对象**：`id`、`user_id`、`username`、`action`（`create`/`update`/`delete`/`login`/`export`/`ai_call`）、`module`、`target_type`、`target_id`、`request_method`、`request_path`、`request_ip`、`trace_id`、`before_data`、`after_data`、`status`（`success`/`fail`）、`error_message`、`created_at`。

---

<a id="api-system"></a>

## 二十、系统模块 System — ✅ 已实现

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 服务信息（名称、版本、环境、文档地址、接口前缀），`include_in_schema=false` |
| GET | `/health` | 存活探针：永远 `200`，附数据库连接摘要 |
| GET | `/health/ready` | 就绪探针：MySQL 不可用 → `503` + 原因；PgSQL 不可用 → `200` 且 `status=degraded` |
| GET | `/api/v1/system/info` | 框架自检：数据库摘要、`connection_status`、`orm_tables`、已实现模块 |

**`/health` 响应**

```json
{
  "code": 200,
  "message": "OK",
  "data": {
    "status": "up",
    "version": "1.0.0",
    "env": "dev",
    "databases": { "mysql": "127.0.0.1:3306/ai_talent_platform", "pgsql": "127.0.0.1:5432/ai_talent_analytics", "redis": "disabled", "milvus": "disabled" }
  },
  "trace_id": "ea56269afbd44191"
}
```

**`/health/ready` 响应（MySQL 不可用，HTTP 503）**

```json
{
  "code": 503,
  "message": "MySQL 不可用：无法连接 MySQL 127.0.0.1:3306，请确认 MySQL 服务已启动、端口正确（.env 中 DB_HOST / DB_PORT）",
  "data": {
    "status": "down",
    "mysql": { "target": "127.0.0.1:3306/ai_talent_platform", "status": "down", "reason": "connection_refused", "message": "…", "elapsed_ms": 0.4 },
    "pgsql": { "target": "127.0.0.1:5432/ai_talent_analytics", "status": "down", "reason": "connection_refused", "message": "…", "elapsed_ms": 0.2 },
    "redis": "disabled",
    "milvus": "disabled",
    "drivers": { "mysql": "mysql+aiomysql", "pgsql": "postgresql+asyncpg" }
  }
}
```

`reason` 取值：`connection_refused` / `auth_failed` / `database_missing` / `driver_missing` / `timeout` / `unknown` / `ok`。

---

<a id="api-appendix"></a>

## 二十一、附录

### 21.1 业务枚举字典

| 枚举 | 取值 |
| --- | --- |
| 角色 role | `super_admin` / `admin` / `hr` / `manager` / `employee` |
| 用户状态 | `is_active` true / false |
| 部门状态 | `is_active` true / false |
| 学历 education | `高中及以下` / `大专` / `本科` / `硕士` / `博士` |
| 岗位等级 level | `junior` / `middle` / `senior` / `expert` |
| 岗位状态 status | `open` / `paused` / `closed` |
| 人才状态 status | `active` / `left` / `reserve` |
| 性别 gender | `male` / `female` / `unknown` |
| 简历状态 | `uploaded` / `parsing` / `success` / `failed` |
| 测评状态 | `pending` / `in_progress` / `submitted` / `scored` |
| 题型 type | `single` / `multiple` / `text` |
| 培训计划状态 | `draft` / `ongoing` / `finished` / `cancelled` |
| 学习记录状态 | `not_started` / `learning` / `completed` |
| 匹配策略 strategy | `hard` / `soft` / `hybrid` |
| 知识文档状态 | `indexing` / `indexed` / `failed` |
| 知识权限 scope | `public` / `dept` / `role` |
| 文件业务类型 biz_type | `resume` / `knowledge` / `avatar` / `export` |
| 审计动作 action | `create` / `update` / `delete` / `login` / `export` / `ai_call` |

### 21.2 分页与排序

- `page` 从 1 开始，`page_size` 默认 20、上限 200（越界自动收敛，不报错）；
- `order_by` 只接受各模块白名单字段，非法字段**静默回落到默认排序**（防注入）；
- `order` 仅 `asc` / `desc`；
- 分页响应固定为 `{items, total, page, page_size, pages}`，`pages = ceil(total / page_size)`。

### 21.3 请求示例（curl）

```bash
# 1. 登录
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@12345"}' | jq -r '.data.access_token')

# 2. 当前用户
curl -s http://127.0.0.1:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"

# 3. 创建部门
curl -s -X POST http://127.0.0.1:8000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"RD","name":"技术研发部","parent_id":1}'

# 4. 用户分页
curl -s "http://127.0.0.1:8000/api/v1/users?page=1&page_size=10&keyword=张" \
  -H "Authorization: Bearer $TOKEN"

# 5. 上传简历（待实现模块）
curl -s -X POST http://127.0.0.1:8000/api/v1/resumes/upload \
  -H "Authorization: Bearer $TOKEN" -F "file=@张三-简历.pdf"
```

### 21.4 接口新增检查清单（提交 PR 前自检）

- [ ] Swagger 中能看到接口，`summary` 为中文、`response_model` 正确；
- [ ] 声明了 `responses=COMMON_ERROR_RESPONSES` 与所需权限；
- [ ] 分页接口返回 `PageResult`，排序字段进白名单；
- [ ] 错误路径返回本文档第二.3 节的错误码，无裸 `raise Exception`；
- [ ] 响应不含敏感字段（`password_hash`、身份证、未脱敏手机号）；
- [ ] 已在本文件的模块章节补充/更新契约（含请求/响应示例与错误约定）。

### 21.5 变更记录

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| v1.0.0 | 2026-11-03 | 首版：全局规范、错误码、权限矩阵、认证/用户/组织完整契约；其余 11 个模块的接口契约与数据结构先定稿（标记 🚧） |
