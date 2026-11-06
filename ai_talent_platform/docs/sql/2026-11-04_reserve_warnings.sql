-- ============================================================================
-- 岗位储备预警表（需求输出版 §07：数据闭环）
-- 日期：2026-11-04
-- 说明：
--   1) 新库由 create_all 自动建表，无需执行本脚本；
--   2) 已有库（开发/测试/生产）执行本脚本一次即可，可重复执行（IF NOT EXISTS）；
--   3) 预警是派生数据，逻辑外键不建物理约束；源数据删除后记录保留可追溯。
-- ============================================================================

CREATE TABLE IF NOT EXISTS `reserve_warnings` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    `warning_type` VARCHAR(32)  NOT NULL COMMENT '预警类型：position_no_output/position_reserve_shortage/position_long_vacant',
    `severity`     VARCHAR(16)  NOT NULL DEFAULT 'medium' COMMENT '严重级别：high/medium/low',
    `position_id`  BIGINT       NULL COMMENT '岗位 ID（逻辑外键）',
    `talent_id`    BIGINT       NULL COMMENT '人才 ID（逻辑外键，预留人才侧预警）',
    `batch_id`     VARCHAR(32)  NULL COMMENT '触发预警的跑批 ID',
    `message`      VARCHAR(256) NOT NULL COMMENT '人可读的预警摘要',
    `detail`       JSON         NULL COMMENT '预警明细（计数、阈值、淘汰原因摘要等）',
    `resolved`     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已解决',
    `resolved_at`  DATETIME     NULL COMMENT '解决时间',
    `resolved_by`  BIGINT       NULL COMMENT '解决人用户 ID（自动闭环时为 NULL）',
    `version`      INT          NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
    `is_deleted`   TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    `deleted_at`   DATETIME     NULL COMMENT '删除时间',
    `created_by`   BIGINT       NULL COMMENT '创建人',
    `updated_by`   BIGINT       NULL COMMENT '更新人',
    `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    INDEX `ix_reserve_warnings_position_id` (`position_id`),
    INDEX `ix_reserve_warnings_position_type_resolved` (`position_id`, `warning_type`, `resolved`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '岗位储备预警记录表';
