# 数据库设计约定与核心表

> 使用的技术：**MySQL 8（业务主数据）**、**PgSQL（分析/时序）**、**Milvus**（向量）、**Redis**（缓存）。
> **不使用 SQLite**：开发、测试、生产统一 MySQL；分析类数据统一 PgSQL。
> 所有表通过 SQLAlchemy 2.0 ORM 定义，禁止裸 SQL。

| 库 | 驱动 | 默认地址 | 存放内容 |
| --- | --- | --- | --- |
| MySQL | `mysql+aiomysql` | `127.0.0.1:3306/ai_talent_platform` | 用户、组织、角色、岗位、人才、测评、培训、匹配等业务主数据 |
| PgSQL | `postgresql+asyncpg` | `127.0.0.1:5432/ai_talent_analytics` | 海量人才履历、测评历史、培训轨迹、行为时序、分析宽表（Vanna NL2SQL 数据源） |

---

## 一、通用约定

### 1.1 每张业务表都有的字段（来自 `BaseModel`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT 自增 | 主键（`pk_<表名>`，主键索引自带） |
| `created_at` | DATETIME | 创建时间（DB + ORM 双默认） |
| `updated_at` | DATETIME | 更新时间（ORM `onupdate`） |
| `is_deleted` | TINYINT(1) | 逻辑删除标记（索引） |
| `deleted_at` | DATETIME | 删除时间 |
| `created_by` / `updated_by` | BIGINT | 操作人（数据范围/审计） |
| `version` | INT | 乐观锁版本号，更新 +1 |

### 1.2 命名与类型规范

| 项目 | 规范 |
| --- | --- |
| 表名 | 复数小写下划线（`talents`、`matching_records`） |
| 字段名 | 小写下划线 |
| 布尔 | `is_xxx`，`TINYINT(1)` |
| 枚举 | `VARCHAR` + 中文 comment（不用 MySQL ENUM，便于扩展） |
| 金额 | `DECIMAL(12,2)` |
| JSON | `JSON`（MySQL 8 原生） |
| 索引 | 唯一约束统一命名 `uq_表名_字段`，索引 `ix_字段` |
| 字符集 | `utf8mb4` / `utf8mb4_general_ci` |

### 1.3 逻辑删除与唯一键

逻辑删除后唯一键仍占用。两种方案：

1. 删除时改写唯一字段（当前 `users` 采用：`username` → `username#deleted#<id>`）；
2. 唯一索引带上 `is_deleted`（如 `(mobile, is_deleted)`），注意并发下重复删除的边界。

---

## 二、已实现的表

### users —— 用户账号

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| username | VARCHAR(64) UQ | 登录名 |
| password_hash | VARCHAR(128) | bcrypt 哈希（绝不存明文） |
| real_name | VARCHAR(64) | 真实姓名 |
| mobile | VARCHAR(20) UQ | 手机号 |
| email | VARCHAR(128) UQ | 邮箱 |
| avatar | VARCHAR(255) | 头像 |
| organization_id | BIGINT FK → organizations.id | 所属部门 |
| position_title | VARCHAR(64) | 职务 |
| is_active | TINYINT | 是否启用 |
| is_superuser | TINYINT | 超级管理员 |
| last_login_at / last_login_ip | DATETIME / VARCHAR(64) | 登录审计 |
| fail_count / locked_until | INT / DATETIME | 登录失败锁定 |

关系：`roles`（多对多，`lazy="selectin"`，由 `user_roles` 关联）。

### roles —— 角色

| 字段 | 说明 |
| --- | --- |
| code | 唯一：`super_admin / admin / hr / manager / employee` |
| name / description / is_active | 名称、描述、是否启用 |

### user_roles —— 用户角色关联

联合主键 `(user_id, role_id)`，均级联删除；`created_at` 记录绑定时间。

### organizations —— 组织架构

| 字段 | 说明 |
| --- | --- |
| parent_id | 上级部门（自关联，NULL 为根） |
| code | 部门编码（唯一） |
| name | 名称（同级唯一约束 `uq_organizations_parent_name`） |
| level | 层级（根为 1） |
| path | 祖级路径，如 `/1/3/`，用于子树查询 `LIKE '/1/3/%'` |
| leader_id | 负责人（**逻辑外键**，不加 DB 外键避免循环依赖） |
| sort / is_active / remark | 排序、启用、备注 |

---

## 三、待实现的表（结构已在 models 文件头部注释中给出）

| 表 | 用途 | 关键点 |
| --- | --- | --- |
| `talents` | 人才主档 | 手机号查重、技能 JSON、`source` 区分人工/AI |
| `talent_portraits` | 人才画像 | 与主档分离存储（可重建数据），记录 `model_name` |
| `positions` | 岗位 | `requirements` JSON 结构化要求、`status` 状态机 |
| `resumes` | 简历 | `file_hash` 秒传、`parsed_data` JSON、`status` 解析状态机 |
| `assessments` / `assessment_questions` / `assessment_answers` / `assessment_results` | 测评 | 状态机 `pending→in_progress→submitted→scored` |
| `courses` / `training_plans` / `plan_courses` / `learning_records` | 培训 | 完成率聚合查询、反作弊字段 |
| `matching_records` | 匹配记录 | `batch_id` 归组，唯一索引 `(batch_id, position_id, talent_id)` |
| `knowledge_docs` / `knowledge_chunks` | 知识库 | 元数据+原文入库，向量在 Milvus（存 `milvus_pk`） |
| `conversations` / `chat_messages` | AI 会话 | token 统计、citations 持久化 |
| `files` | 文件元数据 | `file_hash` 秒传、`biz_type + biz_id` 关联业务 |
| `audit_logs` | 审计 | 只增不改，敏感字段脱敏 |

---

## 四、四库职责边界

| 存储 | 内容 | 注意 |
| --- | --- | --- |
| **MySQL** | 用户、组织、岗位、人才、测评、培训等业务关系数据 | 主从复制、读写分离 |
| **PgSQL** | 海量人才履历、测评历史、时序行为、分析宽表 | 给 Vanna NL2SQL 用，避免大查询打 MySQL 主库 |
| **Milvus** | 人才/岗位/知识库/题干向量 | 只存向量 + 业务主键（pk），原文在关系库 |
| **Redis** | 缓存、会话记忆、限流计数、分布式锁 | 不存唯一数据源，随时可丢 |

> Milvus 是向量数据库，Redis 是缓存中间件，二者不能互相替代。

---

## 五、索引建议（高频查询）

| 表 | 索引 |
| --- | --- |
| users | `username`、`mobile`、`email`、`(organization_id, is_active)` |
| organizations | `code`、`parent_id`、`path` |
| talents | `(organization_id, status)`、`mobile`、`name` |
| positions | `(organization_id, status)`、`code` |
| resumes | `file_hash`、`talent_id`、`status` |
| learning_records | `(talent_id, status)`、`plan_id` |
| matching_records | `(batch_id, position_id, talent_id)` UQ、`position_id` |
| audit_logs | `(user_id, created_at)`、`module`、`trace_id` |

建索引原则：为「等值过滤 + 排序」组合建联合索引，遵循最左前缀；避免在 `is_deleted` 这样的低区分度字段单独建索引（可配合业务字段）。
