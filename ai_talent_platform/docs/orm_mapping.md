# 全局 ORM 映射关系

> 本文由 `scripts/dump_orm_mapping.py` 从 SQLAlchemy 元数据自动生成，勿手工编辑。

- MySQL 业务主库 `ai_talent_platform`：25 张表 / 413 个字段 / 46 条 ORM 关系
- PgSQL 分析库 `ai_talent_analytics`（schema `public`）：6 张表 / 50 个字段，分区父表 4 张

| 库 | 表名 | ORM 类 | 定义文件 | 字段数 |
| --- | --- | --- | --- | --- |
| ai_talent_platform | `assessment_answers` | `AssessmentAnswer` | `app.models.assessment` | 14 |
| ai_talent_platform | `assessment_questions` | `AssessmentQuestion` | `app.models.assessment` | 16 |
| ai_talent_platform | `assessment_results` | `AssessmentResult` | `app.models.assessment` | 16 |
| ai_talent_platform | `assessments` | `Assessment` | `app.models.assessment` | 18 |
| ai_talent_platform | `audit_logs` | `AuditLog` | `app.models.audit` | 23 |
| ai_talent_platform | `chat_messages` | `ChatMessage` | `app.models.conversation` | 17 |
| ai_talent_platform | `conversations` | `Conversation` | `app.models.conversation` | 14 |
| ai_talent_platform | `courses` | `Course` | `app.models.training` | 17 |
| ai_talent_platform | `files` | `File` | `app.models.file` | 21 |
| ai_talent_platform | `knowledge_chunks` | `KnowledgeChunk` | `app.models.knowledge` | 14 |
| ai_talent_platform | `knowledge_docs` | `KnowledgeDoc` | `app.models.knowledge` | 20 |
| ai_talent_platform | `learning_records` | `LearningRecord` | `app.models.training` | 18 |
| ai_talent_platform | `matching_records` | `MatchingRecord` | `app.models.matching` | 20 |
| ai_talent_platform | `organizations` | `Organization` | `app.models.organization` | 17 |
| ai_talent_platform | `plan_courses` | `PlanCourse` | `app.models.training` | 6 |
| ai_talent_platform | `position_requirements` | `PositionRequirement` | `app.models.position` | 12 |
| ai_talent_platform | `positions` | `Position` | `app.models.position` | 21 |
| ai_talent_platform | `resume_experiences` | `ResumeExperience` | `app.models.resume` | 15 |
| ai_talent_platform | `resumes` | `Resume` | `app.models.resume` | 19 |
| ai_talent_platform | `roles` | `Role` | `app.models.role` | 7 |
| ai_talent_platform | `talent_portraits` | `TalentPortrait` | `app.models.talent` | 18 |
| ai_talent_platform | `talents` | `Talent` | `app.models.talent` | 29 |
| ai_talent_platform | `training_plans` | `TrainingPlan` | `app.models.training` | 16 |
| ai_talent_platform | `user_roles` | `UserRole` | `app.models.role` | 3 |
| ai_talent_platform | `users` | `User` | `app.models.user` | 22 |
| ai_talent_analytics | `dim_organization` | `DimOrganization` | `app.models.analytics` | 7 |
| ai_talent_analytics | `dim_talent_wide` | `DimTalentWide` | `app.models.analytics` | 9 |
| ai_talent_analytics | `fact_assessment_history` | `FactAssessmentHistory` | `app.models.analytics` | 9 |
| ai_talent_analytics | `fact_behavior_event` | `FactBehaviorEvent` | `app.models.analytics` | 7 |
| ai_talent_analytics | `fact_talent_snapshot` | `FactTalentSnapshot` | `app.models.analytics` | 9 |
| ai_talent_analytics | `fact_training_track` | `FactTrainingTrack` | `app.models.analytics` | 9 |

## MySQL 业务主库 `ai_talent_platform`

### `assessment_answers` → `AssessmentAnswer`

测评答题记录表（定义：`app.models.assessment`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `assessment_id` | `BIGINT` | 否 | — |  | assessments.id ON DELETE CASCADE | 测评 ID（FK -> assessments.id，CASCADE） |
| `question_id` | `BIGINT` | 否 | — |  | assessment_questions.id ON DELETE CASCADE | 题目 ID（FK -> assessment_questions.id，CASCADE） |
| `answer` | `JSON` | 是 | — |  | — | 用户答案 |
| `score` | `NUMERIC(5, 2)` | 是 | — |  | — | 本题得分 |
| `scored_by` | `VARCHAR(16)` | 是 | — |  | — | 评分方式：rule/ai |
| `scored_at` | `DATETIME` | 是 | — |  | — | 评分时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_assessment_answers_assessment_question` | `assessment_id, question_id` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_assessment_answers_created_by` | `created_by` | 否 |
| `ix_assessment_answers_is_deleted` | `is_deleted` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `assessment` | 多对一 | `Assessment`（`assessments`） | select | answers | merge,save-update |
| `question` | 多对一 | `AssessmentQuestion`（`assessment_questions`） | selectin | answers | merge,save-update |

### `assessment_questions` → `AssessmentQuestion`

