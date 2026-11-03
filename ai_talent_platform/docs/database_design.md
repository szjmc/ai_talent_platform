# 数据库设计文档（MySQL + PgSQL + Milvus + Redis）

> 版本：**v1.0.0** ｜ 更新日期：2026-11-03
> 存储方案：**MySQL 8（业务主数据）** + **PgSQL 16（分析/时序数据）** + **Milvus（向量）** + **Redis（缓存）**。
> **不使用 SQLite**；所有表结构通过 SQLAlchemy 2.0 ORM 定义，禁止裸 SQL。
> 本文档 = 表字典 + 字段说明 + 索引 + 关系图 + 可直接执行的 DDL。
>
> 相关文档：[API 接口文档](api_document.md) ｜ [公共层使用手册](公共层使用手册.md) ｜ [架构说明](architecture.md)

---

## 目录

1. [四库职责与连接](#db-overview)
2. [通用设计约定](#db-conventions)
3. [ER 关系图（内联 SVG）](#db-er)
4. [表总览](#db-tables)
5. [公共层已建表（✅ 已实现）](#db-common-tables)
6. [业务表设计（🚧 待实现）](#db-business-tables)
7. [分析库 PgSQL 设计](#db-pgsql)
8. [Milvus 向量集合设计](#db-milvus)
9. [Redis Key 设计](#db-redis)
10. [完整 DDL（MySQL 8）](#db-ddl)
11. [数据字典：枚举值](#db-enums)
12. [索引与性能建议](#db-index)
13. [迁移与初始化](#db-migration)

---

<a id="db-overview"></a>

## 一、四库职责与连接

| 库 | 版本 | 驱动 | 默认连接 | 职责 | 是否硬依赖 |
| --- | --- | --- | --- | --- | --- |
| **MySQL** | 8.0+ | `mysql+aiomysql`（异步）/ `mysql+pymysql`（迁移） | `127.0.0.1:3306/ai_talent_platform` | 用户、组织、角色、岗位、人才、测评、培训、匹配、知识库元数据、文件、审计、会话 | 是（不可用则服务不就绪） |
| **PgSQL** | 15+ | `postgresql+asyncpg` / `postgresql+psycopg2` | `127.0.0.1:5432/ai_talent_analytics` | 海量履历明细、测评历史、培训轨迹、行为时序、分析宽表（Vanna NL2SQL 数据源） | 否（不可用则分析功能降级） |
| **Milvus** | 2.4+ | `pymilvus` | `http://127.0.0.1:19530` | 人才 / 岗位 / 知识库 / 测评题干向量 | 否（关闭则无语义检索） |
| **Redis** | 7+ | `redis.asyncio` | `redis://127.0.0.1:6379/0` | 缓存、会话记忆、限流、分布式锁 | 否（降级为进程内缓存） |

**边界原则**

- 关系型业务数据只在 MySQL 落库；MySQL 不存大文本明细的历史流水（走 PgSQL）。
- 分析类大查询（NL2SQL、报表）只打 PgSQL，禁止扫 MySQL 主库。
- Milvus 只存「向量 + 业务主键」，原文/元数据在 MySQL，检索后回表。
- Redis 不存唯一数据源，随时可丢。

---

<a id="db-conventions"></a>

## 二、通用设计约定

### 2.1 所有业务表共有的系统字段（来自 `BaseModel`）

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `id` | BIGINT AUTO_INCREMENT | — | 主键（`pk_<表名>`，主键索引自带） |
| `created_at` | DATETIME NOT NULL | `CURRENT_TIMESTAMP` | 创建时间 |
| `updated_at` | DATETIME NOT NULL | `CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` | 更新时间 |
| `is_deleted` | BOOL(TINYINT) NOT NULL | `0` | 逻辑删除标记（有索引） |
| `deleted_at` | DATETIME NULL | NULL | 删除时间 |
| `created_by` | BIGINT NULL | NULL | 创建人用户 ID（数据范围/审计） |
| `updated_by` | BIGINT NULL | NULL | 最后修改人用户 ID |
| `version` | INT NOT NULL | `1` | 乐观锁版本号，更新 +1 |

### 2.2 命名规范

| 对象 | 规则 | 示例 |
| --- | --- | --- |
| 表名 | 复数、小写下划线 | `talents`、`matching_records` |
| 字段名 | 小写下划线 | `work_years`、`organization_id` |
| 主键约束 | `pk_<表名>` | `pk_users` |
| 唯一约束 | `uq_<表名>_<字段>` | `uq_organizations_parent_name` |
| 普通索引 | `ix_<字段>`（同名自动加表前缀） | `ix_users_organization_id` |
| 外键 | `fk_<表名>_<字段>_<引用表>` | `fk_users_organization_id_organizations` |
| 布尔字段 | `is_` / `has_` 前缀 | `is_active` |
| 时间字段 | `_at` 结尾 | `created_at`、`parsed_at` |
| 金额字段 | `DECIMAL(12,2)` | `salary_min` |
| 枚举字段 | `VARCHAR(20/32)` + 中文 `COMMENT` | `status` |

### 2.3 类型与字符集

- 字符集 `utf8mb4` / 排序 `utf8mb4_general_ci`；引擎 `InnoDB`；行格式 `DYNAMIC`。
- 主键统一 `BIGINT`（避免后期扩容）；布尔用 `TINYINT(1)`（SQLAlchemy `Boolean`）。
- 长文本：`VARCHAR(512)` 以内；超长用 `TEXT`（如岗位说明书 `description`）。
- 结构化扩展字段用 `JSON`（MySQL 8 原生，可建函数索引 / `json_contains` 查询）。
- 时间统一「本地时间」（`Asia/Shanghai`），JWT 内部用 UTC 时间戳。

### 2.4 逻辑删除与唯一约束

逻辑删除后唯一键仍占用，两种处理：

1. **删除时改写唯一字段**（`users` 采用）：`username` → `username#deleted#<id>`，同时清空 `mobile/email`；
2. **唯一索引带 `is_deleted`**：如 `UNIQUE(mobile, is_deleted)`，适合允许「删除后重新录入」的场景（`talents` 采用）。

### 2.5 外键策略

- 强关联且生命周期一致的子表使用**物理外键**（`user_roles`、`knowledge_chunks`、`assessment_answers` 等），删除策略 `CASCADE`；
- 弱关联或可能造成**循环依赖**的字段使用**逻辑外键**（如 `organizations.leader_id -> users.id`、`*.created_by`），由 Service 校验存在性；
- 关联查询以应用层 `JOIN`/`IN` 为主，禁止在热点查询上做多级级联。

---

<a id="db-er"></a>

## 三、ER 关系图

> 内联 SVG，离线可打开。实线为物理外键，虚线为逻辑关联。

<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 700" width="1120" height="700" font-family="-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif">
  <defs>
    <style>
      .tbl { fill: #ffffff; stroke: #94a3b8; stroke-width: 1.2; rx: 6; ry: 6; }
      .tbl-head { fill: #1e293b; }
      .tbl-title { font-size: 12px; font-weight: 700; fill: #ffffff; }
      .col { font-size: 10.5px; fill: #334155; }
      .pk { font-size: 10.5px; font-weight: 700; fill: #b91c1c; }
      .fk { font-size: 10.5px; fill: #1d4ed8; }
      .rel { stroke: #475569; stroke-width: 1.2; fill: none; }
      .rel-weak { stroke: #94a3b8; stroke-width: 1.1; stroke-dasharray: 4 3; fill: none; }
      .legend { font-size: 11px; fill: #475569; }
      .group { font-size: 12px; font-weight: 700; fill: #0f172a; }
    </style>
    <marker id="dot" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6">
      <circle cx="5" cy="5" r="4" fill="#475569"/>
    </marker>
    <marker id="crow" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="12" markerHeight="9" orient="auto-start-reverse">
      <path d="M 0 6 L 8 1 M 0 6 L 8 11 M 0 6 L 12 6" stroke="#475569" stroke-width="1.2" fill="none"/>
    </marker>
  </defs>

  <rect x="0" y="0" width="1120" height="700" fill="#f8fafc"/>
  <text x="24" y="28" font-size="16" font-weight="700" fill="#0f172a">ER 关系图（核心表）</text>
  <text x="24" y="46" class="legend">PK 主键 ｜ FK 外键 ｜ 虚线 = 逻辑外键（应用层校验）｜ 所有表均含 id/created_at/updated_at/is_deleted/deleted_at/created_by/updated_by/version 系统字段（下图省略）</text>

  <!-- organizations -->
  <g>
    <rect x="24" y="70" width="190" height="126" class="tbl"/>
    <rect x="24" y="70" width="190" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="34" y="85" class="tbl-title">organizations 组织</text>
    <text x="34" y="106" class="pk">PK id</text>
    <text x="34" y="121" class="fk">FK parent_id → organizations.id</text>
    <text x="34" y="136" class="col">code  UNIQUE</text>
    <text x="34" y="151" class="col">name / level / path</text>
    <text x="34" y="166" class="col">leader_id（逻辑 FK→users）</text>
    <text x="34" y="181" class="col">sort / is_active / remark</text>
  </g>

  <!-- users -->
  <g>
    <rect x="24" y="230" width="190" height="146" class="tbl"/>
    <rect x="24" y="230" width="190" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="34" y="245" class="tbl-title">users 用户</text>
    <text x="34" y="266" class="pk">PK id</text>
    <text x="34" y="281" class="fk">FK organization_id → organizations</text>
    <text x="34" y="296" class="col">username UNIQUE / password_hash</text>
    <text x="34" y="311" class="col">mobile / email / real_name</text>
    <text x="34" y="326" class="col">is_active / is_superuser</text>
    <text x="34" y="341" class="col">fail_count / locked_until</text>
    <text x="34" y="356" class="col">last_login_at / last_login_ip</text>
  </g>

  <!-- roles / user_roles -->
  <g>
    <rect x="24" y="410" width="190" height="92" class="tbl"/>
    <rect x="24" y="410" width="190" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="34" y="425" class="tbl-title">roles 角色</text>
    <text x="34" y="446" class="pk">PK id</text>
    <text x="34" y="461" class="col">code UNIQUE（super_admin…）</text>
    <text x="34" y="476" class="col">name / description / is_active</text>
  </g>
  <g>
    <rect x="24" y="524" width="190" height="72" class="tbl"/>
    <rect x="24" y="524" width="190" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="34" y="539" class="tbl-title">user_roles 关联</text>
    <text x="34" y="560" class="fk">PK/FK user_id → users.id</text>
    <text x="34" y="575" class="fk">PK/FK role_id → roles.id</text>
    <text x="34" y="590" class="col">created_at</text>
  </g>

  <!-- positions -->
  <g>
    <rect x="286" y="70" width="200" height="146" class="tbl"/>
    <rect x="286" y="70" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="296" y="85" class="tbl-title">positions 岗位</text>
    <text x="296" y="106" class="pk">PK id</text>
    <text x="296" y="121" class="fk">FK organization_id → organizations</text>
    <text x="296" y="136" class="col">code UNIQUE / title</text>
    <text x="296" y="151" class="col">level / headcount / status</text>
    <text x="296" y="166" class="col">description TEXT</text>
    <text x="296" y="181" class="col">requirements JSON</text>
    <text x="296" y="196" class="col">skill_tags JSON / salary_*</text>
  </g>

  <!-- talents -->
  <g>
    <rect x="286" y="246" width="200" height="176" class="tbl"/>
    <rect x="286" y="246" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="296" y="261" class="tbl-title">talents 人才档案</text>
    <text x="296" y="282" class="pk">PK id</text>
    <text x="296" y="297" class="fk">FK organization_id → organizations</text>
    <text x="296" y="312" class="fk">FK resume_id → resumes.id</text>
    <text x="296" y="327" class="col">name / gender / mobile</text>
    <text x="296" y="342" class="col">education / school / major</text>
    <text x="296" y="357" class="col">work_years / current_company</text>
    <text x="296" y="372" class="col">skill_tags JSON / certificates</text>
    <text x="296" y="387" class="col">status / potential_level / source</text>
    <text x="296" y="402" class="col">portrait_summary</text>
  </g>

  <!-- resumes / files -->
  <g>
    <rect x="548" y="70" width="200" height="132" class="tbl"/>
    <rect x="548" y="70" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="558" y="85" class="tbl-title">resumes 简历</text>
    <text x="558" y="106" class="pk">PK id</text>
    <text x="558" y="121" class="fk">FK talent_id → talents.id</text>
    <text x="558" y="136" class="fk">FK file_id → files.id</text>
    <text x="558" y="151" class="col">file_hash UNIQUE（秒传）</text>
    <text x="558" y="166" class="col">status / accuracy / parsed_data JSON</text>
    <text x="558" y="181" class="col">parse_error / parsed_at</text>
    <text x="558" y="196" class="col">file_name / file_type / file_size</text>
  </g>
  <g>
    <rect x="548" y="230" width="200" height="102" class="tbl"/>
    <rect x="548" y="230" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="558" y="245" class="tbl-title">files 文件元数据</text>
    <text x="558" y="266" class="pk">PK id</text>
    <text x="558" y="281" class="col">file_hash（去重）/ stored_name</text>
    <text x="558" y="296" class="col">biz_type + biz_id / uploader_id</text>
    <text x="558" y="311" class="col">storage_type / file_path / size</text>
    <text x="558" y="326" class="col">content_type / download_count</text>
  </g>

  <!-- assessments -->
  <g>
    <rect x="548" y="360" width="200" height="150" class="tbl"/>
    <rect x="548" y="360" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="558" y="375" class="tbl-title">assessments 测评</text>
    <text x="558" y="396" class="pk">PK id</text>
    <text x="558" y="411" class="fk">FK talent_id / position_id</text>
    <text x="558" y="426" class="col">title / status / deadline</text>
    <text x="558" y="441" class="col">question_count / started_at</text>
    <text x="558" y="456" class="col">submitted_at / created_by_ai</text>
    <text x="558" y="476" class="legend">├─ assessment_questions 题目</text>
    <text x="558" y="491" class="legend">├─ assessment_answers 答题</text>
    <text x="558" y="506" class="legend">└─ assessment_results 结果</text>
  </g>

  <!-- trainings -->
  <g>
    <rect x="548" y="536" width="200" height="132" class="tbl"/>
    <rect x="548" y="536" width="200" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="558" y="551" class="tbl-title">training_plans 培训</text>
    <text x="558" y="572" class="pk">PK id</text>
    <text x="558" y="587" class="fk">FK talent_id → talents.id</text>
    <text x="558" y="602" class="col">name / goal / start_date / end_date</text>
    <text x="558" y="617" class="col">status / created_by_ai</text>
    <text x="558" y="632" class="legend">├─ plan_courses → courses</text>
    <text x="558" y="647" class="legend">└─ learning_records 学习记录</text>
  </g>

  <!-- matching / knowledge / conversation / audit -->
  <g>
    <rect x="810" y="70" width="286" height="132" class="tbl"/>
    <rect x="810" y="70" width="286" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="820" y="85" class="tbl-title">matching_records 匹配记录</text>
    <text x="820" y="106" class="pk">PK id</text>
    <text x="820" y="121" class="fk">UQ(batch_id, position_id, talent_id)</text>
    <text x="820" y="136" class="col">score / dimension_scores JSON</text>
    <text x="820" y="151" class="col">highlights / gaps JSON</text>
    <text x="820" y="166" class="col">strategy / model_name / matched_by</text>
    <text x="820" y="181" class="col">matched_at（按月分区）</text>
    <text x="820" y="196" class="legend">↳ position_id, talent_id 逻辑外键</text>
  </g>
  <g>
    <rect x="810" y="230" width="286" height="132" class="tbl"/>
    <rect x="810" y="230" width="286" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="820" y="245" class="tbl-title">knowledge_docs / knowledge_chunks</text>
    <text x="820" y="266" class="pk">PK id（docs / chunks）</text>
    <text x="820" y="281" class="fk">chunks.doc_id → knowledge_docs.id</text>
    <text x="820" y="296" class="col">title / category / status</text>
    <text x="820" y="311" class="col">permission_scope / allowed_roles JSON</text>
    <text x="820" y="326" class="col">chunk_count / embedding_model</text>
    <text x="820" y="341" class="col">chunks.milvus_pk（向量主键）</text>
    <text x="820" y="356" class="legend">↳ 向量存 Milvus，原文在本表</text>
  </g>
  <g>
    <rect x="810" y="390" width="286" height="112" class="tbl"/>
    <rect x="810" y="390" width="286" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="820" y="405" class="tbl-title">conversations / chat_messages</text>
    <text x="820" y="426" class="pk">PK id</text>
    <text x="820" y="441" class="fk">FK user_id → users.id</text>
    <text x="820" y="456" class="col">agent_type / title / message_count</text>
    <text x="820" y="471" class="col">messages: role / content / citations</text>
    <text x="820" y="486" class="col">prompt_tokens / completion_tokens</text>
  </g>
  <g>
    <rect x="810" y="528" width="286" height="140" class="tbl"/>
    <rect x="810" y="528" width="286" height="22" class="tbl-head" rx="6" ry="6"/>
    <text x="820" y="543" class="tbl-title">audit_logs 审计</text>
    <text x="820" y="564" class="pk">PK id</text>
    <text x="820" y="579" class="col">user_id / username（逻辑 FK）</text>
    <text x="820" y="594" class="col">action / module / target_type / target_id</text>
    <text x="820" y="609" class="col">request_method / path / ip / trace_id</text>
    <text x="820" y="624" class="col">before_data / after_data JSON</text>
    <text x="820" y="639" class="col">status / error_message / created_at</text>
    <text x="820" y="656" class="legend">只增不改，无 update/delete</text>
  </g>

  <!-- 关系线 -->
  <path class="rel" d="M 214 96 L 286 96" marker-end="url(#crow)"/>
  <path class="rel" d="M 214 286 L 286 300" marker-end="url(#crow)"/>
  <path class="rel" d="M 119 300 L 119 410" marker-end="url(#crow)"/>
  <path class="rel" d="M 119 524 L 119 502" marker-end="url(#crow)"/>
  <path class="rel" d="M 409 246 L 409 216" marker-end="url(#crow)"/>
  <path class="rel" d="M 486 340 L 548 150" marker-end="url(#crow)"/>
  <path class="rel" d="M 486 360 L 548 280" marker-end="url(#crow)"/>
  <path class="rel" d="M 486 400 L 548 430" marker-end="url(#crow)"/>
  <path class="rel" d="M 486 420 L 548 600" marker-end="url(#crow)"/>
  <path class="rel-weak" d="M 186 168 L 340 246" marker-end="url(#dot)"/>
  <path class="rel-weak" d="M 214 450 L 810 560" marker-end="url(#dot)"/>
  <path class="rel-weak" d="M 214 300 L 810 430" marker-end="url(#dot)"/>
</svg>

---

<a id="db-tables"></a>

## 四、表总览

> 状态说明：✅ 已建 ORM 模型并参与建表；🚧 未实现。
| # | 表名 | 中文名 | 连接/生命周期 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `organizations` | 组织架构 | 自关联树（`path` 前缀） | ✅ 已实现 |
| 2 | `users` | 用户账号 | → organizations | ✅ 已实现 |
| 3 | `roles` | 角色 | 独立枚举表 | ✅ 已实现 |
| 4 | `user_roles` | 用户角色关联 | 多对多 | ✅ 已实现 |
| 5 | `positions` | 岗位 | → organizations | ✅ |
| 6 | `position_requirements` | 岗位能力模型（多版本） | → positions | ✅ |
| 7 | `talents` | 人才档案 | → organizations / resumes | ✅ |
| 8 | `talent_portraits` | 人才画像（版本化） | → talents | ✅ |
| 9 | `resumes` | 简历 | → talents / files | ✅ |
| 10 | `resume_experiences` | 简历工作经历（可选拆表） | → resumes | ✅ |
| 11 | `files` | 文件元数据 | 多态关联（biz_type+biz_id） | ✅ |
| 12 | `assessments` | 测评任务 | → talents / positions | ✅ |
| 13 | `assessment_questions` | 测评题目 | → assessments | ✅ |
| 14 | `assessment_answers` | 答题记录 | → assessments / questions | ✅ |
| 15 | `assessment_results` | 测评结果 | 1:1 → assessments | ✅ |
| 16 | `courses` | 课程 | 独立 | ✅ |
| 17 | `training_plans` | 培训计划 | → talents | ✅ |
| 18 | `plan_courses` | 计划-课程 | 多对多 | ✅ |
| 19 | `learning_records` | 学习记录 | → plans / courses / talents | ✅ |
| 20 | `matching_records` | 人岗匹配记录 | → positions / talents | ✅ |
| 21 | `knowledge_docs` | 知识文档 | → files | ✅ |
| 22 | `knowledge_chunks` | 知识切片 | → knowledge_docs | ✅ |
| 23 | `conversations` | AI 会话 | → users | ✅ |
| 24 | `chat_messages` | 会话消息 | → conversations | ✅ |
| 25 | `audit_logs` | 操作审计 | 只增不改 | ✅ |

---

<a id="db-common-tables"></a>

## 五、公共层已建表 — ✅ 已实现

> 以下 4 张表已由 `scripts/init_db.py` 创建并参与 `/api/v1/system/info` 的 `orm_tables` 校验。

### 5.1 `organizations` 组织架构

| 字段 | 类型 | 空 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| parent_id | BIGINT | 是 | NULL | 上级部门（自关联 FK，NULL=根） |
| code | VARCHAR(64) | 否 | — | 部门编码，**唯一** |
| name | VARCHAR(128) | 否 | — | 部门名称（与 parent_id 组合唯一） |
| level | INT | 否 | 1 | 层级，根为 1 |
| path | VARCHAR(512) | 否 | `/` | 祖级路径（父路径前缀），如 `/1/3/` |
| leader_id | BIGINT | 是 | NULL | 负责人用户 ID（**逻辑外键**→users.id） |
| sort | INT | 否 | 0 | 排序号（升序） |
| is_active | BOOL | 否 | 1 | 是否启用 |
| remark | VARCHAR(255) | 是 | NULL | 备注 |

索引：`UNIQUE(code)`、`UNIQUE(parent_id, name)`、`ix(parent_id)`、`ix(path)`、`ix(name)`、`ix(leader_id)`、`ix(is_deleted)`。
关系：`parent_id → organizations.id`（ON DELETE SET NULL）。
规则：新增时 `path = 父.path + 父.id + '/'`；移动部门级联更新所有子孙 `path`/`level`；删除前须无子部门、无在职用户。

### 5.2 `users` 用户账号

| 字段 | 类型 | 空 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| username | VARCHAR(64) | 否 | — | 登录名，**唯一**（删除时改写为 `name#deleted#<id>`） |
| password_hash | VARCHAR(128) | 否 | — | bcrypt 哈希（永不返回给前端） |
| real_name | VARCHAR(64) | 是 | NULL | 真实姓名 |
| mobile | VARCHAR(20) | 是 | NULL | 手机号，**唯一**（删除时置空） |
| email | VARCHAR(128) | 是 | NULL | 邮箱，**唯一**（删除时置空） |
| avatar | VARCHAR(255) | 是 | NULL | 头像地址 |
| organization_id | BIGINT | 是 | NULL | 所属部门（FK→organizations.id，ON DELETE SET NULL） |
| position_title | VARCHAR(64) | 是 | NULL | 岗位/职务 |
| is_active | BOOL | 否 | 1 | 是否启用 |
| is_superuser | BOOL | 否 | 0 | 是否超管 |
| last_login_at | DATETIME | 是 | NULL | 最后登录时间 |
| last_login_ip | VARCHAR(64) | 是 | NULL | 最后登录 IP |
| fail_count | INT | 否 | 0 | 连续登录失败次数 |
| locked_until | DATETIME | 是 | NULL | 锁定截止时间 |

索引：`UNIQUE(username)`、`UNIQUE(mobile)`、`UNIQUE(email)`、`ix(organization_id)`、`ix(is_deleted)`。
关系：`roles`（多对多，经 `user_roles`，`lazy="selectin"`）。

### 5.3 `roles` 角色

| 字段 | 类型 | 空 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| code | VARCHAR(64) | 否 | — | 角色编码，**唯一**：`super_admin/admin/hr/manager/employee` |
| name | VARCHAR(64) | 否 | — | 角色名称 |
| description | VARCHAR(255) | 是 | NULL | 描述 |
| is_active | BOOL | 否 | 1 | 是否启用 |

索引：`UNIQUE(code)`。初始化由 `scripts/init_db.py` 幂等写入 5 个内置角色。

### 5.4 `user_roles` 用户角色关联

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| user_id | BIGINT | **联合主键**，FK→users.id（ON DELETE CASCADE） |
| role_id | BIGINT | **联合主键**，FK→roles.id（ON DELETE CASCADE） |
| created_at | DATETIME | 绑定时间（默认 `CURRENT_TIMESTAMP`） |

---

<a id="db-business-tables"></a>

## 六、业务表设计 — ✅ ORM 已实现

> 表结构已定稿，ORM 模型已全部落地（字段名/类型/索引/约束如需变更需评审，并同步 Alembic 迁移）。
> 21 张业务表 + 4 张公共表共 25 张表由 `app/models/*` 定义，经 `app/models/__init__.py` 注册进 `Base.metadata`；
> 执行 `python scripts/init_db.py` 建表，`python scripts/dump_orm_mapping.py --output docs/orm_mapping.md` 导出全局 ORM 映射。
> 注意：Repository / Service / API 仍是骨架，表结构可用但业务读写逻辑未实现。

### 6.1 `positions` 岗位

| 字段 | 类型 | 空 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT | 否 | AI | 主键 |
| title | VARCHAR(128) | 否 | — | 岗位名称 |
| code | VARCHAR(64) | 否 | — | 岗位编码，**唯一** |
| organization_id | BIGINT | 否 | — | 所属部门（FK→organizations.id） |
| level | VARCHAR(20) | 否 | `middle` | `junior/middle/senior/expert` |
| headcount | INT | 否 | 1 | 编制人数 |
| status | VARCHAR(20) | 否 | `open` | `open/paused/closed` |
| description | TEXT | 是 | NULL | 岗位说明书全文 |
| requirements | JSON | 是 | NULL | 结构化要求（education/experience_years/skills/certificates/soft_skills/weights） |
| skill_tags | JSON | 是 | NULL | 技能标签数组 |
| salary_min | DECIMAL(12,2) | 是 | NULL | 薪资下限（元/月） |
| salary_max | DECIMAL(12,2) | 是 | NULL | 薪资上限 |
| publish_at | DATETIME | 是 | NULL | 发布时间 |
| close_at | DATETIME | 是 | NULL | 关闭时间 |

索引：`UNIQUE(code)`、`ix(organization_id, status)`、`ix(level)`、`ix(is_deleted)`。
状态机：`open ⇄ paused`，`open/paused → closed`（单向）。

### 6.2 `position_requirements` 岗位能力模型（多版本）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| position_id | BIGINT | FK→positions.id（CASCADE） |
| version | INT | 版本号（同岗位递增） |
| dimensions | JSON | 维度定义（技能/经验/学历/素质） |
| weights | JSON | 维度权重（合计 1.0） |
| source | VARCHAR(20) | `manual` / `ai_parse` |

索引：`UNIQUE(position_id, version)`。

### 6.3 `talents` 人才档案

| 字段 | 类型 | 空 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT | 否 | AI | 主键 |
| name | VARCHAR(64) | 否 | — | 姓名 |
| gender | VARCHAR(16) | 是 | `unknown` | `male/female/unknown` |
| birth_date | DATE | 是 | NULL | 出生日期 |
| mobile | VARCHAR(20) | 是 | NULL | 手机号（与 is_deleted 组合唯一） |
| email | VARCHAR(128) | 是 | NULL | 邮箱 |
| id_card | VARCHAR(128) | 是 | NULL | 身份证（AES 加密存储，响应脱敏） |
| education | VARCHAR(20) | 是 | NULL | 最高学历 |
| school | VARCHAR(128) | 是 | NULL | 毕业院校 |
| major | VARCHAR(128) | 是 | NULL | 专业 |
| work_years | INT | 否 | 0 | 工作年限 |
| current_company | VARCHAR(128) | 是 | NULL | 现公司 |
| current_position | VARCHAR(128) | 是 | NULL | 现职位 |
| organization_id | BIGINT | 是 | NULL | 归属部门（FK→organizations.id） |
| skill_tags | JSON | 是 | NULL | 技能标签 |
| certificates | JSON | 是 | NULL | 证书 |
| self_evaluation | TEXT | 是 | NULL | 自我评价 |
| status | VARCHAR(20) | 否 | `active` | `active/left/reserve` |
| potential_level | VARCHAR(8) | 是 | NULL | 潜力等级 `A/B/C/D`（画像生成） |
| portrait_summary | VARCHAR(512) | 是 | NULL | 画像摘要 |
| source | VARCHAR(20) | 否 | `manual` | `manual` / `ai_resume` |
| resume_id | BIGINT | 是 | NULL | 来源简历（FK→resumes.id，SET NULL） |

索引：`UNIQUE(mobile, is_deleted)`、`ix(organization_id, status)`、`ix(name)`、`ix(work_years)`、`ix(is_deleted)`。
JSON 检索：`skill_tags` 用 `JSON_CONTAINS(skill_tags, '"Python"')`；高频技能筛选建议另建 `talent_skills(talent_id, skill)` 关联表并建索引。

### 6.4 `talent_portraits` 人才画像（版本化）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| talent_id | BIGINT | FK→talents.id（CASCADE） |
| version | INT | 画像版本（每次重算 +1） |
| dimensions | JSON | `{skill, experience, quality, potential}` 0-100 |
| labels | JSON | AI 标签数组 |
| strengths | JSON | 优势列表 |
| weaknesses | JSON | 短板列表 |
| suggested_positions | JSON | 建议岗位 |
| potential_level | VARCHAR(8) | 潜力等级 |
| model_name | VARCHAR(64) | 生成模型（可追溯） |
| prompt_version | VARCHAR(32) | 提示词版本 |
| generated_at | DATETIME | 生成时间 |

索引：`UNIQUE(talent_id, version)`、`ix(generated_at)`。
> 画像属「可重建数据」，与主档分离；只保留最近 N 个版本，历史版本按月归档到 PgSQL。

### 6.5 `resumes` 简历

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| talent_id | BIGINT | FK→talents.id（SET NULL），解析后回填 |
| file_id | BIGINT | FK→files.id |
| file_name | VARCHAR(255) | 原始文件名 |
| file_type | VARCHAR(32) | `pdf/docx/doc/xlsx/txt/png/jpg` |
| file_size | BIGINT | 字节数 |
| file_hash | CHAR(64) | sha256，**唯一**（秒传/查重） |
| status | VARCHAR(20) | `uploaded/parsing/success/failed` |
| accuracy | DECIMAL(4,3) | 解析置信度 0~1 |
| parsed_data | JSON | 结构化简历（basic_info/education/work_experience/projects/skills） |
| parse_error | VARCHAR(512) | 失败原因 |
| parsed_at | DATETIME | 解析完成时间 |

索引：`UNIQUE(file_hash)`、`ix(talent_id)`、`ix(status)`、`ix(is_deleted)`。

### 6.6 `resume_experiences` 简历工作经历（可选拆表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| resume_id | BIGINT | FK→resumes.id（CASCADE） |
| company / title | VARCHAR(128) | 公司 / 职位 |
| start_date / end_date | DATE | 起止（`end_date` 为空表示至今） |
| description | TEXT | 工作内容 |

索引：`ix(resume_id)`、`ix(company)`（用于「某公司背景人才」统计）。

### 6.7 `files` 文件元数据

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| file_name | VARCHAR(255) | 原始文件名 |
| stored_name | VARCHAR(128) | 存储名（uuid + 扩展名） |
| file_path | VARCHAR(512) | 相对路径 `{biz_type}/{yyyy}/{mm}/xxx` |
| file_size | BIGINT | 字节数 |
| content_type | VARCHAR(128) | MIME |
| extension | VARCHAR(16) | 扩展名（含点） |
| file_hash | CHAR(64) | sha256（秒传，可选唯一） |
| storage_type | VARCHAR(16) | `local` / `oss` |
| biz_type | VARCHAR(32) | `resume/knowledge/avatar/export` |
| biz_id | BIGINT | 关联业务 ID |
| uploader_id | BIGINT | 上传人（FK→users.id） |
| is_public | BOOL | 是否可公开访问 |
| download_count | INT | 下载次数 |

索引：`ix(file_hash)`、`ix(biz_type, biz_id)`、`ix(uploader_id)`。

### 6.8 `assessments` 测评任务

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| talent_id | BIGINT | FK→talents.id |
| position_id | BIGINT | FK→positions.id（可空） |
| title | VARCHAR(128) | 测评标题 |
| status | VARCHAR(20) | `pending/in_progress/submitted/scored` |
| question_count | INT | 题目数量 |
| deadline | DATETIME | 截止时间 |
| started_at / submitted_at | DATETIME | 开始 / 提交时间 |
| total_score | DECIMAL(5,2) | 总分（提交后回填） |
| created_by_ai | BOOL | 是否 AI 生成（千人千卷） |

索引：`ix(talent_id, status)`、`ix(deadline)`。

### 6.9 `assessment_questions` 题目

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| assessment_id | BIGINT | FK→assessments.id（CASCADE） |
| type | VARCHAR(16) | `single/multiple/text` |
| content | TEXT | 题干 |
| options | JSON | 选项 `[{key,text}]`（客观题） |
| answer | JSON | 标准答案（**绝不返回给前端**） |
| score | DECIMAL(5,2) | 分值 |
| dimension | VARCHAR(32) | 考察维度（用于雷达图） |
| sort | INT | 展示顺序 |

索引：`ix(assessment_id, sort)`。

### 6.10 `assessment_answers` 答题记录

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| assessment_id | BIGINT | FK→assessments.id（CASCADE） |
| question_id | BIGINT | FK→assessment_questions.id（CASCADE） |
| answer | JSON | 用户答案 |
| score | DECIMAL(5,2) | 本题得分 |
| scored_by | VARCHAR(16) | `rule` / `ai` |
| scored_at | DATETIME | 评分时间 |

索引：`UNIQUE(assessment_id, question_id)`（重复提交覆盖）。

### 6.11 `assessment_results` 测评结果

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| assessment_id | BIGINT | **唯一**，FK→assessments.id（CASCADE） |
| total_score | DECIMAL(5,2) | 总分 |
| dimension_scores | JSON | 维度分（雷达图数据源） |
| level | VARCHAR(16) | `初级/中级/高级/专家` |
| report | JSON | AI 研判报告 |
| suggestions | JSON | 提升建议 |
| model_name / prompt_version | VARCHAR | 可追溯 |

索引：`UNIQUE(assessment_id)`、`ix(level)`。

### 6.12 `courses` 课程

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| title | VARCHAR(128) | 课程名称 |
| category | VARCHAR(32) | 分类 |
| level | VARCHAR(20) | 适用层级 |
| duration_hours | DECIMAL(6,2) | 学时 |
| resource_url | VARCHAR(512) | 资源地址 |
| cover | VARCHAR(255) | 封面 |
| description | TEXT | 简介 |
| skill_tags | JSON | 对应技能标签 |
| is_active | BOOL | 是否上架 |

索引：`ix(category)`、`ix(level)`。

### 6.13 `training_plans` 培训计划

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| name | VARCHAR(128) | 计划名称 |
| talent_id | BIGINT | FK→talents.id |
| goal | VARCHAR(512) | 培养目标 |
| start_date / end_date | DATE | 起止日期 |
| status | VARCHAR(20) | `draft/ongoing/finished/cancelled` |
| created_by_ai | BOOL | 是否 AI 生成 |
| source_assessment_id | BIGINT | 能力缺口来源测评 |

索引：`ix(talent_id, status)`。

### 6.14 `plan_courses` 计划-课程

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| plan_id | BIGINT | FK→training_plans.id（CASCADE） |
| course_id | BIGINT | FK→courses.id |
| sort | INT | 学习顺序 |
| require_pass | BOOL | 是否必须通过考核 |

`UNIQUE(plan_id, course_id)`。

### 6.15 `learning_records` 学习记录

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| plan_id / course_id / talent_id | BIGINT | 关联 |
| progress | INT | 0-100（单调递增） |
| study_minutes | INT | 累计学习分钟 |
| last_study_at | DATETIME | 最近学习时间 |
| status | VARCHAR(20) | `not_started/learning/completed` |
| score | DECIMAL(5,2) | 考核分数 |
| passed | BOOL | 是否通过 |
| suspicious | BOOL | 反作弊标记（学时与进度比例异常） |

索引：`UNIQUE(plan_id, course_id, talent_id)`、`ix(talent_id, status)`。

### 6.16 `matching_records` 人岗匹配记录

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| batch_id | CHAR(32) | 跑批 ID（uuid hex），归组同批结果 |
| position_id | BIGINT | 逻辑 FK→positions.id |
| talent_id | BIGINT | 逻辑 FK→talents.id |
| score | DECIMAL(5,2) | 总分 0-100 |
| dimension_scores | JSON | `{skill,experience,education,quality}` |
| highlights | JSON | 优势亮点 |
| gaps | JSON | 短板 |
| suggestion | VARCHAR(512) | 适配建议 |
| strategy | VARCHAR(16) | `hard/soft/hybrid` |
| model_name | VARCHAR(64) | AI 解释所用模型 |
| matched_by | BIGINT | 触发用户 |
| matched_at | DATETIME | 匹配时间（分区键） |

索引：`UNIQUE(batch_id, position_id, talent_id)`、`ix(position_id, score)`、`ix(talent_id)`、`ix(matched_at)`。
说明：可重算数据，按月 `RANGE` 分区（或迁至 PgSQL），历史保留 6 个月。

### 6.17 `knowledge_docs` 知识文档

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| title | VARCHAR(255) | 文档标题 |
| category | VARCHAR(64) | 分类 |
| source_type | VARCHAR(16) | `file` / `text` |
| file_id | BIGINT | FK→files.id（可空） |
| status | VARCHAR(20) | `indexing/indexed/failed` |
| chunk_count | INT | 切片数量 |
| permission_scope | VARCHAR(16) | `public/dept/role` |
| allowed_roles | JSON | 允许访问的角色编码（`role` 级） |
| organization_id | BIGINT | 部门范围（`dept` 级） |
| embedding_model | VARCHAR(64) | 向量模型（维度需一致） |
| index_error | VARCHAR(512) | 索引失败原因 |
| indexed_at | DATETIME | 索引完成时间 |

索引：`ix(status)`、`ix(category)`、`ix(organization_id)`。

### 6.18 `knowledge_chunks` 知识切片

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| doc_id | BIGINT | FK→knowledge_docs.id（CASCADE） |
| chunk_index | INT | 切片序号 |
| content | TEXT | 原文 |
| token_count | INT | token 数 |
| milvus_pk | BIGINT | Milvus 主键（回表用） |
| page_no | INT | 页码（引用溯源） |

索引：`UNIQUE(doc_id, chunk_index)`、`ix(milvus_pk)`。

### 6.19 `conversations` AI 会话

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| user_id | BIGINT | FK→users.id（CASCADE） |
| title | VARCHAR(128) | 会话标题 |
| agent_type | VARCHAR(32) | `assistant/resume/talent/matching/assessment/training` |
| status | VARCHAR(16) | `active/archived` |
| message_count | INT | 消息数 |
| last_message_at | DATETIME | 最后消息时间 |

索引：`ix(user_id, last_message_at)`。

### 6.20 `chat_messages` 会话消息

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| conversation_id | BIGINT | FK→conversations.id（CASCADE） |
| role | VARCHAR(16) | `user/assistant/system/tool` |
| content | TEXT | 内容 |
| tool_calls | JSON | 工具调用记录 |
| citations | JSON | RAG 引用（doc_id/chunk/page/score） |
| prompt_tokens / completion_tokens | INT | token 统计 |
| model_name | VARCHAR(64) | 模型 |
| elapsed_ms | INT | 耗时 |

索引：`ix(conversation_id, created_at)`。

### 6.21 `audit_logs` 操作审计

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT | 主键 |
| user_id | BIGINT | 操作人（逻辑 FK） |
| username | VARCHAR(64) | 冗余用户名（防用户改名后无法追溯） |
| action | VARCHAR(16) | `create/update/delete/login/export/ai_call` |
| module | VARCHAR(32) | `talent/position/...` |
| target_type / target_id | VARCHAR(32) / BIGINT | 操作对象 |
| request_method / request_path | VARCHAR(8) / VARCHAR(255) | 请求信息 |
| request_ip | VARCHAR(64) | 客户端 IP |
| user_agent | VARCHAR(255) | UA |
| trace_id | VARCHAR(64) | 链路 ID |
| before_data / after_data | JSON | 变更前后（敏感字段脱敏） |
| status | VARCHAR(16) | `success/fail` |
| error_message | VARCHAR(512) | 失败原因 |
| created_at | DATETIME | 操作时间（索引，按月归档） |

索引：`ix(user_id, created_at)`、`ix(module, action)`、`ix(trace_id)`、`ix(created_at)`。

---

<a id="db-pgsql"></a>

## 七、分析库 PgSQL 设计

> 库名 `ai_talent_analytics`，schema `public`。只做「分析/时序」，不做业务事务。
>
> **实现状态**：6 张表已由 `app/models/analytics.py` 定义，执行 `python scripts/init_pgsql.py` 可建库、建 schema、建表并创建 DEFAULT 分区；`--check` 只读检查结构，`--print-ddl` 离线输出 DDL。当前尚未实现 MySQL → PgSQL 同步，也未实现按月/按天滚动子分区维护。

| 表 | 说明 | 分区 |
| --- | --- | --- |
| `fact_talent_snapshot` | 人才快照（按月）：`snapshot_month, organization_id, education, age_group, talent_count, avg_work_years` | `RANGE(snapshot_month)` |
| `fact_assessment_history` | 测评历史明细：`assessment_id, talent_id, position_id, total_score, level, dimension_scores JSONB, scored_at` | `RANGE(scored_at)` 按月 |
| `fact_training_track` | 培训轨迹：`talent_id, course_id, plan_id, progress, study_minutes, status, event_at` | `RANGE(event_at)` |
| `fact_behavior_event` | 行为时序：`talent_id, event_type, page, duration_ms, event_at` | `RANGE(event_at)` 按天 |
| `dim_organization` | 部门维表（从 MySQL 同步）：`org_id, code, name, level, path, parent_id` | — |
| `dim_talent_wide` | 人才分析宽表：`talent_id, name, org_path, education, work_years, skill_tags TEXT[], potential_level, match_avg_score` | — |

设计要点：

1. 宽表 `dim_talent_wide` 供 Vanna NL2SQL 直接查询，避免复杂 JOIN；
2. 时序表统一 `fact_` 前缀 + 时间分区，索引 `(talent_id, event_at)`；
3. 同步方式：MySQL → PgSQL 由定时任务/ETL（`scripts/sync_analytics.py` 待实现）增量同步；
4. 只读账号：NL2SQL 使用专用只读账号，且仅授权 `public` 下的 `fact_*`/`dim_*`。

---

<a id="db-milvus"></a>

## 八、Milvus 向量集合设计

| 集合名 | 维度 | 主键 | 标量字段（用于过滤） | 用途 |
| --- | --- | --- | --- | --- |
| `talent_vectors` | 与 embedding 模型一致（如 1024） | `talent_id`（int64） | `organization_id`、`status`、`skill_tags`(JSON) | 人才语义检索、简历查重 |
| `position_vectors` | 同上 | `position_id` | `organization_id`、`status`、`level` | 岗位语义匹配 |
| `knowledge_vectors` | 同上 | `chunk_id`（int64，对应 `knowledge_chunks.id`） | `doc_id`、`organization_id`、`allowed_roles`(JSON)、`category` | RAG 检索（带权限过滤） |
| `question_vectors` | 同上 | `question_id` | `dimension`、`difficulty` | 相似题去重 / 智能抽题 |

索引：`IVF_FLAT`（或 `HNSW`），度量 `IP`/`COSINE`；`nlist` 1024、查询 `nprobe` 16 起调。
一致性：`milvus_pk`/`talent_id` 与关系库主键一一对应；删除文档时同步删除向量；检索后按 `id` 回表取原文。
权限：RAG 检索时 `expr` 必须带上 `allowed_roles` / `organization_id` 过滤，**在检索阶段**完成，防止越权召回。

---

<a id="db-redis"></a>

## 九、Redis Key 设计

| Key | 类型 | TTL | 用途 |
| --- | --- | --- | --- |
| `user:token:blacklist:{jti}` | string | 至 token 过期 | 强制下线（可选） |
| `user:info:{user_id}` | hash | 300s | 当前用户信息缓存（减少每请求查库） |
| `org:tree` | string(JSON) | 600s | 组织树缓存，组织变更时清理 |
| `chat:mem:{conversation_id}` | list | 3600s | 多轮对话短期记忆（最近 10 轮） |
| `chat:summary:{conversation_id}` | string | 3600s | 长对话摘要（控制上下文长度） |
| `quota:token:{user_id}:{date}` | string(int) | 当天 | 每日 token 配额计数 |
| `ratelimit:{ip}:{path}` | string(int) | 60s | 接口限流（多实例必须用 Redis） |
| `lock:{resource}:{id}` | string | 30s | 分布式锁（`SET NX PX`） |
| `cache:matching:{batch_id}` | string(JSON) | 1800s | 匹配结果缓存 |
| `embedding:{sha256(text)}` | string(JSON) | 86400s | 文本向量缓存（省重复计费） |

规范：`业务:对象:标识[:维度]`；所有 key 必须设置 TTL；禁止用 `KEYS *`（用 `SCAN`）。

---

<a id="db-ddl"></a>

## 十、完整 DDL（MySQL 8）

> 与 ORM 生成的 DDL 一致（已用 MySQL 方言编译校验）。生产请用 Alembic 生成迁移，以下用于建库评审与 DBA 交付。

```sql
-- ============================================================
-- 建库
-- ============================================================
CREATE DATABASE IF NOT EXISTS `ai_talent_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE `ai_talent_platform`;

-- ============================================================
-- 1. 组织架构
-- ============================================================
CREATE TABLE `organizations` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `parent_id`     BIGINT       NULL COMMENT '上级部门 ID，NULL 表示根节点',
  `code`          VARCHAR(64)  NOT NULL COMMENT '部门编码',
  `name`          VARCHAR(128) NOT NULL COMMENT '部门名称',
  `level`         INT          NOT NULL DEFAULT 1 COMMENT '层级，根为 1',
  `path`          VARCHAR(512) NOT NULL DEFAULT '/' COMMENT '祖级路径，如 /1/3/7/',
  `leader_id`     BIGINT       NULL COMMENT '部门负责人用户 ID（逻辑外键 -> users.id）',
  `sort`          INT          NOT NULL DEFAULT 0 COMMENT '排序号，越小越靠前',
  `is_active`     TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否启用',
  `remark`        VARCHAR(255) NULL COMMENT '备注',
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `is_deleted`    TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已删除（逻辑删除）',
  `deleted_at`    DATETIME     NULL COMMENT '删除时间',
  `created_by`    BIGINT       NULL COMMENT '创建人用户 ID',
  `updated_by`    BIGINT       NULL COMMENT '最后修改人用户 ID',
  `version`       INT          NOT NULL DEFAULT 1 COMMENT '乐观锁版本号',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_organizations_code` (`code`),
  UNIQUE KEY `uq_organizations_parent_name` (`parent_id`, `name`),
  KEY `ix_organizations_parent_id` (`parent_id`),
  KEY `ix_organizations_path` (`path`),
  KEY `ix_organizations_name` (`name`),
  KEY `ix_organizations_leader_id` (`leader_id`),
  KEY `ix_organizations_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_organizations_parent_id_organizations` FOREIGN KEY (`parent_id`)
    REFERENCES `organizations` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='组织架构表';

-- ============================================================
-- 2. 角色
-- ============================================================
CREATE TABLE `roles` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '角色 ID',
  `code`        VARCHAR(64)  NOT NULL COMMENT '角色编码：super_admin/admin/hr/manager/employee',
  `name`        VARCHAR(64)  NOT NULL COMMENT '角色名称',
  `description` VARCHAR(255) NULL COMMENT '角色描述',
  `is_active`   TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_roles_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='角色表';

-- ============================================================
-- 3. 用户
-- ============================================================
CREATE TABLE `users` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `username`        VARCHAR(64)  NOT NULL COMMENT '登录名',
  `password_hash`   VARCHAR(128) NOT NULL COMMENT '密码哈希（bcrypt）',
  `real_name`       VARCHAR(64)  NULL COMMENT '真实姓名',
  `mobile`          VARCHAR(20)  NULL COMMENT '手机号',
  `email`           VARCHAR(128) NULL COMMENT '邮箱',
  `avatar`          VARCHAR(255) NULL COMMENT '头像地址',
  `organization_id` BIGINT       NULL COMMENT '所属部门 ID',
  `position_title`  VARCHAR(64)  NULL COMMENT '岗位名称/职务',
  `is_active`       TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否启用',
  `is_superuser`    TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否超管',
  `last_login_at`   DATETIME     NULL COMMENT '最后登录时间',
  `last_login_ip`   VARCHAR(64)  NULL COMMENT '最后登录 IP',
  `fail_count`      INT          NOT NULL DEFAULT 0 COMMENT '连续登录失败次数',
  `locked_until`    DATETIME     NULL COMMENT '锁定截止时间',
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `is_deleted`      TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已删除（逻辑删除）',
  `deleted_at`      DATETIME     NULL COMMENT '删除时间',
  `created_by`      BIGINT       NULL COMMENT '创建人用户 ID',
  `updated_by`      BIGINT       NULL COMMENT '最后修改人用户 ID',
  `version`         INT          NOT NULL DEFAULT 1 COMMENT '乐观锁版本号',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_username` (`username`),
  UNIQUE KEY `uq_users_mobile` (`mobile`),
  UNIQUE KEY `uq_users_email` (`email`),
  KEY `ix_users_organization_id` (`organization_id`),
  KEY `ix_users_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_users_organization_id_organizations` FOREIGN KEY (`organization_id`)
    REFERENCES `organizations` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户账号表';

-- ============================================================
-- 4. 用户角色关联
-- ============================================================
CREATE TABLE `user_roles` (
  `user_id`    BIGINT   NOT NULL COMMENT '用户 ID',
  `role_id`    BIGINT   NOT NULL COMMENT '角色 ID',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '绑定时间',
  PRIMARY KEY (`user_id`, `role_id`),
  CONSTRAINT `fk_user_roles_user_id_users` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_user_roles_role_id_roles` FOREIGN KEY (`role_id`)
    REFERENCES `roles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户角色关联表';
```

### 10.1 业务表 DDL（待实现，建表时照抄）

```sql
-- 5. 岗位 -----------------------------------------------------------------
CREATE TABLE `positions` (
  `id`              BIGINT        NOT NULL AUTO_INCREMENT,
  `title`           VARCHAR(128)  NOT NULL COMMENT '岗位名称',
  `code`            VARCHAR(64)   NOT NULL COMMENT '岗位编码',
  `organization_id` BIGINT        NOT NULL COMMENT '所属部门',
  `level`           VARCHAR(20)   NOT NULL DEFAULT 'middle' COMMENT 'junior/middle/senior/expert',
  `headcount`       INT           NOT NULL DEFAULT 1 COMMENT '编制人数',
  `status`          VARCHAR(20)   NOT NULL DEFAULT 'open' COMMENT 'open/paused/closed',
  `description`     TEXT          NULL COMMENT '岗位说明书全文',
  `requirements`    JSON          NULL COMMENT '结构化要求',
  `skill_tags`      JSON          NULL COMMENT '技能标签',
  `salary_min`      DECIMAL(12,2) NULL COMMENT '薪资下限',
  `salary_max`      DECIMAL(12,2) NULL COMMENT '薪资上限',
  `publish_at`      DATETIME      NULL COMMENT '发布时间',
  `close_at`        DATETIME      NULL COMMENT '关闭时间',
  `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`      TINYINT(1)    NOT NULL DEFAULT 0,
  `deleted_at`      DATETIME      NULL,
  `created_by`      BIGINT        NULL,
  `updated_by`      BIGINT        NULL,
  `version`         INT           NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_positions_code` (`code`),
  KEY `ix_positions_org_status` (`organization_id`, `status`),
  KEY `ix_positions_level` (`level`),
  KEY `ix_positions_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_positions_organization_id_organizations` FOREIGN KEY (`organization_id`)
    REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='岗位表';

-- 6. 人才档案 --------------------------------------------------------------
CREATE TABLE `talents` (
  `id`               BIGINT       NOT NULL AUTO_INCREMENT,
  `name`             VARCHAR(64)  NOT NULL COMMENT '姓名',
  `gender`           VARCHAR(16)  NULL DEFAULT 'unknown' COMMENT 'male/female/unknown',
  `birth_date`       DATE         NULL,
  `mobile`           VARCHAR(20)  NULL COMMENT '手机号',
  `email`            VARCHAR(128) NULL,
  `id_card`          VARCHAR(128) NULL COMMENT '身份证（加密）',
  `education`        VARCHAR(20)  NULL COMMENT '最高学历',
  `school`           VARCHAR(128) NULL,
  `major`            VARCHAR(128) NULL,
  `work_years`       INT          NOT NULL DEFAULT 0,
  `current_company`  VARCHAR(128) NULL,
  `current_position` VARCHAR(128) NULL,
  `organization_id`  BIGINT       NULL,
  `skill_tags`       JSON         NULL,
  `certificates`     JSON         NULL,
  `self_evaluation`  TEXT         NULL,
  `status`           VARCHAR(20)  NOT NULL DEFAULT 'active' COMMENT 'active/left/reserve',
  `potential_level`  VARCHAR(8)   NULL COMMENT 'A/B/C/D',
  `portrait_summary` VARCHAR(512) NULL,
  `source`           VARCHAR(20)  NOT NULL DEFAULT 'manual' COMMENT 'manual/ai_resume',
  `resume_id`        BIGINT       NULL,
  `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`       TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`       DATETIME     NULL,
  `created_by`       BIGINT       NULL,
  `updated_by`       BIGINT       NULL,
  `version`          INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_talents_mobile_deleted` (`mobile`, `is_deleted`),
  KEY `ix_talents_org_status` (`organization_id`, `status`),
  KEY `ix_talents_name` (`name`),
  KEY `ix_talents_work_years` (`work_years`),
  KEY `ix_talents_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_talents_organization_id_organizations` FOREIGN KEY (`organization_id`)
    REFERENCES `organizations` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='人才档案表';

-- 7. 简历 ----------------------------------------------------------------
CREATE TABLE `resumes` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `talent_id`   BIGINT       NULL,
  `file_id`     BIGINT       NULL,
  `file_name`   VARCHAR(255) NOT NULL,
  `file_type`   VARCHAR(32)  NOT NULL,
  `file_size`   BIGINT       NOT NULL DEFAULT 0,
  `file_hash`   CHAR(64)     NOT NULL COMMENT 'sha256',
  `status`      VARCHAR(20)  NOT NULL DEFAULT 'uploaded' COMMENT 'uploaded/parsing/success/failed',
  `accuracy`    DECIMAL(4,3) NULL COMMENT '解析置信度',
  `parsed_data` JSON         NULL,
  `parse_error` VARCHAR(512) NULL,
  `parsed_at`   DATETIME     NULL,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`  TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`  DATETIME     NULL,
  `created_by`  BIGINT       NULL,
  `updated_by`  BIGINT       NULL,
  `version`     INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_resumes_file_hash` (`file_hash`),
  KEY `ix_resumes_talent_id` (`talent_id`),
  KEY `ix_resumes_status` (`status`),
  CONSTRAINT `fk_resumes_talent_id_talents` FOREIGN KEY (`talent_id`)
    REFERENCES `talents` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='简历表';

-- 8. 文件 ----------------------------------------------------------------
CREATE TABLE `files` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `file_name`      VARCHAR(255) NOT NULL COMMENT '原始文件名',
  `stored_name`    VARCHAR(128) NOT NULL COMMENT '存储名 uuid.ext',
  `file_path`      VARCHAR(512) NOT NULL COMMENT '相对路径',
  `file_size`      BIGINT       NOT NULL DEFAULT 0,
  `content_type`   VARCHAR(128) NULL,
  `extension`      VARCHAR(16)  NULL,
  `file_hash`      CHAR(64)     NULL COMMENT 'sha256（秒传）',
  `storage_type`   VARCHAR(16)  NOT NULL DEFAULT 'local',
  `biz_type`       VARCHAR(32)  NOT NULL COMMENT 'resume/knowledge/avatar/export',
  `biz_id`         BIGINT       NULL,
  `uploader_id`    BIGINT       NULL,
  `is_public`      TINYINT(1)   NOT NULL DEFAULT 0,
  `download_count` INT          NOT NULL DEFAULT 0,
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`     TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`     DATETIME     NULL,
  `created_by`     BIGINT       NULL,
  `updated_by`     BIGINT       NULL,
  `version`        INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `ix_files_file_hash` (`file_hash`),
  KEY `ix_files_biz` (`biz_type`, `biz_id`),
  KEY `ix_files_uploader_id` (`uploader_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='文件元数据表';

-- 9. 测评相关 -------------------------------------------------------------
CREATE TABLE `assessments` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `talent_id`      BIGINT       NOT NULL,
  `position_id`    BIGINT       NULL,
  `title`          VARCHAR(128) NOT NULL,
  `status`         VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/in_progress/submitted/scored',
  `question_count` INT          NOT NULL DEFAULT 0,
  `deadline`       DATETIME     NULL,
  `started_at`     DATETIME     NULL,
  `submitted_at`   DATETIME     NULL,
  `total_score`    DECIMAL(5,2) NULL,
  `created_by_ai`  TINYINT(1)   NOT NULL DEFAULT 0,
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`     TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`     DATETIME     NULL,
  `created_by`     BIGINT       NULL,
  `updated_by`     BIGINT       NULL,
  `version`        INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `ix_assessments_talent_status` (`talent_id`, `status`),
  KEY `ix_assessments_deadline` (`deadline`),
  CONSTRAINT `fk_assessments_talent_id_talents` FOREIGN KEY (`talent_id`)
    REFERENCES `talents` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='测评任务表';

CREATE TABLE `assessment_questions` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT,
  `assessment_id` BIGINT       NOT NULL,
  `type`          VARCHAR(16)  NOT NULL COMMENT 'single/multiple/text',
  `content`       TEXT         NOT NULL,
  `options`       JSON         NULL,
  `answer`        JSON         NULL COMMENT '标准答案（不返回前端）',
  `score`         DECIMAL(5,2) NOT NULL DEFAULT 0,
  `dimension`     VARCHAR(32)  NULL COMMENT '考察维度',
  `sort`          INT          NOT NULL DEFAULT 0,
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_assessment_questions_assessment_sort` (`assessment_id`, `sort`),
  CONSTRAINT `fk_assessment_questions_assessment_id_assessments` FOREIGN KEY (`assessment_id`)
    REFERENCES `assessments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='测评题目表';

CREATE TABLE `assessment_answers` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT,
  `assessment_id` BIGINT       NOT NULL,
  `question_id`   BIGINT       NOT NULL,
  `answer`        JSON         NULL,
  `score`         DECIMAL(5,2) NULL,
  `scored_by`     VARCHAR(16)  NULL COMMENT 'rule/ai',
  `scored_at`     DATETIME     NULL,
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_assessment_answers_aid_qid` (`assessment_id`, `question_id`),
  CONSTRAINT `fk_assessment_answers_assessment_id_assessments` FOREIGN KEY (`assessment_id`)
    REFERENCES `assessments` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_assessment_answers_question_id_questions` FOREIGN KEY (`question_id`)
    REFERENCES `assessment_questions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='答题记录表';

CREATE TABLE `assessment_results` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT,
  `assessment_id`   BIGINT       NOT NULL,
  `total_score`     DECIMAL(5,2) NOT NULL DEFAULT 0,
  `dimension_scores` JSON        NULL,
  `level`           VARCHAR(16)  NULL COMMENT '初级/中级/高级/专家',
  `report`          JSON         NULL,
  `suggestions`     JSON         NULL,
  `model_name`      VARCHAR(64)  NULL,
  `prompt_version`  VARCHAR(32)  NULL,
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_assessment_results_assessment_id` (`assessment_id`),
  KEY `ix_assessment_results_level` (`level`),
  CONSTRAINT `fk_assessment_results_assessment_id_assessments` FOREIGN KEY (`assessment_id`)
    REFERENCES `assessments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='测评结果表';

-- 10. 培训相关 -------------------------------------------------------------
CREATE TABLE `courses` (
  `id`             BIGINT        NOT NULL AUTO_INCREMENT,
  `title`          VARCHAR(128)  NOT NULL,
  `category`       VARCHAR(32)   NULL,
  `level`          VARCHAR(20)   NULL,
  `duration_hours` DECIMAL(6,2)  NOT NULL DEFAULT 0,
  `resource_url`   VARCHAR(512)  NULL,
  `cover`          VARCHAR(255)  NULL,
  `description`    TEXT          NULL,
  `skill_tags`     JSON          NULL,
  `is_active`      TINYINT(1)    NOT NULL DEFAULT 1,
  `created_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`     TINYINT(1)    NOT NULL DEFAULT 0,
  `deleted_at`     DATETIME      NULL,
  `created_by`     BIGINT        NULL,
  `updated_by`     BIGINT        NULL,
  `version`        INT           NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `ix_courses_category` (`category`),
  KEY `ix_courses_level` (`level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='课程表';

CREATE TABLE `training_plans` (
  `id`                   BIGINT       NOT NULL AUTO_INCREMENT,
  `name`                 VARCHAR(128) NOT NULL,
  `talent_id`            BIGINT       NOT NULL,
  `goal`                 VARCHAR(512) NULL,
  `start_date`           DATE         NULL,
  `end_date`             DATE         NULL,
  `status`               VARCHAR(20)  NOT NULL DEFAULT 'draft' COMMENT 'draft/ongoing/finished/cancelled',
  `created_by_ai`        TINYINT(1)   NOT NULL DEFAULT 0,
  `source_assessment_id` BIGINT       NULL,
  `created_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`           TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`           DATETIME     NULL,
  `created_by`           BIGINT       NULL,
  `updated_by`           BIGINT       NULL,
  `version`              INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `ix_training_plans_talent_status` (`talent_id`, `status`),
  CONSTRAINT `fk_training_plans_talent_id_talents` FOREIGN KEY (`talent_id`)
    REFERENCES `talents` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='培训计划表';

CREATE TABLE `plan_courses` (
  `plan_id`      BIGINT     NOT NULL,
  `course_id`    BIGINT     NOT NULL,
  `sort`         INT        NOT NULL DEFAULT 0,
  `require_pass` TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (`plan_id`, `course_id`),
  CONSTRAINT `fk_plan_courses_plan_id_training_plans` FOREIGN KEY (`plan_id`)
    REFERENCES `training_plans` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_plan_courses_course_id_courses` FOREIGN KEY (`course_id`)
    REFERENCES `courses` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='培训计划课程关联表';

CREATE TABLE `learning_records` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `plan_id`        BIGINT       NOT NULL,
  `course_id`      BIGINT       NOT NULL,
  `talent_id`      BIGINT       NOT NULL,
  `progress`       INT          NOT NULL DEFAULT 0 COMMENT '0-100（单调递增）',
  `study_minutes`  INT          NOT NULL DEFAULT 0,
  `last_study_at`  DATETIME     NULL,
  `status`         VARCHAR(20)  NOT NULL DEFAULT 'not_started' COMMENT 'not_started/learning/completed',
  `score`          DECIMAL(5,2) NULL,
  `passed`         TINYINT(1)   NOT NULL DEFAULT 0,
  `suspicious`     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '反作弊标记',
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_learning_records_plan_course_talent` (`plan_id`, `course_id`, `talent_id`),
  KEY `ix_learning_records_talent_status` (`talent_id`, `status`),
  CONSTRAINT `fk_learning_records_plan_id_training_plans` FOREIGN KEY (`plan_id`)
    REFERENCES `training_plans` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='学习记录表';

-- 11. 匹配记录 -------------------------------------------------------------
CREATE TABLE `matching_records` (
  `id`               BIGINT       NOT NULL AUTO_INCREMENT,
  `batch_id`         CHAR(32)     NOT NULL COMMENT '跑批 ID',
  `position_id`      BIGINT       NOT NULL,
  `talent_id`        BIGINT       NOT NULL,
  `score`            DECIMAL(5,2) NOT NULL DEFAULT 0,
  `dimension_scores` JSON         NULL,
  `highlights`       JSON         NULL,
  `gaps`             JSON         NULL,
  `suggestion`       VARCHAR(512) NULL,
  `strategy`         VARCHAR(16)  NOT NULL DEFAULT 'hybrid' COMMENT 'hard/soft/hybrid',
  `model_name`       VARCHAR(64)  NULL,
  `matched_by`       BIGINT       NULL,
  `matched_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`, `matched_at`),
  UNIQUE KEY `uq_matching_batch_position_talent` (`batch_id`, `position_id`, `talent_id`),
  KEY `ix_matching_position_score` (`position_id`, `score`),
  KEY `ix_matching_talent_id` (`talent_id`),
  KEY `ix_matching_matched_at` (`matched_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='人岗匹配记录表'
PARTITION BY RANGE (TO_DAYS(`matched_at`)) (
  PARTITION p202611 VALUES LESS THAN (TO_DAYS('2026-12-01')),
  PARTITION p202612 VALUES LESS THAN (TO_DAYS('2027-01-01')),
  PARTITION pmax    VALUES LESS THAN (MAXVALUE)
);

-- 12. 知识库 --------------------------------------------------------------
CREATE TABLE `knowledge_docs` (
  `id`               BIGINT       NOT NULL AUTO_INCREMENT,
  `title`            VARCHAR(255) NOT NULL,
  `category`         VARCHAR(64)  NULL,
  `source_type`      VARCHAR(16)  NOT NULL DEFAULT 'file' COMMENT 'file/text',
  `file_id`          BIGINT       NULL,
  `status`           VARCHAR(20)  NOT NULL DEFAULT 'indexing' COMMENT 'indexing/indexed/failed',
  `chunk_count`      INT          NOT NULL DEFAULT 0,
  `permission_scope` VARCHAR(16)  NOT NULL DEFAULT 'public' COMMENT 'public/dept/role',
  `allowed_roles`    JSON         NULL,
  `organization_id`  BIGINT       NULL,
  `embedding_model`  VARCHAR(64)  NULL,
  `index_error`      VARCHAR(512) NULL,
  `indexed_at`       DATETIME     NULL,
  `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted`       TINYINT(1)   NOT NULL DEFAULT 0,
  `deleted_at`       DATETIME     NULL,
  `created_by`       BIGINT       NULL,
  `updated_by`       BIGINT       NULL,
  `version`          INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `ix_knowledge_docs_status` (`status`),
  KEY `ix_knowledge_docs_category` (`category`),
  KEY `ix_knowledge_docs_org` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='知识文档表';

CREATE TABLE `knowledge_chunks` (
  `id`          BIGINT NOT NULL AUTO_INCREMENT,
  `doc_id`      BIGINT NOT NULL,
  `chunk_index` INT    NOT NULL DEFAULT 0,
  `content`     TEXT   NOT NULL,
  `token_count` INT    NOT NULL DEFAULT 0,
  `milvus_pk`   BIGINT NULL COMMENT 'Milvus 主键',
  `page_no`     INT    NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_knowledge_chunks_doc_index` (`doc_id`, `chunk_index`),
  KEY `ix_knowledge_chunks_milvus_pk` (`milvus_pk`),
  CONSTRAINT `fk_knowledge_chunks_doc_id_docs` FOREIGN KEY (`doc_id`)
    REFERENCES `knowledge_docs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='知识切片表';

-- 13. AI 会话 -------------------------------------------------------------
CREATE TABLE `conversations` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT,
  `user_id`         BIGINT       NOT NULL,
  `title`           VARCHAR(128) NULL,
  `agent_type`      VARCHAR(32)  NOT NULL DEFAULT 'assistant',
  `status`          VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active/archived',
  `message_count`   INT          NOT NULL DEFAULT 0,
  `last_message_at` DATETIME     NULL,
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_conversations_user_last` (`user_id`, `last_message_at`),
  CONSTRAINT `fk_conversations_user_id_users` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AI 会话表';

CREATE TABLE `chat_messages` (
  `id`                BIGINT      NOT NULL AUTO_INCREMENT,
  `conversation_id`   BIGINT      NOT NULL,
  `role`              VARCHAR(16) NOT NULL COMMENT 'user/assistant/system/tool',
  `content`           TEXT        NULL,
  `tool_calls`        JSON        NULL,
  `citations`         JSON        NULL,
  `prompt_tokens`     INT         NOT NULL DEFAULT 0,
  `completion_tokens` INT         NOT NULL DEFAULT 0,
  `model_name`        VARCHAR(64) NULL,
  `elapsed_ms`        INT         NULL,
  `created_at`        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_chat_messages_conv_created` (`conversation_id`, `created_at`),
  CONSTRAINT `fk_chat_messages_conversation_id_conversations` FOREIGN KEY (`conversation_id`)
    REFERENCES `conversations` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='会话消息表';

-- 15. 人才画像（版本化，可由主档重建）---------------------------------------
CREATE TABLE `talent_portraits` (
  `id`                  BIGINT       NOT NULL AUTO_INCREMENT,
  `talent_id`           BIGINT       NOT NULL,
  `version`             INT          NOT NULL DEFAULT 1 COMMENT '画像版本',
  `dimensions`          JSON         NULL COMMENT '{skill,experience,quality,potential}',
  `labels`              JSON         NULL COMMENT 'AI 标签',
  `strengths`           JSON         NULL,
  `weaknesses`          JSON         NULL,
  `suggested_positions` JSON         NULL,
  `potential_level`     VARCHAR(8)   NULL COMMENT 'A/B/C/D',
  `model_name`          VARCHAR(64)  NULL,
  `prompt_version`      VARCHAR(32)  NULL,
  `generated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_talent_portraits_talent_version` (`talent_id`, `version`),
  KEY `ix_talent_portraits_generated_at` (`generated_at`),
  CONSTRAINT `fk_talent_portraits_talent_id_talents` FOREIGN KEY (`talent_id`)
    REFERENCES `talents` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='人才画像表';

-- 16. 岗位能力模型（多版本）------------------------------------------------
CREATE TABLE `position_requirements` (
  `id`          BIGINT      NOT NULL AUTO_INCREMENT,
  `position_id` BIGINT      NOT NULL,
  `version`     INT         NOT NULL DEFAULT 1,
  `dimensions`  JSON        NULL COMMENT '维度定义',
  `weights`     JSON        NULL COMMENT '维度权重（合计 1.0）',
  `source`      VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT 'manual/ai_parse',
  `created_at`  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_position_requirements_position_version` (`position_id`, `version`),
  CONSTRAINT `fk_position_requirements_position_id_positions` FOREIGN KEY (`position_id`)
    REFERENCES `positions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='岗位能力模型表';

-- 17. 简历工作经历（可选拆表，便于统计分析）--------------------------------
CREATE TABLE `resume_experiences` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `resume_id`   BIGINT       NOT NULL,
  `company`     VARCHAR(128) NOT NULL,
  `title`       VARCHAR(128) NULL,
  `start_date`  DATE         NULL,
  `end_date`    DATE         NULL COMMENT '为空表示至今',
  `description` TEXT         NULL,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_resume_experiences_resume_id` (`resume_id`),
  KEY `ix_resume_experiences_company` (`company`),
  CONSTRAINT `fk_resume_experiences_resume_id_resumes` FOREIGN KEY (`resume_id`)
    REFERENCES `resumes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='简历工作经历表';

-- 14. 审计 ---------------------------------------------------------------
CREATE TABLE `audit_logs` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `user_id`        BIGINT       NULL,
  `username`       VARCHAR(64)  NULL,
  `action`         VARCHAR(16)  NOT NULL COMMENT 'create/update/delete/login/export/ai_call',
  `module`         VARCHAR(32)  NULL,
  `target_type`    VARCHAR(32)  NULL,
  `target_id`      BIGINT       NULL,
  `request_method` VARCHAR(8)   NULL,
  `request_path`   VARCHAR(255) NULL,
  `request_ip`     VARCHAR(64)  NULL,
  `user_agent`     VARCHAR(255) NULL,
  `trace_id`       VARCHAR(64)  NULL,
  `before_data`    JSON         NULL,
  `after_data`     JSON         NULL,
  `status`         VARCHAR(16)  NOT NULL DEFAULT 'success' COMMENT 'success/fail',
  `error_message`  VARCHAR(512) NULL,
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_audit_user_created` (`user_id`, `created_at`),
  KEY `ix_audit_module_action` (`module`, `action`),
  KEY `ix_audit_trace_id` (`trace_id`),
  KEY `ix_audit_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='操作审计表';
```

> `matching_records` 使用了分区表：**分区表要求主键包含分区键**，因此示例主键为 `(id, matched_at)`；
> 若团队暂不启用分区，改为 `PRIMARY KEY (id)` 即可（ORM 侧无需改动）。

---

<a id="db-enums"></a>

## 十一、数据字典：枚举值

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `roles.code` | `super_admin` / `admin` / `hr` / `manager` / `employee` | 超级管理员 / 系统管理员 / 人力资源 / 部门管理者 / 普通员工 |
| `users.is_active` | 1 / 0 | 启用 / 禁用 |
| `organizations.level` | 1..N | 层级，根为 1 |
| `positions.level` | `junior` / `middle` / `senior` / `expert` | 初级 / 中级 / 高级 / 专家 |
| `positions.status` | `open` / `paused` / `closed` | 在招 / 暂停 / 关闭 |
| `talents.gender` | `male` / `female` / `unknown` | 男 / 女 / 未知 |
| `talents.education` | `高中及以下` / `大专` / `本科` / `硕士` / `博士` | 最高学历 |
| `talents.status` | `active` / `left` / `reserve` | 在职 / 离职 / 储备 |
| `talents.potential_level` | `A` / `B` / `C` / `D` | 高潜 / 中上 / 一般 / 待观察 |
| `talents.source` | `manual` / `ai_resume` | 手工录入 / AI 解析简历 |
| `resumes.status` | `uploaded` / `parsing` / `success` / `failed` | 已上传 / 解析中 / 成功 / 失败 |
| `assessments.status` | `pending` / `in_progress` / `submitted` / `scored` | 待开始 / 进行中 / 已提交 / 已评分 |
| `assessment_questions.type` | `single` / `multiple` / `text` | 单选 / 多选 / 问答 |
| `assessment_results.level` | `初级` / `中级` / `高级` / `专家` | 能力等级 |
| `training_plans.status` | `draft` / `ongoing` / `finished` / `cancelled` | 草稿 / 进行中 / 已完成 / 已取消 |
| `learning_records.status` | `not_started` / `learning` / `completed` | 未开始 / 学习中 / 已完成 |
| `matching_records.strategy` | `hard` / `soft` / `hybrid` | 硬性过滤 / 向量软匹配 / 混合 |
| `knowledge_docs.status` | `indexing` / `indexed` / `failed` | 索引中 / 已索引 / 失败 |
| `knowledge_docs.permission_scope` | `public` / `dept` / `role` | 公开 / 部门 / 指定角色 |
| `files.biz_type` | `resume` / `knowledge` / `avatar` / `export` | 简历 / 知识库 / 头像 / 导出 |
| `audit_logs.action` | `create` / `update` / `delete` / `login` / `export` / `ai_call` | 操作类型 |

---

<a id="db-index"></a>

## 十二、索引与性能建议

1. **联合索引顺序**：等值条件在前、范围条件在后；高频列表查询按「部门 + 状态 + 时间」建联合索引；
2. **避免低区分度单列索引**：`is_deleted` 单独索引收益有限，建议与业务字段组合（如 `(organization_id, is_deleted)`）；
3. **JSON 字段**：MySQL 8 可对 `skill_tags` 建多值索引
   `ALTER TABLE talents ADD INDEX idx_skill_tags ((CAST(skill_tags AS CHAR(64) ARRAY)));`（8.0.17+），
   高频技能检索优先用它，而不是 `LIKE '%Python%'`；
4. **大表**：`matching_records`、`audit_logs`、`chat_messages` 按月分区或归档，历史数据迁 PgSQL；
5. **统计**：所有聚合用 SQL `GROUP BY`/`COUNT`，禁止在 Python 循环统计；
6. **N+1**：列表接口用 `IN` 批量取关联（如部门名），或 ORM `selectinload`；
7. **分页深翻**：超过 1 万页的深分页改用「游标分页」（`id > last_id ORDER BY id LIMIT n`）；
8. **连接池**：MySQL `pool_size=10 / max_overflow=20 / pool_recycle=3600`；PgSQL `5 / 10 / 1800`；
9. **慢查询**：开启 `slow_query_log`（>1s），结合 `/health/ready` 的 `elapsed_ms` 监控；
10. **热点缓存**：组织树、用户信息、匹配批次结果进 Redis（见第九节），TTL 5-10 分钟。

---

<a id="db-migration"></a>

## 十三、迁移与初始化

### 13.1 初始化（开发 / 首次部署）

```bash
cp .env.example .env        # 填写 DB_* 与 PGSQL_*（默认 root/root、postgres/postgres）
python scripts/init_db.py   # MySQL 建库 -> 建表 -> 写入 5 个内置角色 -> PgSQL 建分析库
python scripts/create_admin.py -u admin -p Admin@12345
python scripts/seed_data.py # 可选：演示部门树 + 测试账号（Test@12345）
```

`init_db.py` 支持的参数：`--tables-only`（仅建表）、`--drop`（先删表，**仅开发环境**）。

### 13.2 表结构变更流程（生产）

1. 修改 `app/models/*.py`；
2. `alembic revision --autogenerate -m "add xxx"`（`alembic/env.py` 使用 `settings.DATABASE_URL_SYNC`）；
3. 人工审查生成的迁移脚本（特别是索引与默认值）；
4. `alembic upgrade head`；
5. 在 `docs/database_design.md` 与 `docs/api_document.md` 对应章节同步更新。

> 生产禁止使用 `Base.metadata.create_all`（`lifespan` 仅在非 prod 环境自动建表）。

### 13.3 备份与安全

| 项目 | 要求 |
| --- | --- |
| 全量备份 | MySQL 每日 `mysqldump --single-transaction`，保留 7 天；PgSQL 每日 `pg_dump` |
| 增量 | binlog / WAL 归档，支持 PITR |
| 敏感字段 | `users.password_hash`（bcrypt 单向）、`talents.id_card`（AES 加密）、手机号响应脱敏 |
| 权限 | 应用账号最小权限（DML only）；NL2SQL 使用独立只读账号 |
| 审计 | 所有写操作与导出、AI 调用写 `audit_logs`，不可修改删除 |
| 合规 | 符合《网络安全法》《数据安全法》《个人信息保护法》：加密存储、访问留痕、可导出溯源 |
