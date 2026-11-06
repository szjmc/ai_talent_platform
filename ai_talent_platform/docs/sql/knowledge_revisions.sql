
CREATE TABLE knowledge_revisions (
	doc_id BIGINT NOT NULL COMMENT '稳定业务文档ID', 
	revision_no INTEGER NOT NULL COMMENT '业务版本号，与乐观锁version不同', 
	parent_revision_id BIGINT COMMENT '来源版本；Service必须校验属于同一文档', 
	source_file_id BIGINT COMMENT '上传原件', 
	file_id BIGINT COMMENT '该版本下载文件；修改后生成Word', 
	body_file_id BIGINT COMMENT '完整正文文件，复用files与MinIO', 
	preview_file_id BIGINT COMMENT '切片预览及来源快照文件', 
	title VARCHAR(255) NOT NULL COMMENT '版本标题快照', 
	category VARCHAR(64) NOT NULL COMMENT '版本分类快照', 
	status VARCHAR(16) NOT NULL DEFAULT 'draft', 
	content_hash VARCHAR(64) COMMENT '正文SHA256', 
	preview_hash VARCHAR(64) COMMENT '正文及分块配置摘要；编辑后必须失效', 
	index_manifest BIGINT COMMENT '索引发布清单文件：切片ID、集合、模型及配置', 
	published_by BIGINT, 
	published_at DATETIME, 
	error VARCHAR(512) COMMENT '发布失败原因，不保存密钥或原始个人数据', 
	active_marker INTEGER GENERATED ALWAYS AS (CASE WHEN status = 'published' AND is_deleted = 0 THEN 1 ELSE NULL END) STORED, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_knowledge_revisions PRIMARY KEY (id), 
	CONSTRAINT uq_knowledge_revision_number UNIQUE (doc_id, revision_no), 
	CONSTRAINT uq_knowledge_revision_active UNIQUE (doc_id, active_marker), 
	CONSTRAINT ck_knowledge_revisions_knowledge_revision_positive CHECK (revision_no > 0), 
	CONSTRAINT ck_knowledge_revisions_knowledge_revision_status CHECK (status in ('draft','previewed','publishing','published','archived','failed')), 
	CONSTRAINT fk_knowledge_revisions_doc_id_knowledge_docs FOREIGN KEY(doc_id) REFERENCES knowledge_docs (id) ON DELETE CASCADE, 
	CONSTRAINT fk_knowledge_revisions_parent_revision_id_knowledge_revisions FOREIGN KEY(parent_revision_id) REFERENCES knowledge_revisions (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_source_file_id_files FOREIGN KEY(source_file_id) REFERENCES files (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_file_id_files FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_body_file_id_files FOREIGN KEY(body_file_id) REFERENCES files (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_preview_file_id_files FOREIGN KEY(preview_file_id) REFERENCES files (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_index_manifest_files FOREIGN KEY(index_manifest) REFERENCES files (id) ON DELETE SET NULL, 
	CONSTRAINT fk_knowledge_revisions_published_by_users FOREIGN KEY(published_by) REFERENCES users (id) ON DELETE SET NULL
)ENGINE=InnoDB CHARSET=utf8mb4 COMMENT='知识文档草稿及历史版本，复用原有文件表' COLLATE utf8mb4_unicode_ci

;

CREATE INDEX ix_knowledge_revisions_created_by ON knowledge_revisions (created_by);

CREATE INDEX ix_knowledge_revisions_doc_id ON knowledge_revisions (doc_id);

CREATE INDEX ix_knowledge_revisions_is_deleted ON knowledge_revisions (is_deleted);

CREATE INDEX ix_knowledge_revisions_status ON knowledge_revisions (status);