测评题目表（定义：`app.models.assessment`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `assessment_id` | `BIGINT` | 否 | — |  | assessments.id ON DELETE CASCADE | 测评 ID（FK -> assessments.id，CASCADE） |
| `type` | `VARCHAR(16)` | 否 | 服务器端 'single' |  | — | 题型：single/multiple/text |
| `content` | `TEXT` | 否 | — |  | — | 题干 |
| `options` | `JSON` | 是 | — |  | — | 选项 [{key,text}]（客观题） |
| `answer` | `JSON` | 是 | — |  | — | 标准答案（绝不返回给前端） |
| `score` | `NUMERIC(5, 2)` | 否 | 服务器端 0 |  | — | 分值 |
| `dimension` | `VARCHAR(32)` | 是 | — |  | — | 考察维度（雷达图） |
| `sort` | `INTEGER` | 否 | 服务器端 0 |  | — | 展示顺序 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_assessment_questions_assessment_sort` | `assessment_id, sort` | 否 |
| `ix_assessment_questions_created_by` | `created_by` | 否 |
| `ix_assessment_questions_is_deleted` | `is_deleted` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `answers` | 一对多 | `AssessmentAnswer`（`assessment_answers`） | select | question | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `assessment` | 多对一 | `Assessment`（`assessments`） | select | questions | merge,save-update |

### `assessment_results` → `AssessmentResult`

测评结果表（定义：`app.models.assessment`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `assessment_id` | `BIGINT` | 否 | — |  | assessments.id ON DELETE CASCADE | 测评 ID（唯一，FK -> assessments.id，CASCADE） |
| `total_score` | `NUMERIC(5, 2)` | 是 | — |  | — | 总分 |
| `dimension_scores` | `JSON` | 是 | — |  | — | 维度分（雷达图数据源） |
| `level` | `VARCHAR(16)` | 是 | — |  | — | 等级：初级/中级/高级/专家 |
| `report` | `JSON` | 是 | — |  | — | AI 研判报告 |
| `suggestions` | `JSON` | 是 | — |  | — | 提升建议 |
| `model_name` | `VARCHAR(64)` | 是 | — |  | — | 模型（可追溯） |
| `prompt_version` | `VARCHAR(32)` | 是 | — |  | — | 提示词版本 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_assessment_results_assessment_id` | `assessment_id` | 是 |
| `ix_assessment_results_created_by` | `created_by` | 否 |
| `ix_assessment_results_is_deleted` | `is_deleted` | 否 |
| `ix_assessment_results_level` | `level` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `assessment` | 多对一 | `Assessment`（`assessments`） | select | result | merge,save-update |

### `assessments` → `Assessment`

测评任务表（定义：`app.models.assessment`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `talent_id` | `BIGINT` | 否 | — |  | talents.id ON DELETE CASCADE | 人才 ID（FK -> talents.id，CASCADE） |
| `position_id` | `BIGINT` | 是 | — |  | positions.id ON DELETE SET NULL | 岗位 ID（FK -> positions.id，SET NULL，可空） |
| `title` | `VARCHAR(128)` | 否 | — |  | — | 测评标题 |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'pending' |  | — | 状态：pending/in_progress/submitted/scored |
| `question_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 题目数量 |
| `deadline` | `DATETIME` | 是 | — |  | — | 截止时间 |
| `started_at` | `DATETIME` | 是 | — |  | — | 开始时间 |
| `submitted_at` | `DATETIME` | 是 | — |  | — | 提交时间 |
| `total_score` | `NUMERIC(5, 2)` | 是 | — |  | — | 总分（提交后回填） |
| `created_by_ai` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否 AI 生成（千人千卷） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_assessments_created_by` | `created_by` | 否 |
| `ix_assessments_deadline` | `deadline` | 否 |
| `ix_assessments_is_deleted` | `is_deleted` | 否 |
| `ix_assessments_talent_id_status` | `talent_id, status` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `answers` | 一对多 | `AssessmentAnswer`（`assessment_answers`） | select | assessment | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `position` | 多对一 | `Position`（`positions`） | selectin | — | merge,save-update |
| `questions` | 一对多 | `AssessmentQuestion`（`assessment_questions`） | select | assessment | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `result` | 一对一 | `AssessmentResult`（`assessment_results`） | selectin | assessment | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `talent` | 多对一 | `Talent`（`talents`） | selectin | — | merge,save-update |

### `audit_logs` → `AuditLog`

操作审计日志表（定义：`app.models.audit`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | `BIGINT` | 是 | — |  | users.id ON DELETE SET NULL | 操作人 ID（逻辑外键 -> users.id，审计保留需要） |
| `username` | `VARCHAR(64)` | 是 | — |  | — | 冗余用户名（防用户改名后无法追溯） |
| `action` | `VARCHAR(16)` | 否 | — |  | — | 动作：create/update/delete/login/export/ai_call |
| `module` | `VARCHAR(32)` | 是 | — |  | — | 模块：talent/position/... |
| `target_type` | `VARCHAR(32)` | 是 | — |  | — | 操作对象类型 |
| `target_id` | `BIGINT` | 是 | — |  | — | 操作对象 ID |
| `request_method` | `VARCHAR(8)` | 是 | — |  | — | 请求方法 |
| `request_path` | `VARCHAR(255)` | 是 | — |  | — | 请求路径 |
| `request_ip` | `VARCHAR(64)` | 是 | — |  | — | 客户端 IP |
| `user_agent` | `VARCHAR(255)` | 是 | — |  | — | User-Agent |
| `trace_id` | `VARCHAR(64)` | 是 | — |  | — | 链路追踪 ID |
| `before_data` | `JSON` | 是 | — |  | — | 变更前数据（敏感字段脱敏） |
| `after_data` | `JSON` | 是 | — |  | — | 变更后数据（敏感字段脱敏） |
| `status` | `VARCHAR(16)` | 否 | 服务器端 'success' |  | — | 状态：success/fail |
| `error_message` | `VARCHAR(512)` | 是 | — |  | — | 失败原因 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_audit_logs_created_by` | `created_by` | 否 |
| `ix_audit_logs_is_deleted` | `is_deleted` | 否 |
| `ix_audit_logs_module_action` | `module, action` | 否 |
| `ix_audit_logs_trace_id` | `trace_id` | 否 |
| `ix_audit_logs_user_id_created_at` | `user_id, created_at` | 否 |

### `chat_messages` → `ChatMessage`

会话消息表（定义：`app.models.conversation`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `conversation_id` | `BIGINT` | 否 | — |  | conversations.id ON DELETE CASCADE | 会话 ID（FK -> conversations.id，CASCADE） |
| `role` | `VARCHAR(16)` | 否 | 服务器端 'user' |  | — | 角色：user/assistant/system/tool |
| `content` | `TEXT` | 是 | — |  | — | 消息内容 |
| `tool_calls` | `JSON` | 是 | — |  | — | 工具调用记录 |
| `citations` | `JSON` | 是 | — |  | — | RAG 引用：doc_id/chunk/page/score |
| `prompt_tokens` | `INTEGER` | 否 | 服务器端 0 |  | — | 提示 token 数 |
| `completion_tokens` | `INTEGER` | 否 | 服务器端 0 |  | — | 生成 token 数 |
| `model_name` | `VARCHAR(64)` | 是 | — |  | — | 模型 |
| `elapsed_ms` | `INTEGER` | 是 | — |  | — | 耗时（毫秒） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_chat_messages_conversation_created` | `conversation_id, created_at` | 否 |
| `ix_chat_messages_conversation_id` | `conversation_id` | 否 |
| `ix_chat_messages_created_by` | `created_by` | 否 |
| `ix_chat_messages_is_deleted` | `is_deleted` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `conversation` | 多对一 | `Conversation`（`conversations`） | select | messages | merge,save-update |

