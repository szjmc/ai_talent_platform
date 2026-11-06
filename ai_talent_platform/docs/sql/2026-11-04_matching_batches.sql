CREATE TABLE matching_batches (
	batch_id VARCHAR(32) NOT NULL, 
	position_id BIGINT NOT NULL, 
	matched_at DATETIME NOT NULL, 
	response JSON NOT NULL, 
	request_scope JSON NOT NULL, 
	id BIGINT NOT NULL COMMENT '主键 ID（BIGINT，主键索引由 PK 提供）' AUTO_INCREMENT, 
	version INTEGER NOT NULL COMMENT '乐观锁版本号，每次更新 +1' DEFAULT 1, 
	created_at DATETIME NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP, 
	is_deleted BOOL NOT NULL COMMENT '是否已删除（逻辑删除）' DEFAULT 0, 
	deleted_at DATETIME COMMENT '删除时间', 
	created_by BIGINT COMMENT '创建人用户 ID', 
	updated_by BIGINT COMMENT '最后修改人用户 ID', 
	CONSTRAINT pk_matching_batches PRIMARY KEY (id), 
	CONSTRAINT uq_matching_batches_batch_id UNIQUE (batch_id)
);

CREATE INDEX ix_matching_batches_created_by ON matching_batches (created_by);

CREATE INDEX ix_matching_batches_is_deleted ON matching_batches (is_deleted);

CREATE INDEX ix_matching_batches_matched_at ON matching_batches (matched_at);

CREATE INDEX ix_matching_batches_position_id ON matching_batches (position_id);
