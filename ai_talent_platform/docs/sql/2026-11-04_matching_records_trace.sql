-- ============================================================================
-- 匹配记录可追溯字段变更（MATCH-009）
-- 目标表：matching_records（MySQL 8.0）
-- 变更性质：仅新增可空列，不修改既有列类型 / 不调整唯一键，对存量数据与查询完全兼容。
-- 执行前提：已备份数据库；确认无长事务锁表（MySQL 8 加列默认 INSTANT，秒级完成）。
-- 执行方式：
--   1) 已有环境：直接执行本文件；
--   2) 全新环境：无需执行，应用初始化 create_all 会按模型建出这些列。
-- 回滚：见文件末尾 ROLLBACK 段。
-- ============================================================================

ALTER TABLE `matching_records`
    ADD COLUMN `requirement_version` INT NULL COMMENT '本次匹配使用的岗位画像版本' AFTER `matched_at`,
    ADD COLUMN `recall_mode` VARCHAR(16) NULL COMMENT '召回方式：vector / sql_fallback（如实记录）' AFTER `requirement_version`,
    ADD COLUMN `hard_results` JSON NULL COMMENT '四态判定明细（HardConditionResult 列表）' AFTER `recall_mode`,
    ADD COLUMN `explanation` JSON NULL COMMENT '校验后的匹配解释（MatchExplanation）' AFTER `hard_results`,
    ADD COLUMN `stats` JSON NULL COMMENT '跑批统计（候选/淘汰/待补全/输出 + 权重 + 告警）' AFTER `explanation`;

-- 可选：按批次 + 人才检索（解释重算、幂等校验）
-- ALTER TABLE `matching_records`
--     ADD INDEX `ix_matching_records_batch_talent` (`batch_id`, `talent_id`);

-- ----------------------------------------------------------------------------
-- ROLLBACK（仅在需要退回旧结构时执行；会丢失新增字段数据）
-- ----------------------------------------------------------------------------
-- ALTER TABLE `matching_records`
--     DROP COLUMN `stats`,
--     DROP COLUMN `explanation`,
--     DROP COLUMN `hard_results`,
--     DROP COLUMN `recall_mode`,
--     DROP COLUMN `requirement_version`;