### `conversations` → `Conversation`

AI 会话表（定义：`app.models.conversation`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | `BIGINT` | 否 | — |  | users.id ON DELETE CASCADE | 用户 ID（FK -> users.id，CASCADE） |
| `title` | `VARCHAR(128)` | 是 | — |  | — | 会话标题 |
| `agent_type` | `VARCHAR(32)` | 否 | 服务器端 'assistant' |  | — | Agent 类型：assistant/resume/talent/matching/assessment/training |
| `status` | `VARCHAR(16)` | 否 | 服务器端 'active' |  | — | 状态：active/archived |
| `message_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 消息数 |
| `last_message_at` | `DATETIME` | 是 | — |  | — | 最后消息时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_conversations_created_by` | `created_by` | 否 |
| `ix_conversations_is_deleted` | `is_deleted` | 否 |
| `ix_conversations_user_id` | `user_id` | 否 |
| `ix_conversations_user_id_last_message_at` | `user_id, last_message_at` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `messages` | 一对多 | `ChatMessage`（`chat_messages`） | select | conversation | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `user` | 多对一 | `User`（`users`） | selectin | — | merge,save-update |

### `courses` → `Course`

课程表（定义：`app.models.training`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `title` | `VARCHAR(128)` | 否 | — |  | — | 课程名称 |
| `category` | `VARCHAR(32)` | 是 | — |  | — | 分类 |
| `level` | `VARCHAR(20)` | 是 | — |  | — | 适用层级 |
| `duration_hours` | `NUMERIC(6, 2)` | 是 | — |  | — | 学时 |
| `resource_url` | `VARCHAR(512)` | 是 | — |  | — | 资源地址 |
| `cover` | `VARCHAR(255)` | 是 | — |  | — | 封面 |
| `description` | `TEXT` | 是 | — |  | — | 简介 |
| `skill_tags` | `JSON` | 是 | — |  | — | 对应技能标签 |
| `is_active` | `BOOLEAN` | 否 | 服务器端 1 |  | — | 是否上架 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_courses_category` | `category` | 否 |
| `ix_courses_created_by` | `created_by` | 否 |
| `ix_courses_is_deleted` | `is_deleted` | 否 |
| `ix_courses_level` | `level` | 否 |
| `ix_courses_title` | `title` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `learning_records` | 一对多 | `LearningRecord`（`learning_records`） | select | course | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `plan_courses` | 一对多 | `PlanCourse`（`plan_courses`） | select | course | delete,delete-orphan,expunge,merge,refresh-expire,save-update |

### `files` → `File`

文件元数据表（定义：`app.models.file`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `file_name` | `VARCHAR(255)` | 否 | — |  | — | 原始文件名 |
| `stored_name` | `VARCHAR(128)` | 否 | — |  | — | 存储名（uuid + 扩展名） |
| `file_path` | `VARCHAR(512)` | 否 | — |  | — | 相对路径：{biz_type}/{yyyy}/{mm}/xxx |
| `file_size` | `BIGINT` | 否 | 服务器端 0 |  | — | 字节数 |
| `content_type` | `VARCHAR(128)` | 是 | — |  | — | MIME 类型 |
| `extension` | `VARCHAR(16)` | 是 | — |  | — | 扩展名（含点） |
| `file_hash` | `VARCHAR(64)` | 是 | — |  | — | sha256，秒传与查重 |
| `storage_type` | `VARCHAR(16)` | 否 | 服务器端 'local' |  | — | 存储方式：local/oss |
| `biz_type` | `VARCHAR(32)` | 否 | — |  | — | 业务类型：resume/knowledge/avatar/export |
| `biz_id` | `BIGINT` | 是 | — |  | — | 关联业务 ID（多态） |
| `uploader_id` | `BIGINT` | 是 | — |  | users.id ON DELETE SET NULL | 上传人（FK -> users.id，SET NULL） |
| `is_public` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否公开访问 |
| `download_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 下载次数 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_files_biz_type_biz_id` | `biz_type, biz_id` | 否 |
| `ix_files_created_by` | `created_by` | 否 |
| `ix_files_file_hash` | `file_hash` | 否 |
| `ix_files_is_deleted` | `is_deleted` | 否 |
| `ix_files_uploader_id` | `uploader_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `knowledge_docs` | 一对多 | `KnowledgeDoc`（`knowledge_docs`） | select | file | merge,save-update |
| `resumes` | 一对多 | `Resume`（`resumes`） | select | file | merge,save-update |
| `uploader` | 多对一 | `User`（`users`） | selectin | — | merge,save-update |

### `knowledge_chunks` → `KnowledgeChunk`

知识切片表（定义：`app.models.knowledge`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `doc_id` | `BIGINT` | 否 | — |  | knowledge_docs.id ON DELETE CASCADE | 文档 ID（FK -> knowledge_docs.id，CASCADE） |
| `chunk_index` | `INTEGER` | 否 | — |  | — | 切片序号 |
| `content` | `TEXT` | 否 | — |  | — | 切片原文 |
| `token_count` | `INTEGER` | 是 | — |  | — | token 数 |
| `milvus_pk` | `BIGINT` | 是 | — |  | — | Milvus 主键（回表用） |
| `page_no` | `INTEGER` | 是 | — |  | — | 页码（引用溯源） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_knowledge_chunks_doc_index` | `doc_id, chunk_index` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_knowledge_chunks_created_by` | `created_by` | 否 |
| `ix_knowledge_chunks_doc_id` | `doc_id` | 否 |
| `ix_knowledge_chunks_is_deleted` | `is_deleted` | 否 |
| `ix_knowledge_chunks_milvus_pk` | `milvus_pk` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `doc` | 多对一 | `KnowledgeDoc`（`knowledge_docs`） | select | chunks | merge,save-update |

### `knowledge_docs` → `KnowledgeDoc`

