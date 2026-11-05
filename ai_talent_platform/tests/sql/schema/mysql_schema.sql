-- ============================================================================
-- mysql_schema.sql — MySQL 业务库建表 DDL（25 张表，测试/联调用）
-- 标签：[架构组] [P1]（由 ORM 元数据渲染：python scripts/init_db.py 或本文件）
--
-- 生成方式（与 app/models/* 保持同源，模型变更后重新生成）：
--   from sqlalchemy.schema import CreateTable, CreateIndex; from sqlalchemy.dialects import mysql
--   from app.models import Base  # 逐表 compile(dialect=mysql.dialect())
-- 注意：本文件不含 CREATE DATABASE / USE，请先建库并选中：
--   CREATE DATABASE ai_talent_platform_test DEFAULT CHARACTER SET utf8mb4;
-- ============================================================================

SET NAMES utf8mb4;
SET sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION';
CREATE TABLE courses (
	title VARCHAR(128) NOT NULL COMMENT '课程名称', 
	category VARCHAR(32) COMMENT '分类', 
	level VARCHAR(20) COMMENT '适用层级', 
	duration_hours NUMERIC(6, 2) COMMENT '学时', 
	resource_url VARCHAR(512) COMMENT '资源地址', 
	cover VARCHAR(255) COMMENT '封面', 
	description TEXT COMMENT '简介', 
	skill_tags JSON COMMENT '对应技能标签', 
	is_active BOOL NOT NULL COMMENT '是否上架' DEFAULT 1, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_courses PRIMARY KEY (id)
)COMMENT='课程表';
CREATE INDEX ix_courses_category ON courses (category);
CREATE INDEX ix_courses_created_by ON courses (created_by);
CREATE INDEX ix_courses_is_deleted ON courses (is_deleted);
CREATE INDEX ix_courses_level ON courses (level);
CREATE INDEX ix_courses_title ON courses (title);
CREATE TABLE matching_records (
	batch_id VARCHAR(32) NOT NULL COMMENT '跑批 ID（uuid hex）', 
	position_id BIGINT NOT NULL COMMENT '岗位 ID（逻辑外键）', 
	talent_id BIGINT NOT NULL COMMENT '人才 ID（逻辑外键）', 
	score NUMERIC(5, 2) NOT NULL COMMENT '匹配总分 0-100' DEFAULT '0', 
	dimension_scores JSON COMMENT '维度分：skill/experience/education/quality', 
	highlights JSON COMMENT '优势亮点', 
	gaps JSON COMMENT '短板', 
	suggestion VARCHAR(512) COMMENT '适配建议', 
	strategy VARCHAR(16) COMMENT '匹配策略：hard/soft/hybrid', 
	model_name VARCHAR(64) COMMENT 'AI 解释所用模型', 
	matched_by BIGINT COMMENT '触发用户 ID', 
	matched_at DATETIME COMMENT '匹配时间（分区键）', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_matching_records PRIMARY KEY (id), 
	CONSTRAINT uq_matching_records_batch_position_talent UNIQUE (batch_id, position_id, talent_id)
)COMMENT='人岗匹配记录表';
CREATE INDEX ix_matching_records_batch_id ON matching_records (batch_id);
CREATE INDEX ix_matching_records_created_by ON matching_records (created_by);
CREATE INDEX ix_matching_records_is_deleted ON matching_records (is_deleted);
CREATE INDEX ix_matching_records_matched_at ON matching_records (matched_at);
CREATE INDEX ix_matching_records_position_id_score ON matching_records (position_id, score);
CREATE INDEX ix_matching_records_talent_id ON matching_records (talent_id);
CREATE TABLE organizations (
	parent_id BIGINT COMMENT '上级部门 ID，NULL 表示根节点', 
	code VARCHAR(64) NOT NULL COMMENT '部门编码', 
	name VARCHAR(128) NOT NULL COMMENT '部门名称', 
	level INTEGER NOT NULL COMMENT '层级，根为 1' DEFAULT 1, 
	path VARCHAR(512) NOT NULL COMMENT '祖级路径，如 /1/3/7/' DEFAULT '/', 
	leader_id BIGINT COMMENT '部门负责人用户 ID（逻辑外键 -> users.id）', 
	sort INTEGER NOT NULL COMMENT '排序号，越小越靠前' DEFAULT 0, 
	is_active BOOL NOT NULL COMMENT '是否启用' DEFAULT 1, 
	remark VARCHAR(255) COMMENT '备注', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_organizations PRIMARY KEY (id), 
	CONSTRAINT uq_organizations_parent_name UNIQUE (parent_id, name), 
	CONSTRAINT fk_organizations_parent_id_organizations FOREIGN KEY(parent_id) REFERENCES organizations (id) ON DELETE SET NULL
)COMMENT='组织架构表';
CREATE UNIQUE INDEX ix_organizations_code ON organizations (code);
CREATE INDEX ix_organizations_created_by ON organizations (created_by);
CREATE INDEX ix_organizations_is_deleted ON organizations (is_deleted);
CREATE INDEX ix_organizations_leader_id ON organizations (leader_id);
CREATE INDEX ix_organizations_name ON organizations (name);
CREATE INDEX ix_organizations_parent_id ON organizations (parent_id);
CREATE INDEX ix_organizations_path ON organizations (path);
CREATE TABLE roles (
	id BIGINT NOT NULL COMMENT '角色 ID' AUTO_INCREMENT, 
	code VARCHAR(64) NOT NULL COMMENT '角色编码：super_admin/admin/hr/manager/employee', 
	name VARCHAR(64) NOT NULL COMMENT '角色名称', 
	description VARCHAR(255) COMMENT '角色描述', 
	is_active BOOL NOT NULL COMMENT '是否启用' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	CONSTRAINT pk_roles PRIMARY KEY (id)
)COMMENT='角色表';
CREATE UNIQUE INDEX ix_roles_code ON roles (code);
CREATE TABLE positions (
	title VARCHAR(128) NOT NULL COMMENT '岗位名称', 
	code VARCHAR(64) NOT NULL COMMENT '岗位编码', 
	organization_id BIGINT NOT NULL COMMENT '所属部门 ID（FK -> organizations.id）', 
	level VARCHAR(20) NOT NULL COMMENT '岗位层级：junior/middle/senior/expert' DEFAULT 'middle', 
	headcount INTEGER NOT NULL COMMENT '编制人数' DEFAULT 1, 
	status VARCHAR(20) NOT NULL COMMENT '状态：open/paused/closed（open ⇄ paused，closed 单向）' DEFAULT 'open', 
	description TEXT COMMENT '岗位说明书全文', 
	requirements JSON COMMENT '结构化要求：education/experience_years/skills/certificates/soft_skills/weights', 
	skill_tags JSON COMMENT '技能标签数组', 
	salary_min NUMERIC(12, 2) COMMENT '薪资下限（元/月）', 
	salary_max NUMERIC(12, 2) COMMENT '薪资上限（元/月）', 
	publish_at DATETIME COMMENT '发布时间', 
	close_at DATETIME COMMENT '关闭时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_positions PRIMARY KEY (id), 
	CONSTRAINT fk_positions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id)
)COMMENT='岗位表';
CREATE UNIQUE INDEX ix_positions_code ON positions (code);
CREATE INDEX ix_positions_created_by ON positions (created_by);
CREATE INDEX ix_positions_is_deleted ON positions (is_deleted);
CREATE INDEX ix_positions_level ON positions (level);
CREATE INDEX ix_positions_organization_id_status ON positions (organization_id, status);
CREATE INDEX ix_positions_title ON positions (title);
CREATE TABLE talents (
	name VARCHAR(64) NOT NULL COMMENT '姓名', 
	gender VARCHAR(16) NOT NULL COMMENT '性别：male/female/unknown' DEFAULT 'unknown', 
	birth_date DATE COMMENT '出生日期', 
	mobile VARCHAR(20) COMMENT '手机号', 
	email VARCHAR(128) COMMENT '邮箱', 
	id_card VARCHAR(128) COMMENT '身份证号（AES 加密存储，响应脱敏）', 
	education VARCHAR(20) COMMENT '最高学历', 
	school VARCHAR(128) COMMENT '毕业院校', 
	major VARCHAR(128) COMMENT '专业', 
	work_years INTEGER NOT NULL COMMENT '工作年限' DEFAULT 0, 
	current_company VARCHAR(128) COMMENT '现公司', 
	current_position VARCHAR(128) COMMENT '现职位', 
	organization_id BIGINT COMMENT '归属部门 ID（FK -> organizations.id，SET NULL）', 
	skill_tags JSON COMMENT '技能标签数组', 
	certificates JSON COMMENT '证书列表', 
	self_evaluation TEXT COMMENT '自我评价', 
	status VARCHAR(20) NOT NULL COMMENT '状态：active/left/reserve' DEFAULT 'active', 
	potential_level VARCHAR(8) COMMENT '潜力等级：A/B/C/D（画像生成）', 
	portrait_summary VARCHAR(512) COMMENT '画像摘要', 
	source VARCHAR(20) NOT NULL COMMENT '来源：manual/ai_resume' DEFAULT 'manual', 
	resume_id BIGINT COMMENT '来源简历 ID（逻辑外键 -> resumes.id）', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_talents PRIMARY KEY (id), 
	CONSTRAINT uq_talents_mobile_is_deleted UNIQUE (mobile, is_deleted), 
	CONSTRAINT fk_talents_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE SET NULL
)COMMENT='人才档案表';
CREATE INDEX ix_talents_created_by ON talents (created_by);
CREATE INDEX ix_talents_is_deleted ON talents (is_deleted);
CREATE INDEX ix_talents_name ON talents (name);
CREATE INDEX ix_talents_organization_id_status ON talents (organization_id, status);
CREATE INDEX ix_talents_work_years ON talents (work_years);
CREATE TABLE users (
	username VARCHAR(64) NOT NULL COMMENT '登录名', 
	password_hash VARCHAR(128) NOT NULL COMMENT '密码哈希（bcrypt）', 
	real_name VARCHAR(64) COMMENT '真实姓名', 
	mobile VARCHAR(20) COMMENT '手机号', 
	email VARCHAR(128) COMMENT '邮箱', 
	avatar VARCHAR(255) COMMENT '头像地址', 
	organization_id BIGINT COMMENT '所属部门 ID', 
	position_title VARCHAR(64) COMMENT '岗位名称/职务', 
	is_active BOOL NOT NULL COMMENT '是否启用' DEFAULT 1, 
	is_superuser BOOL NOT NULL COMMENT '是否超管' DEFAULT 0, 
	last_login_at DATETIME COMMENT '最后登录时间', 
	last_login_ip VARCHAR(64) COMMENT '最后登录 IP', 
	fail_count INTEGER NOT NULL COMMENT '连续登录失败次数' DEFAULT 0, 
	locked_until DATETIME COMMENT '锁定截止时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT fk_users_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE SET NULL
)COMMENT='用户账号表';
CREATE INDEX ix_users_created_by ON users (created_by);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_is_deleted ON users (is_deleted);
CREATE UNIQUE INDEX ix_users_mobile ON users (mobile);
CREATE INDEX ix_users_organization_id ON users (organization_id);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE TABLE assessments (
	talent_id BIGINT NOT NULL COMMENT '人才 ID（FK -> talents.id，CASCADE）', 
	position_id BIGINT COMMENT '岗位 ID（FK -> positions.id，SET NULL，可空）', 
	title VARCHAR(128) NOT NULL COMMENT '测评标题', 
	status VARCHAR(20) NOT NULL COMMENT '状态：pending/in_progress/submitted/scored' DEFAULT 'pending', 
	question_count INTEGER NOT NULL COMMENT '题目数量' DEFAULT 0, 
	deadline DATETIME COMMENT '截止时间', 
	started_at DATETIME COMMENT '开始时间', 
	submitted_at DATETIME COMMENT '提交时间', 
	total_score NUMERIC(5, 2) COMMENT '总分（提交后回填）', 
	created_by_ai BOOL NOT NULL COMMENT '是否 AI 生成（千人千卷）' DEFAULT 0, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_assessments PRIMARY KEY (id), 
	CONSTRAINT fk_assessments_talent_id_talents FOREIGN KEY(talent_id) REFERENCES talents (id) ON DELETE CASCADE, 
	CONSTRAINT fk_assessments_position_id_positions FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE SET NULL
)COMMENT='测评任务表';
CREATE INDEX ix_assessments_created_by ON assessments (created_by);
CREATE INDEX ix_assessments_deadline ON assessments (deadline);
CREATE INDEX ix_assessments_is_deleted ON assessments (is_deleted);
CREATE INDEX ix_assessments_talent_id_status ON assessments (talent_id, status);
CREATE TABLE audit_logs (
	user_id BIGINT COMMENT '操作人 ID（逻辑外键 -> users.id，审计保留需要）', 
	username VARCHAR(64) COMMENT '冗余用户名（防用户改名后无法追溯）', 
	action VARCHAR(16) NOT NULL COMMENT '动作：create/update/delete/login/export/ai_call', 
	module VARCHAR(32) COMMENT '模块：talent/position/...', 
	target_type VARCHAR(32) COMMENT '操作对象类型', 
	target_id BIGINT COMMENT '操作对象 ID', 
	request_method VARCHAR(8) COMMENT '请求方法', 
	request_path VARCHAR(255) COMMENT '请求路径', 
	request_ip VARCHAR(64) COMMENT '客户端 IP', 
	user_agent VARCHAR(255) COMMENT 'User-Agent', 
	trace_id VARCHAR(64) COMMENT '链路追踪 ID', 
	before_data JSON COMMENT '变更前数据（敏感字段脱敏）', 
	after_data JSON COMMENT '变更后数据（敏感字段脱敏）', 
	status VARCHAR(16) NOT NULL COMMENT '状态：success/fail' DEFAULT 'success', 
	error_message VARCHAR(512) COMMENT '失败原因', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
	CONSTRAINT fk_audit_logs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
)COMMENT='操作审计日志表';
CREATE INDEX ix_audit_logs_created_by ON audit_logs (created_by);
CREATE INDEX ix_audit_logs_is_deleted ON audit_logs (is_deleted);
CREATE INDEX ix_audit_logs_module_action ON audit_logs (module, action);
CREATE INDEX ix_audit_logs_trace_id ON audit_logs (trace_id);
CREATE INDEX ix_audit_logs_user_id_created_at ON audit_logs (user_id, created_at);
CREATE TABLE conversations (
	user_id BIGINT NOT NULL COMMENT '用户 ID（FK -> users.id，CASCADE）', 
	title VARCHAR(128) COMMENT '会话标题', 
	agent_type VARCHAR(32) NOT NULL COMMENT 'Agent 类型：assistant/resume/talent/matching/assessment/training' DEFAULT 'assistant', 
	status VARCHAR(16) NOT NULL COMMENT '状态：active/archived' DEFAULT 'active', 
	message_count INTEGER NOT NULL COMMENT '消息数' DEFAULT 0, 
	last_message_at DATETIME COMMENT '最后消息时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_conversations PRIMARY KEY (id), 
	CONSTRAINT fk_conversations_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)COMMENT='AI 会话表';
CREATE INDEX ix_conversations_created_by ON conversations (created_by);
CREATE INDEX ix_conversations_is_deleted ON conversations (is_deleted);
CREATE INDEX ix_conversations_user_id ON conversations (user_id);
CREATE INDEX ix_conversations_user_id_last_message_at ON conversations (user_id, last_message_at);
CREATE TABLE files (
	file_name VARCHAR(255) NOT NULL COMMENT '原始文件名', 
	stored_name VARCHAR(128) NOT NULL COMMENT '存储名（uuid + 扩展名）', 
	file_path VARCHAR(512) NOT NULL COMMENT '相对路径：{biz_type}/{yyyy}/{mm}/xxx', 
	file_size BIGINT NOT NULL COMMENT '字节数' DEFAULT 0, 
	content_type VARCHAR(128) COMMENT 'MIME 类型', 
	extension VARCHAR(16) COMMENT '扩展名（含点）', 
	file_hash VARCHAR(64) COMMENT 'sha256，秒传与查重', 
	storage_type VARCHAR(16) NOT NULL COMMENT '存储方式：local/oss' DEFAULT 'local', 
	biz_type VARCHAR(32) NOT NULL COMMENT '业务类型：resume/knowledge/avatar/export', 
	biz_id BIGINT COMMENT '关联业务 ID（多态）', 
	uploader_id BIGINT COMMENT '上传人（FK -> users.id，SET NULL）', 
	is_public BOOL NOT NULL COMMENT '是否公开访问' DEFAULT 0, 
	download_count INTEGER NOT NULL COMMENT '下载次数' DEFAULT 0, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_files PRIMARY KEY (id), 
	CONSTRAINT fk_files_uploader_id_users FOREIGN KEY(uploader_id) REFERENCES users (id) ON DELETE SET NULL
)COMMENT='文件元数据表';
CREATE INDEX ix_files_biz_type_biz_id ON files (biz_type, biz_id);
CREATE INDEX ix_files_created_by ON files (created_by);
CREATE INDEX ix_files_file_hash ON files (file_hash);
CREATE INDEX ix_files_is_deleted ON files (is_deleted);
CREATE INDEX ix_files_uploader_id ON files (uploader_id);
CREATE TABLE position_requirements (
	position_id BIGINT NOT NULL COMMENT '岗位 ID（FK -> positions.id，CASCADE）', 
	version INTEGER NOT NULL COMMENT '版本号（同岗位递增）' DEFAULT 1, 
	dimensions JSON COMMENT '维度定义：技能/经验/学历/素质', 
	weights JSON COMMENT '维度权重（合计 1.0）', 
	source VARCHAR(20) NOT NULL COMMENT '来源：manual/ai_parse' DEFAULT 'manual', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_position_requirements PRIMARY KEY (id), 
	CONSTRAINT uq_position_requirements_position_version UNIQUE (position_id, version), 
	CONSTRAINT fk_position_requirements_position_id_positions FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE
)COMMENT='岗位能力模型表（多版本）';
CREATE INDEX ix_position_requirements_created_by ON position_requirements (created_by);
CREATE INDEX ix_position_requirements_is_deleted ON position_requirements (is_deleted);
CREATE INDEX ix_position_requirements_position_id ON position_requirements (position_id);
CREATE TABLE talent_portraits (
	talent_id BIGINT NOT NULL COMMENT '人才 ID（FK -> talents.id，CASCADE）', 
	version INTEGER NOT NULL COMMENT '画像版本（每次重算 +1）' DEFAULT 1, 
	dimensions JSON COMMENT '维度分：skill/experience/quality/potential，0-100', 
	labels JSON COMMENT 'AI 标签数组', 
	strengths JSON COMMENT '优势列表', 
	weaknesses JSON COMMENT '短板列表', 
	suggested_positions JSON COMMENT '建议岗位', 
	potential_level VARCHAR(8) COMMENT '潜力等级', 
	model_name VARCHAR(64) COMMENT '生成模型', 
	prompt_version VARCHAR(32) COMMENT '提示词版本', 
	generated_at DATETIME COMMENT '生成时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_talent_portraits PRIMARY KEY (id), 
	CONSTRAINT uq_talent_portraits_talent_version UNIQUE (talent_id, version), 
	CONSTRAINT fk_talent_portraits_talent_id_talents FOREIGN KEY(talent_id) REFERENCES talents (id) ON DELETE CASCADE
)COMMENT='人才画像表（版本化）';
CREATE INDEX ix_talent_portraits_created_by ON talent_portraits (created_by);
CREATE INDEX ix_talent_portraits_generated_at ON talent_portraits (generated_at);
CREATE INDEX ix_talent_portraits_is_deleted ON talent_portraits (is_deleted);
CREATE INDEX ix_talent_portraits_talent_id ON talent_portraits (talent_id);
CREATE TABLE training_plans (
	name VARCHAR(128) NOT NULL COMMENT '计划名称', 
	talent_id BIGINT NOT NULL COMMENT '人才 ID（FK -> talents.id，CASCADE）', 
	goal VARCHAR(512) COMMENT '培养目标', 
	start_date DATE COMMENT '开始日期', 
	end_date DATE COMMENT '结束日期', 
	status VARCHAR(20) NOT NULL COMMENT '状态：draft/ongoing/finished/cancelled' DEFAULT 'draft', 
	created_by_ai BOOL NOT NULL COMMENT '是否 AI 生成' DEFAULT 0, 
	source_assessment_id BIGINT COMMENT '能力缺口来源测评 ID（逻辑外键 -> assessments.id）', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_training_plans PRIMARY KEY (id), 
	CONSTRAINT fk_training_plans_talent_id_talents FOREIGN KEY(talent_id) REFERENCES talents (id) ON DELETE CASCADE
)COMMENT='培训计划表';
CREATE INDEX ix_training_plans_created_by ON training_plans (created_by);
CREATE INDEX ix_training_plans_is_deleted ON training_plans (is_deleted);
CREATE INDEX ix_training_plans_talent_id_status ON training_plans (talent_id, status);
CREATE TABLE user_roles (
	user_id BIGINT NOT NULL COMMENT '用户 ID', 
	role_id BIGINT NOT NULL COMMENT '角色 ID', 
	created_at DATETIME NOT NULL COMMENT '绑定时间' DEFAULT CURRENT_TIMESTAMP, 
	CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id), 
	CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_user_roles_role_id_roles FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
)COMMENT='用户角色关联表';
CREATE TABLE assessment_questions (
	assessment_id BIGINT NOT NULL COMMENT '测评 ID（FK -> assessments.id，CASCADE）', 
	type VARCHAR(16) NOT NULL COMMENT '题型：single/multiple/text' DEFAULT 'single', 
	content TEXT NOT NULL COMMENT '题干', 
	options JSON COMMENT '选项 [{key,text}]（客观题）', 
	answer JSON COMMENT '标准答案（绝不返回给前端）', 
	score NUMERIC(5, 2) NOT NULL COMMENT '分值' DEFAULT 0, 
	dimension VARCHAR(32) COMMENT '考察维度（雷达图）', 
	sort INTEGER NOT NULL COMMENT '展示顺序' DEFAULT 0, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_assessment_questions PRIMARY KEY (id), 
	CONSTRAINT fk_assessment_questions_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE CASCADE
)COMMENT='测评题目表';
CREATE INDEX ix_assessment_questions_assessment_sort ON assessment_questions (assessment_id, sort);
CREATE INDEX ix_assessment_questions_created_by ON assessment_questions (created_by);
CREATE INDEX ix_assessment_questions_is_deleted ON assessment_questions (is_deleted);
CREATE TABLE assessment_results (
	assessment_id BIGINT NOT NULL COMMENT '测评 ID（唯一，FK -> assessments.id，CASCADE）', 
	total_score NUMERIC(5, 2) COMMENT '总分', 
	dimension_scores JSON COMMENT '维度分（雷达图数据源）', 
	level VARCHAR(16) COMMENT '等级：初级/中级/高级/专家', 
	report JSON COMMENT 'AI 研判报告', 
	suggestions JSON COMMENT '提升建议', 
	model_name VARCHAR(64) COMMENT '模型（可追溯）', 
	prompt_version VARCHAR(32) COMMENT '提示词版本', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_assessment_results PRIMARY KEY (id), 
	CONSTRAINT fk_assessment_results_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE CASCADE
)COMMENT='测评结果表';
CREATE UNIQUE INDEX ix_assessment_results_assessment_id ON assessment_results (assessment_id);
CREATE INDEX ix_assessment_results_created_by ON assessment_results (created_by);
CREATE INDEX ix_assessment_results_is_deleted ON assessment_results (is_deleted);
CREATE INDEX ix_assessment_results_level ON assessment_results (level);
CREATE TABLE chat_messages (
	conversation_id BIGINT NOT NULL COMMENT '会话 ID（FK -> conversations.id，CASCADE）', 
	`role` VARCHAR(16) NOT NULL COMMENT '角色：user/assistant/system/tool' DEFAULT 'user', 
	content TEXT COMMENT '消息内容', 
	tool_calls JSON COMMENT '工具调用记录', 
	citations JSON COMMENT 'RAG 引用：doc_id/chunk/page/score', 
	prompt_tokens INTEGER NOT NULL COMMENT '提示 token 数' DEFAULT 0, 
	completion_tokens INTEGER NOT NULL COMMENT '生成 token 数' DEFAULT 0, 
	model_name VARCHAR(64) COMMENT '模型', 
	elapsed_ms INTEGER COMMENT '耗时（毫秒）', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_chat_messages PRIMARY KEY (id), 
	CONSTRAINT fk_chat_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
)COMMENT='会话消息表';
CREATE INDEX ix_chat_messages_conversation_created ON chat_messages (conversation_id, created_at);
CREATE INDEX ix_chat_messages_conversation_id ON chat_messages (conversation_id);
CREATE INDEX ix_chat_messages_created_by ON chat_messages (created_by);
CREATE INDEX ix_chat_messages_is_deleted ON chat_messages (is_deleted);
CREATE TABLE knowledge_docs (
	title VARCHAR(255) NOT NULL COMMENT '文档标题', 
	category VARCHAR(64) COMMENT '分类', 
	source_type VARCHAR(16) NOT NULL COMMENT '来源类型：file/text' DEFAULT 'text', 
	file_id BIGINT COMMENT '文件 ID（FK -> files.id，SET NULL，source_type=text 时为空）', 
	status VARCHAR(20) NOT NULL COMMENT '索引状态：indexing/indexed/failed' DEFAULT 'indexing', 
	chunk_count INTEGER NOT NULL COMMENT '切片数量' DEFAULT 0, 
	permission_scope VARCHAR(16) NOT NULL COMMENT '权限范围：public/dept/role' DEFAULT 'public', 
	allowed_roles JSON COMMENT '允许访问的角色编码（permission_scope=role 时生效）', 
	organization_id BIGINT COMMENT '部门范围（permission_scope=dept 时生效）', 
	embedding_model VARCHAR(64) COMMENT '向量模型', 
	index_error VARCHAR(512) COMMENT '索引失败原因', 
	indexed_at DATETIME COMMENT '索引完成时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_knowledge_docs PRIMARY KEY (id), 
	CONSTRAINT fk_knowledge_docs_file_id_files FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL
)COMMENT='知识文档表';
CREATE INDEX ix_knowledge_docs_category ON knowledge_docs (category);
CREATE INDEX ix_knowledge_docs_created_by ON knowledge_docs (created_by);
CREATE INDEX ix_knowledge_docs_is_deleted ON knowledge_docs (is_deleted);
CREATE INDEX ix_knowledge_docs_organization_id ON knowledge_docs (organization_id);
CREATE INDEX ix_knowledge_docs_status ON knowledge_docs (status);
CREATE INDEX ix_knowledge_docs_title ON knowledge_docs (title);
CREATE TABLE learning_records (
	plan_id BIGINT COMMENT '计划 ID（FK -> training_plans.id，SET NULL）', 
	course_id BIGINT COMMENT '课程 ID（FK -> courses.id，SET NULL）', 
	talent_id BIGINT NOT NULL COMMENT '人才 ID（FK -> talents.id，CASCADE）', 
	progress INTEGER NOT NULL COMMENT '进度 0-100（单调递增）' DEFAULT 0, 
	study_minutes INTEGER NOT NULL COMMENT '累计学习分钟' DEFAULT 0, 
	last_study_at DATETIME COMMENT '最近学习时间', 
	status VARCHAR(20) NOT NULL COMMENT '状态：not_started/learning/completed' DEFAULT 'not_started', 
	score NUMERIC(5, 2) COMMENT '考核分数', 
	passed BOOL COMMENT '是否通过', 
	suspicious BOOL NOT NULL COMMENT '反作弊标记（学时与进度比例异常）' DEFAULT 0, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_learning_records PRIMARY KEY (id), 
	CONSTRAINT uq_learning_records_plan_course_talent UNIQUE (plan_id, course_id, talent_id), 
	CONSTRAINT fk_learning_records_plan_id_training_plans FOREIGN KEY(plan_id) REFERENCES training_plans (id) ON DELETE SET NULL, 
	CONSTRAINT fk_learning_records_course_id_courses FOREIGN KEY(course_id) REFERENCES courses (id) ON DELETE SET NULL, 
	CONSTRAINT fk_learning_records_talent_id_talents FOREIGN KEY(talent_id) REFERENCES talents (id) ON DELETE CASCADE
)COMMENT='学习记录表';
CREATE INDEX ix_learning_records_created_by ON learning_records (created_by);
CREATE INDEX ix_learning_records_is_deleted ON learning_records (is_deleted);
CREATE INDEX ix_learning_records_talent_id_status ON learning_records (talent_id, status);
CREATE TABLE plan_courses (
	plan_id BIGINT NOT NULL COMMENT '计划 ID（FK -> training_plans.id，CASCADE）', 
	course_id BIGINT NOT NULL COMMENT '课程 ID（FK -> courses.id，CASCADE）', 
	sort INTEGER NOT NULL COMMENT '学习顺序' DEFAULT 0, 
	require_pass BOOL NOT NULL COMMENT '是否必须通过考核' DEFAULT 0, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	CONSTRAINT pk_plan_courses PRIMARY KEY (plan_id, course_id), 
	CONSTRAINT uq_plan_courses_plan_course UNIQUE (plan_id, course_id), 
	CONSTRAINT fk_plan_courses_plan_id_training_plans FOREIGN KEY(plan_id) REFERENCES training_plans (id) ON DELETE CASCADE, 
	CONSTRAINT fk_plan_courses_course_id_courses FOREIGN KEY(course_id) REFERENCES courses (id) ON DELETE CASCADE
)COMMENT='培训计划-课程关联表';
CREATE INDEX ix_plan_courses_course_id ON plan_courses (course_id);
CREATE TABLE resumes (
	talent_id BIGINT COMMENT '人才 ID（FK -> talents.id，SET NULL，解析后回填）', 
	file_id BIGINT COMMENT '文件 ID（FK -> files.id，SET NULL）', 
	file_name VARCHAR(255) NOT NULL COMMENT '原始文件名', 
	file_type VARCHAR(32) COMMENT '文件类型：pdf/docx/doc/xlsx/txt/png/jpg', 
	file_size BIGINT NOT NULL COMMENT '字节数' DEFAULT 0, 
	file_hash VARCHAR(64) NOT NULL COMMENT 'sha256（秒传/查重，唯一）', 
	status VARCHAR(20) NOT NULL COMMENT '状态：uploaded/parsing/success/failed' DEFAULT 'uploaded', 
	accuracy NUMERIC(4, 3) COMMENT '解析置信度 0~1', 
	parsed_data JSON COMMENT '结构化简历：basic_info/education/work_experience/projects/skills', 
	parse_error VARCHAR(512) COMMENT '解析失败原因', 
	parsed_at DATETIME COMMENT '解析完成时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_resumes PRIMARY KEY (id), 
	CONSTRAINT fk_resumes_talent_id_talents FOREIGN KEY(talent_id) REFERENCES talents (id) ON DELETE SET NULL, 
	CONSTRAINT fk_resumes_file_id_files FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL
)COMMENT='简历表';
CREATE INDEX ix_resumes_created_by ON resumes (created_by);
CREATE UNIQUE INDEX ix_resumes_file_hash ON resumes (file_hash);
CREATE INDEX ix_resumes_is_deleted ON resumes (is_deleted);
CREATE INDEX ix_resumes_status ON resumes (status);
CREATE INDEX ix_resumes_talent_id ON resumes (talent_id);
CREATE TABLE assessment_answers (
	assessment_id BIGINT NOT NULL COMMENT '测评 ID（FK -> assessments.id，CASCADE）', 
	question_id BIGINT NOT NULL COMMENT '题目 ID（FK -> assessment_questions.id，CASCADE）', 
	answer JSON COMMENT '用户答案', 
	score NUMERIC(5, 2) COMMENT '本题得分', 
	scored_by VARCHAR(16) COMMENT '评分方式：rule/ai', 
	scored_at DATETIME COMMENT '评分时间', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_assessment_answers PRIMARY KEY (id), 
	CONSTRAINT uq_assessment_answers_assessment_question UNIQUE (assessment_id, question_id), 
	CONSTRAINT fk_assessment_answers_assessment_id_assessments FOREIGN KEY(assessment_id) REFERENCES assessments (id) ON DELETE CASCADE, 
	CONSTRAINT fk_assessment_answers_question_id_assessment_questions FOREIGN KEY(question_id) REFERENCES assessment_questions (id) ON DELETE CASCADE
)COMMENT='测评答题记录表';
CREATE INDEX ix_assessment_answers_created_by ON assessment_answers (created_by);
CREATE INDEX ix_assessment_answers_is_deleted ON assessment_answers (is_deleted);
CREATE TABLE knowledge_chunks (
	doc_id BIGINT NOT NULL COMMENT '文档 ID（FK -> knowledge_docs.id，CASCADE）', 
	chunk_index INTEGER NOT NULL COMMENT '切片序号', 
	content TEXT NOT NULL COMMENT '切片原文', 
	token_count INTEGER COMMENT 'token 数', 
	milvus_pk BIGINT COMMENT 'Milvus 主键（回表用）', 
	page_no INTEGER COMMENT '页码（引用溯源）', 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id), 
	CONSTRAINT uq_knowledge_chunks_doc_index UNIQUE (doc_id, chunk_index), 
	CONSTRAINT fk_knowledge_chunks_doc_id_knowledge_docs FOREIGN KEY(doc_id) REFERENCES knowledge_docs (id) ON DELETE CASCADE
)COMMENT='知识切片表';
CREATE INDEX ix_knowledge_chunks_created_by ON knowledge_chunks (created_by);
CREATE INDEX ix_knowledge_chunks_doc_id ON knowledge_chunks (doc_id);
CREATE INDEX ix_knowledge_chunks_is_deleted ON knowledge_chunks (is_deleted);
CREATE INDEX ix_knowledge_chunks_milvus_pk ON knowledge_chunks (milvus_pk);
CREATE TABLE resume_experiences (
	resume_id BIGINT NOT NULL COMMENT '简历 ID（FK -> resumes.id，CASCADE）', 
	company VARCHAR(128) NOT NULL COMMENT '公司名称', 
	title VARCHAR(128) COMMENT '职位', 
	start_date DATE COMMENT '开始日期', 
	end_date DATE COMMENT '结束日期（空表示至今）', 
	description TEXT COMMENT '工作内容', 
	sort INTEGER NOT NULL COMMENT '展示顺序' DEFAULT 0, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_resume_experiences PRIMARY KEY (id), 
	CONSTRAINT fk_resume_experiences_resume_id_resumes FOREIGN KEY(resume_id) REFERENCES resumes (id) ON DELETE CASCADE
)COMMENT='简历工作经历表';
CREATE INDEX ix_resume_experiences_company ON resume_experiences (company);
CREATE INDEX ix_resume_experiences_created_by ON resume_experiences (created_by);
CREATE INDEX ix_resume_experiences_is_deleted ON resume_experiences (is_deleted);
CREATE INDEX ix_resume_experiences_resume_id ON resume_experiences (resume_id);