知识文档表（定义：`app.models.knowledge`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `title` | `VARCHAR(255)` | 否 | — |  | — | 文档标题 |
| `category` | `VARCHAR(64)` | 是 | — |  | — | 分类 |
| `source_type` | `VARCHAR(16)` | 否 | 服务器端 'text' |  | — | 来源类型：file/text |
| `file_id` | `BIGINT` | 是 | — |  | files.id ON DELETE SET NULL | 文件 ID（FK -> files.id，SET NULL，source_type=text 时为空） |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'indexing' |  | — | 索引状态：indexing/indexed/failed |
| `chunk_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 切片数量 |
| `permission_scope` | `VARCHAR(16)` | 否 | 服务器端 'public' |  | — | 权限范围：public/dept/role |
| `allowed_roles` | `JSON` | 是 | — |  | — | 允许访问的角色编码（permission_scope=role 时生效） |
| `organization_id` | `BIGINT` | 是 | — |  | — | 部门范围（permission_scope=dept 时生效） |
| `embedding_model` | `VARCHAR(64)` | 是 | — |  | — | 向量模型 |
| `index_error` | `VARCHAR(512)` | 是 | — |  | — | 索引失败原因 |
| `indexed_at` | `DATETIME` | 是 | — |  | — | 索引完成时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_knowledge_docs_category` | `category` | 否 |
| `ix_knowledge_docs_created_by` | `created_by` | 否 |
| `ix_knowledge_docs_is_deleted` | `is_deleted` | 否 |
| `ix_knowledge_docs_organization_id` | `organization_id` | 否 |
| `ix_knowledge_docs_status` | `status` | 否 |
| `ix_knowledge_docs_title` | `title` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `chunks` | 一对多 | `KnowledgeChunk`（`knowledge_chunks`） | select | doc | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `file` | 多对一 | `File`（`files`） | selectin | knowledge_docs | merge,save-update |

### `learning_records` → `LearningRecord`

学习记录表（定义：`app.models.training`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan_id` | `BIGINT` | 是 | — |  | training_plans.id ON DELETE SET NULL | 计划 ID（FK -> training_plans.id，SET NULL） |
| `course_id` | `BIGINT` | 是 | — |  | courses.id ON DELETE SET NULL | 课程 ID（FK -> courses.id，SET NULL） |
| `talent_id` | `BIGINT` | 否 | — |  | talents.id ON DELETE CASCADE | 人才 ID（FK -> talents.id，CASCADE） |
| `progress` | `INTEGER` | 否 | 服务器端 0 |  | — | 进度 0-100（单调递增） |
| `study_minutes` | `INTEGER` | 否 | 服务器端 0 |  | — | 累计学习分钟 |
| `last_study_at` | `DATETIME` | 是 | — |  | — | 最近学习时间 |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'not_started' |  | — | 状态：not_started/learning/completed |
| `score` | `NUMERIC(5, 2)` | 是 | — |  | — | 考核分数 |
| `passed` | `BOOLEAN` | 是 | — |  | — | 是否通过 |
| `suspicious` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 反作弊标记（学时与进度比例异常） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_learning_records_plan_course_talent` | `plan_id, course_id, talent_id` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_learning_records_created_by` | `created_by` | 否 |
| `ix_learning_records_is_deleted` | `is_deleted` | 否 |
| `ix_learning_records_talent_id_status` | `talent_id, status` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `course` | 多对一 | `Course`（`courses`） | select | learning_records | merge,save-update |
| `plan` | 多对一 | `TrainingPlan`（`training_plans`） | select | learning_records | merge,save-update |
| `talent` | 多对一 | `Talent`（`talents`） | select | learning_records | merge,save-update |

### `matching_records` → `MatchingRecord`

人岗匹配记录表（定义：`app.models.matching`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `batch_id` | `VARCHAR(32)` | 否 | — |  | — | 跑批 ID（uuid hex） |
| `position_id` | `BIGINT` | 否 | — |  | — | 岗位 ID（逻辑外键） |
| `talent_id` | `BIGINT` | 否 | — |  | — | 人才 ID（逻辑外键） |
| `score` | `NUMERIC(5, 2)` | 否 | 服务器端 0 |  | — | 匹配总分 0-100 |
| `dimension_scores` | `JSON` | 是 | — |  | — | 维度分：skill/experience/education/quality |
| `highlights` | `JSON` | 是 | — |  | — | 优势亮点 |
| `gaps` | `JSON` | 是 | — |  | — | 短板 |
| `suggestion` | `VARCHAR(512)` | 是 | — |  | — | 适配建议 |
| `strategy` | `VARCHAR(16)` | 是 | — |  | — | 匹配策略：hard/soft/hybrid |
| `model_name` | `VARCHAR(64)` | 是 | — |  | — | AI 解释所用模型 |
| `matched_by` | `BIGINT` | 是 | — |  | — | 触发用户 ID |
| `matched_at` | `DATETIME` | 是 | — |  | — | 匹配时间（分区键） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_matching_records_batch_position_talent` | `batch_id, position_id, talent_id` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_matching_records_batch_id` | `batch_id` | 否 |
| `ix_matching_records_created_by` | `created_by` | 否 |
| `ix_matching_records_is_deleted` | `is_deleted` | 否 |
| `ix_matching_records_matched_at` | `matched_at` | 否 |
| `ix_matching_records_position_id_score` | `position_id, score` | 否 |
| `ix_matching_records_talent_id` | `talent_id` | 否 |

### `organizations` → `Organization`

组织架构表（定义：`app.models.organization`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `parent_id` | `BIGINT` | 是 | — |  | organizations.id ON DELETE SET NULL | 上级部门 ID，NULL 表示根节点 |
| `code` | `VARCHAR(64)` | 否 | — |  | — | 部门编码 |
| `name` | `VARCHAR(128)` | 否 | — |  | — | 部门名称 |
| `level` | `INTEGER` | 否 | 服务器端 1 |  | — | 层级，根为 1 |
| `path` | `VARCHAR(512)` | 否 | 服务器端 '/' |  | — | 祖级路径，如 /1/3/7/ |
| `leader_id` | `BIGINT` | 是 | — |  | — | 部门负责人用户 ID（逻辑外键 -> users.id） |
| `sort` | `INTEGER` | 否 | 服务器端 0 |  | — | 排序号，越小越靠前 |
| `is_active` | `BOOLEAN` | 否 | 服务器端 1 |  | — | 是否启用 |
| `remark` | `VARCHAR(255)` | 是 | — |  | — | 备注 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_organizations_parent_name` | `parent_id, name` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_organizations_code` | `code` | 是 |
| `ix_organizations_created_by` | `created_by` | 否 |
| `ix_organizations_is_deleted` | `is_deleted` | 否 |
| `ix_organizations_leader_id` | `leader_id` | 否 |
| `ix_organizations_name` | `name` | 否 |
| `ix_organizations_parent_id` | `parent_id` | 否 |
| `ix_organizations_path` | `path` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `positions` | 一对多 | `Position`（`positions`） | select | organization | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `talents` | 一对多 | `Talent`（`talents`） | select | organization | merge,save-update |

### `plan_courses` → `PlanCourse`

培训计划-课程关联表（定义：`app.models.training`）

- 主键：`plan_id, course_id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan_id` | `BIGINT` | 否 | — | ✅ | training_plans.id ON DELETE CASCADE | 计划 ID（FK -> training_plans.id，CASCADE） |
| `course_id` | `BIGINT` | 否 | — | ✅ | courses.id ON DELETE CASCADE | 课程 ID（FK -> courses.id，CASCADE） |
| `sort` | `INTEGER` | 否 | 服务器端 0 |  | — | 学习顺序 |
| `require_pass` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否必须通过考核 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_plan_courses_plan_course` | `plan_id, course_id` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_plan_courses_course_id` | `course_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `course` | 多对一 | `Course`（`courses`） | select | plan_courses | merge,save-update |
| `plan` | 多对一 | `TrainingPlan`（`training_plans`） | select | plan_courses | merge,save-update |

### `position_requirements` → `PositionRequirement`

岗位能力模型表（多版本）（定义：`app.models.position`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `position_id` | `BIGINT` | 否 | — |  | positions.id ON DELETE CASCADE | 岗位 ID（FK -> positions.id，CASCADE） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 版本号（同岗位递增） |
| `dimensions` | `JSON` | 是 | — |  | — | 维度定义：技能/经验/学历/素质 |
| `weights` | `JSON` | 是 | — |  | — | 维度权重（合计 1.0） |
| `source` | `VARCHAR(20)` | 否 | 服务器端 'manual' |  | — | 来源：manual/ai_parse |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_position_requirements_position_version` | `position_id, version` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_position_requirements_created_by` | `created_by` | 否 |
| `ix_position_requirements_is_deleted` | `is_deleted` | 否 |
| `ix_position_requirements_position_id` | `position_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `position` | 多对一 | `Position`（`positions`） | select | requirement_versions | merge,save-update |

### `positions` → `Position`

岗位表（定义：`app.models.position`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `title` | `VARCHAR(128)` | 否 | — |  | — | 岗位名称 |
| `code` | `VARCHAR(64)` | 否 | — |  | — | 岗位编码 |
| `organization_id` | `BIGINT` | 否 | — |  | organizations.id | 所属部门 ID（FK -> organizations.id） |
| `level` | `VARCHAR(20)` | 否 | 服务器端 'middle' |  | — | 岗位层级：junior/middle/senior/expert |
| `headcount` | `INTEGER` | 否 | 服务器端 1 |  | — | 编制人数 |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'open' |  | — | 状态：open/paused/closed（open ⇄ paused，closed 单向） |
| `description` | `TEXT` | 是 | — |  | — | 岗位说明书全文 |
| `requirements` | `JSON` | 是 | — |  | — | 结构化要求：education/experience_years/skills/certificates/soft_skills/weights |
| `skill_tags` | `JSON` | 是 | — |  | — | 技能标签数组 |
| `salary_min` | `NUMERIC(12, 2)` | 是 | — |  | — | 薪资下限（元/月） |
| `salary_max` | `NUMERIC(12, 2)` | 是 | — |  | — | 薪资上限（元/月） |
| `publish_at` | `DATETIME` | 是 | — |  | — | 发布时间 |
| `close_at` | `DATETIME` | 是 | — |  | — | 关闭时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_positions_code` | `code` | 是 |
| `ix_positions_created_by` | `created_by` | 否 |
| `ix_positions_is_deleted` | `is_deleted` | 否 |
| `ix_positions_level` | `level` | 否 |
| `ix_positions_organization_id_status` | `organization_id, status` | 否 |
| `ix_positions_title` | `title` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `organization` | 多对一 | `Organization`（`organizations`） | selectin | positions | merge,save-update |
| `requirement_versions` | 一对多 | `PositionRequirement`（`position_requirements`） | select | position | delete,delete-orphan,expunge,merge,refresh-expire,save-update |

### `resume_experiences` → `ResumeExperience`

简历工作经历表（定义：`app.models.resume`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `resume_id` | `BIGINT` | 否 | — |  | resumes.id ON DELETE CASCADE | 简历 ID（FK -> resumes.id，CASCADE） |
| `company` | `VARCHAR(128)` | 否 | — |  | — | 公司名称 |
| `title` | `VARCHAR(128)` | 是 | — |  | — | 职位 |
| `start_date` | `DATE` | 是 | — |  | — | 开始日期 |
| `end_date` | `DATE` | 是 | — |  | — | 结束日期（空表示至今） |
| `description` | `TEXT` | 是 | — |  | — | 工作内容 |
| `sort` | `INTEGER` | 否 | 服务器端 0 |  | — | 展示顺序 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_resume_experiences_company` | `company` | 否 |
| `ix_resume_experiences_created_by` | `created_by` | 否 |
| `ix_resume_experiences_is_deleted` | `is_deleted` | 否 |
| `ix_resume_experiences_resume_id` | `resume_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `resume` | 多对一 | `Resume`（`resumes`） | select | experiences | merge,save-update |

### `resumes` → `Resume`

简历表（定义：`app.models.resume`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `talent_id` | `BIGINT` | 是 | — |  | talents.id ON DELETE SET NULL | 人才 ID（FK -> talents.id，SET NULL，解析后回填） |
| `file_id` | `BIGINT` | 是 | — |  | files.id ON DELETE SET NULL | 文件 ID（FK -> files.id，SET NULL） |
| `file_name` | `VARCHAR(255)` | 否 | — |  | — | 原始文件名 |
| `file_type` | `VARCHAR(32)` | 是 | — |  | — | 文件类型：pdf/docx/doc/xlsx/txt/png/jpg |
| `file_size` | `BIGINT` | 否 | 服务器端 0 |  | — | 字节数 |
| `file_hash` | `VARCHAR(64)` | 否 | — |  | — | sha256（秒传/查重，唯一） |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'uploaded' |  | — | 状态：uploaded/parsing/success/failed |
| `accuracy` | `NUMERIC(4, 3)` | 是 | — |  | — | 解析置信度 0~1 |
| `parsed_data` | `JSON` | 是 | — |  | — | 结构化简历：basic_info/education/work_experience/projects/skills |
| `parse_error` | `VARCHAR(512)` | 是 | — |  | — | 解析失败原因 |
| `parsed_at` | `DATETIME` | 是 | — |  | — | 解析完成时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_resumes_created_by` | `created_by` | 否 |
| `ix_resumes_file_hash` | `file_hash` | 是 |
| `ix_resumes_is_deleted` | `is_deleted` | 否 |
| `ix_resumes_status` | `status` | 否 |
| `ix_resumes_talent_id` | `talent_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `experiences` | 一对多 | `ResumeExperience`（`resume_experiences`） | select | resume | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `file` | 多对一 | `File`（`files`） | selectin | resumes | merge,save-update |
| `talent` | 多对一 | `Talent`（`talents`） | selectin | resumes | merge,save-update |

### `roles` → `Role`

角色表（定义：`app.models.role`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 否 | — | ✅ | — | 角色 ID |
| `code` | `VARCHAR(64)` | 否 | — |  | — | 角色编码：super_admin/admin/hr/manager/employee |
| `name` | `VARCHAR(64)` | 否 | — |  | — | 角色名称 |
| `description` | `VARCHAR(255)` | 是 | — |  | — | 角色描述 |
| `is_active` | `BOOLEAN` | 否 | 服务器端 1 |  | — | 是否启用 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_roles_code` | `code` | 是 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `users` | 多对多 | `User（经 user_roles）`（`users`） | select | roles | merge,save-update |

### `talent_portraits` → `TalentPortrait`

人才画像表（版本化）（定义：`app.models.talent`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `talent_id` | `BIGINT` | 否 | — |  | talents.id ON DELETE CASCADE | 人才 ID（FK -> talents.id，CASCADE） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 画像版本（每次重算 +1） |
| `dimensions` | `JSON` | 是 | — |  | — | 维度分：skill/experience/quality/potential，0-100 |
| `labels` | `JSON` | 是 | — |  | — | AI 标签数组 |
| `strengths` | `JSON` | 是 | — |  | — | 优势列表 |
| `weaknesses` | `JSON` | 是 | — |  | — | 短板列表 |
| `suggested_positions` | `JSON` | 是 | — |  | — | 建议岗位 |
| `potential_level` | `VARCHAR(8)` | 是 | — |  | — | 潜力等级 |
| `model_name` | `VARCHAR(64)` | 是 | — |  | — | 生成模型 |
| `prompt_version` | `VARCHAR(32)` | 是 | — |  | — | 提示词版本 |
| `generated_at` | `DATETIME` | 是 | — |  | — | 生成时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_talent_portraits_talent_version` | `talent_id, version` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_talent_portraits_created_by` | `created_by` | 否 |
| `ix_talent_portraits_generated_at` | `generated_at` | 否 |
| `ix_talent_portraits_is_deleted` | `is_deleted` | 否 |
| `ix_talent_portraits_talent_id` | `talent_id` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `talent` | 多对一 | `Talent`（`talents`） | select | portraits | merge,save-update |

### `talents` → `Talent`

人才档案表（定义：`app.models.talent`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `name` | `VARCHAR(64)` | 否 | — |  | — | 姓名 |
| `gender` | `VARCHAR(16)` | 否 | 服务器端 'unknown' |  | — | 性别：male/female/unknown |
| `birth_date` | `DATE` | 是 | — |  | — | 出生日期 |
| `mobile` | `VARCHAR(20)` | 是 | — |  | — | 手机号 |
| `email` | `VARCHAR(128)` | 是 | — |  | — | 邮箱 |
| `id_card` | `VARCHAR(128)` | 是 | — |  | — | 身份证号（AES 加密存储，响应脱敏） |
| `education` | `VARCHAR(20)` | 是 | — |  | — | 最高学历 |
| `school` | `VARCHAR(128)` | 是 | — |  | — | 毕业院校 |
| `major` | `VARCHAR(128)` | 是 | — |  | — | 专业 |
| `work_years` | `INTEGER` | 否 | 服务器端 0 |  | — | 工作年限 |
| `current_company` | `VARCHAR(128)` | 是 | — |  | — | 现公司 |
| `current_position` | `VARCHAR(128)` | 是 | — |  | — | 现职位 |
| `organization_id` | `BIGINT` | 是 | — |  | organizations.id ON DELETE SET NULL | 归属部门 ID（FK -> organizations.id，SET NULL） |
| `skill_tags` | `JSON` | 是 | — |  | — | 技能标签数组 |
| `certificates` | `JSON` | 是 | — |  | — | 证书列表 |
| `self_evaluation` | `TEXT` | 是 | — |  | — | 自我评价 |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'active' |  | — | 状态：active/left/reserve |
| `potential_level` | `VARCHAR(8)` | 是 | — |  | — | 潜力等级：A/B/C/D（画像生成） |
| `portrait_summary` | `VARCHAR(512)` | 是 | — |  | — | 画像摘要 |
| `source` | `VARCHAR(20)` | 否 | 服务器端 'manual' |  | — | 来源：manual/ai_resume |
| `resume_id` | `BIGINT` | 是 | — |  | — | 来源简历 ID（逻辑外键 -> resumes.id） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_talents_mobile_is_deleted` | `mobile, is_deleted` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_talents_created_by` | `created_by` | 否 |
| `ix_talents_is_deleted` | `is_deleted` | 否 |
| `ix_talents_name` | `name` | 否 |
| `ix_talents_organization_id_status` | `organization_id, status` | 否 |
| `ix_talents_work_years` | `work_years` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `learning_records` | 一对多 | `LearningRecord`（`learning_records`） | select | talent | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `organization` | 多对一 | `Organization`（`organizations`） | selectin | talents | merge,save-update |
| `portraits` | 一对多 | `TalentPortrait`（`talent_portraits`） | select | talent | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `resumes` | 一对多 | `Resume`（`resumes`） | select | talent | merge,save-update |
| `training_plans` | 一对多 | `TrainingPlan`（`training_plans`） | select | talent | delete,delete-orphan,expunge,merge,refresh-expire,save-update |

### `training_plans` → `TrainingPlan`

培训计划表（定义：`app.models.training`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `name` | `VARCHAR(128)` | 否 | — |  | — | 计划名称 |
| `talent_id` | `BIGINT` | 否 | — |  | talents.id ON DELETE CASCADE | 人才 ID（FK -> talents.id，CASCADE） |
| `goal` | `VARCHAR(512)` | 是 | — |  | — | 培养目标 |
| `start_date` | `DATE` | 是 | — |  | — | 开始日期 |
| `end_date` | `DATE` | 是 | — |  | — | 结束日期 |
| `status` | `VARCHAR(20)` | 否 | 服务器端 'draft' |  | — | 状态：draft/ongoing/finished/cancelled |
| `created_by_ai` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否 AI 生成 |
| `source_assessment_id` | `BIGINT` | 是 | — |  | — | 能力缺口来源测评 ID（逻辑外键 -> assessments.id） |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_training_plans_created_by` | `created_by` | 否 |
| `ix_training_plans_is_deleted` | `is_deleted` | 否 |
| `ix_training_plans_talent_id_status` | `talent_id, status` | 否 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `learning_records` | 一对多 | `LearningRecord`（`learning_records`） | select | plan | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `plan_courses` | 一对多 | `PlanCourse`（`plan_courses`） | select | plan | delete,delete-orphan,expunge,merge,refresh-expire,save-update |
| `talent` | 多对一 | `Talent`（`talents`） | select | training_plans | merge,save-update |

### `user_roles` → `UserRole`

用户角色关联表（定义：`app.models.role`）

- 主键：`user_id, role_id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | `BIGINT` | 否 | — | ✅ | users.id ON DELETE CASCADE | 用户 ID |
| `role_id` | `BIGINT` | 否 | — | ✅ | roles.id ON DELETE CASCADE | 角色 ID |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 绑定时间 |

### `users` → `User`

用户账号表（定义：`app.models.user`）

- 主键：`id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `username` | `VARCHAR(64)` | 否 | — |  | — | 登录名 |
| `password_hash` | `VARCHAR(128)` | 否 | — |  | — | 密码哈希（bcrypt） |
| `real_name` | `VARCHAR(64)` | 是 | — |  | — | 真实姓名 |
| `mobile` | `VARCHAR(20)` | 是 | — |  | — | 手机号 |
| `email` | `VARCHAR(128)` | 是 | — |  | — | 邮箱 |
| `avatar` | `VARCHAR(255)` | 是 | — |  | — | 头像地址 |
| `organization_id` | `BIGINT` | 是 | — |  | organizations.id ON DELETE SET NULL | 所属部门 ID |
| `position_title` | `VARCHAR(64)` | 是 | — |  | — | 岗位名称/职务 |
| `is_active` | `BOOLEAN` | 否 | 服务器端 1 |  | — | 是否启用 |
| `is_superuser` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否超管 |
| `last_login_at` | `DATETIME` | 是 | — |  | — | 最后登录时间 |
| `last_login_ip` | `VARCHAR(64)` | 是 | — |  | — | 最后登录 IP |
| `fail_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 连续登录失败次数 |
| `locked_until` | `DATETIME` | 是 | — |  | — | 锁定截止时间 |
| `id` | `BIGINT` | 否 | — | ✅ | — | 主键 ID（BIGINT，主键索引由 PK 提供） |
| `version` | `INTEGER` | 否 | 服务器端 1 |  | — | 乐观锁版本号，每次更新 +1 |
| `created_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 CURRENT_TIMESTAMP |  | — | 更新时间 |
| `is_deleted` | `BOOLEAN` | 否 | 服务器端 0 |  | — | 是否已删除（逻辑删除） |
| `deleted_at` | `DATETIME` | 是 | — |  | — | 删除时间 |
| `created_by` | `BIGINT` | 是 | — |  | — | 创建人用户 ID |
| `updated_by` | `BIGINT` | 是 | — |  | — | 最后修改人用户 ID |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_users_created_by` | `created_by` | 否 |
| `ix_users_email` | `email` | 是 |
| `ix_users_is_deleted` | `is_deleted` | 否 |
| `ix_users_mobile` | `mobile` | 是 |
| `ix_users_organization_id` | `organization_id` | 否 |
| `ix_users_username` | `username` | 是 |

ORM 关系：

| 属性 | 关系 | 目标 | 加载方式 | 反向属性 | 级联 |
| --- | --- | --- | --- | --- | --- |
| `roles` | 多对多 | `Role（经 user_roles）`（`roles`） | selectin | users | merge,save-update |

## PgSQL 分析库 `ai_talent_analytics`

### `dim_organization` → `DimOrganization`

部门维表（定义：`app.models.analytics`）

- 主键：`org_id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `org_id` | `BIGINT` | 否 | — | ✅ | — | MySQL 部门 ID |
| `code` | `VARCHAR(64)` | 否 | — |  | — | 部门编码 |
| `name` | `VARCHAR(128)` | 否 | — |  | — | 部门名称 |
| `level` | `INTEGER` | 否 | — |  | — | 部门层级 |
| `path` | `VARCHAR(512)` | 否 | — |  | — | 祖先路径 |
| `parent_id` | `BIGINT` | 是 | — |  | — | 上级部门 ID |
| `synced_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 最近同步时间 |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_dim_organization_code` | `code` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_dim_organization_parent` | `parent_id` | 否 |

### `dim_talent_wide` → `DimTalentWide`

人才分析宽表（定义：`app.models.analytics`）

- 主键：`talent_id`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `talent_id` | `BIGINT` | 否 | — | ✅ | — | MySQL 人才 ID |
| `name` | `VARCHAR(64)` | 否 | — |  | — | 人才姓名 |
| `org_path` | `VARCHAR(512)` | 是 | — |  | — | 所属部门路径 |
| `education` | `VARCHAR(32)` | 是 | — |  | — | 最高学历 |
| `work_years` | `NUMERIC(5, 2)` | 是 | — |  | — | 工作年限 |
| `skill_tags` | `ARRAY` | 否 | 服务器端 '{}'::text[] |  | — | 技能标签 |
| `potential_level` | `VARCHAR(32)` | 是 | — |  | — | 潜力等级 |
| `match_avg_score` | `NUMERIC(5, 2)` | 是 | — |  | — | 平均匹配分 |
| `synced_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 最近同步时间 |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_dim_talent_wide_education` | `education` | 否 |
| `ix_dim_talent_wide_potential` | `potential_level` | 否 |

### `fact_assessment_history` → `FactAssessmentHistory`

测评历史明细（定义：`app.models.analytics`）

- 分区：`PARTITION BY RANGE (scored_at)`，默认分区 `fact_assessment_history_default`
- 主键：`id, scored_at`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 否 | 服务器端 None | ✅ | — | 历史记录 ID |
| `assessment_id` | `BIGINT` | 否 | — |  | — | MySQL 测评任务 ID |
| `talent_id` | `BIGINT` | 否 | — |  | — | 人才 ID |
| `position_id` | `BIGINT` | 是 | — |  | — | 岗位 ID |
| `total_score` | `NUMERIC(5, 2)` | 是 | — |  | — | 总分，0-100 |
| `level` | `VARCHAR(32)` | 是 | — |  | — | 能力等级 |
| `dimension_scores` | `JSONB` | 否 | 服务器端 '{}'::jsonb |  | — | 维度得分 |
| `scored_at` | `DATETIME` | 否 | — | ✅ | — | 评分时间 |
| `created_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 入库时间 |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_fact_assessment_history_assessment` | `assessment_id` | 否 |
| `ix_fact_assessment_history_talent_time` | `talent_id, scored_at` | 否 |

### `fact_behavior_event` → `FactBehaviorEvent`

用户行为时序事件（定义：`app.models.analytics`）

- 分区：`PARTITION BY RANGE (event_at)`，默认分区 `fact_behavior_event_default`
- 主键：`id, event_at`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 否 | 服务器端 None | ✅ | — | 事件 ID |
| `talent_id` | `BIGINT` | 否 | — |  | — | 人才 ID |
| `event_type` | `VARCHAR(64)` | 否 | — |  | — | 事件类型 |
| `page` | `VARCHAR(512)` | 是 | — |  | — | 页面或资源路径 |
| `duration_ms` | `INTEGER` | 是 | — |  | — | 停留时长，毫秒 |
| `event_at` | `DATETIME` | 否 | — | ✅ | — | 事件发生时间 |
| `created_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 入库时间 |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_fact_behavior_event_talent_time` | `talent_id, event_at` | 否 |
| `ix_fact_behavior_event_type_time` | `event_type, event_at` | 否 |

### `fact_talent_snapshot` → `FactTalentSnapshot`

人才月度快照（定义：`app.models.analytics`）

- 分区：`PARTITION BY RANGE (snapshot_month)`，默认分区 `fact_talent_snapshot_default`
- 主键：`id, snapshot_month`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 否 | 服务器端 None | ✅ | — | 快照记录 ID |
| `snapshot_month` | `DATE` | 否 | — | ✅ | — | 快照月份，取当月 1 日 |
| `organization_id` | `BIGINT` | 否 | — |  | — | 部门 ID，0 表示全部门汇总 |
| `education` | `VARCHAR(32)` | 否 | 服务器端 '' |  | — | 学历，空字符串表示不限 |
| `age_group` | `VARCHAR(32)` | 否 | 服务器端 '' |  | — | 年龄段，空字符串表示不限 |
| `talent_count` | `INTEGER` | 否 | 服务器端 0 |  | — | 人才数量 |
| `avg_work_years` | `NUMERIC(5, 2)` | 是 | — |  | — | 平均工作年限 |
| `created_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 更新时间 |

唯一约束：

| 约束名 | 字段 |
| --- | --- |
| `uq_fact_talent_snapshot_dimensions` | `snapshot_month, organization_id, education, age_group` |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_fact_talent_snapshot_org_month` | `organization_id, snapshot_month` | 否 |

### `fact_training_track` → `FactTrainingTrack`

培训学习轨迹（定义：`app.models.analytics`）

- 分区：`PARTITION BY RANGE (event_at)`，默认分区 `fact_training_track_default`
- 主键：`id, event_at`

| 字段 | 类型 | 空 | 默认 | 主键 | 外键 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 否 | 服务器端 None | ✅ | — | 轨迹记录 ID |
| `talent_id` | `BIGINT` | 否 | — |  | — | 人才 ID |
| `course_id` | `BIGINT` | 否 | — |  | — | 课程 ID |
| `plan_id` | `BIGINT` | 是 | — |  | — | 培训计划 ID |
| `progress` | `NUMERIC(5, 2)` | 否 | 服务器端 0 |  | — | 学习进度百分比 |
| `study_minutes` | `INTEGER` | 否 | 服务器端 0 |  | — | 累计学习分钟数 |
| `status` | `VARCHAR(32)` | 否 | — |  | — | 学习状态 |
| `event_at` | `DATETIME` | 否 | — | ✅ | — | 轨迹事件时间 |
| `created_at` | `DATETIME` | 否 | 服务器端 now() |  | — | 入库时间 |

索引：

| 索引名 | 字段 | 唯一 |
| --- | --- | --- |
| `ix_fact_training_track_course` | `course_id` | 否 |
| `ix_fact_training_track_talent_time` | `talent_id, event_at` | 否 |

## MySQL → PgSQL 分析表血缘（ETL 未实现，仅记录对应关系）

| PgSQL 表 | 来源 | 分区键 |
| --- | --- | --- |
| `fact_talent_snapshot` | talents + organizations 按 (organization_id, education, 年龄段) 聚合 | snapshot_month（RANGE，按月） |
| `fact_assessment_history` | assessments + assessment_results | scored_at（RANGE，按月） |
| `fact_training_track` | learning_records + training_plans + courses | event_at（RANGE） |
| `fact_behavior_event` | 前端埋点 / 业务行为事件 | event_at（RANGE，按天） |
| `dim_organization` | organizations 全量同步 | 无（维表） |
| `dim_talent_wide` | talents + talent_portraits + organizations + matching_records 打宽 | 无（宽表） |
