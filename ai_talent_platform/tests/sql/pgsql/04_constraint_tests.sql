-- ============================================================================
-- 04_constraint_tests.sql — PgSQL 约束行为测试（CHECK / UNIQUE / NOT NULL / 分区 / Identity）
-- 标签：[Vanna组] [P0]（分析数据质量红线：越界分数/负数学时/重复维度必须被拒绝）
-- 依赖：01（断言函数）、02（种子数据）。负向用例期望 SQLSTATE：
--   23514 CHECK 违反 / 23505 唯一违反 / 23502 非空违反。
-- ============================================================================

DO $$
BEGIN
  IF current_database() NOT LIKE '%test%'
     AND coalesce(current_setting('sqltest.allow_non_test_db', true), '') <> 'on' THEN
    RAISE EXCEPTION 'SQL 测试只允许在名称含 test 的数据库执行（如 ai_talent_analytics_test）；确需其他库请先 SET sqltest.allow_non_test_db = on';
  END IF;
END $$;
DELETE FROM sqltest_results WHERE test_id LIKE 'CON-%';

-- ---------------------------------------------------------------- CHECK 约束（期望 23514）
SELECT sqltest_expect_error('CON-001', '快照 talent_count 不能为负', $sql$INSERT INTO fact_talent_snapshot (id, snapshot_month, organization_id, education, age_group, talent_count) VALUES (900091,'2026-10-01',900002,'博士','30-40',-1)$sql$, '23514');
SELECT sqltest_expect_error('CON-002', '快照 avg_work_years 不能为负', $sql$INSERT INTO fact_talent_snapshot (id, snapshot_month, organization_id, education, age_group, talent_count, avg_work_years) VALUES (900091,'2026-10-01',900002,'博士','30-40',1,-1)$sql$, '23514');
SELECT sqltest_expect_error('CON-003', '测评 total_score 不能超过 100', $sql$INSERT INTO fact_assessment_history (id, assessment_id, talent_id, total_score, scored_at) VALUES (900091,900091,900001,101,'2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-004', '测评 total_score 不能为负', $sql$INSERT INTO fact_assessment_history (id, assessment_id, talent_id, total_score, scored_at) VALUES (900091,900091,900001,-1,'2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-005', '培训 progress 不能超过 100', $sql$INSERT INTO fact_training_track (id, talent_id, course_id, progress, study_minutes, status, event_at) VALUES (900091,900001,900001,101,0,'learning','2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-006', '培训 progress 不能为负', $sql$INSERT INTO fact_training_track (id, talent_id, course_id, progress, study_minutes, status, event_at) VALUES (900091,900001,900001,-1,0,'learning','2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-007', '培训 study_minutes 不能为负', $sql$INSERT INTO fact_training_track (id, talent_id, course_id, progress, study_minutes, status, event_at) VALUES (900091,900001,900001,50,-5,'learning','2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-008', '行为 duration_ms 不能为负', $sql$INSERT INTO fact_behavior_event (id, talent_id, event_type, duration_ms, event_at) VALUES (900091,900001,'page_view',-1,'2026-10-25 10:00:00+08')$sql$, '23514');
SELECT sqltest_expect_error('CON-009', '组织 level 必须 >= 1', $sql$INSERT INTO dim_organization (org_id, code, name, level, path) VALUES (900091,'sqltest_bad','x',0,'/')$sql$, '23514');
SELECT sqltest_expect_error('CON-010', '人才 work_years 不能为负', $sql$INSERT INTO dim_talent_wide (talent_id, name, work_years) VALUES (900091,'x',-1)$sql$, '23514');
SELECT sqltest_expect_error('CON-011', '人才 match_avg_score 不能超过 100', $sql$INSERT INTO dim_talent_wide (talent_id, name, match_avg_score) VALUES (900091,'x',101)$sql$, '23514');

-- ---------------------------------------------------------------- 唯一 / 主键（期望 23505）
SELECT sqltest_expect_error('CON-020', '组织 code 唯一', $sql$INSERT INTO dim_organization (org_id, code, name, level, path) VALUES (900091,'sqltest_hq','x',1,'/')$sql$, '23505');
SELECT sqltest_expect_error('CON-021', '人才宽表主键唯一', $sql$INSERT INTO dim_talent_wide (talent_id, name) VALUES (900001,'重复人才')$sql$, '23505');
SELECT sqltest_expect_error('CON-022', '快照维度组合唯一', $sql$INSERT INTO fact_talent_snapshot (id, snapshot_month, organization_id, education, age_group, talent_count) VALUES (900091,'2026-10-01',900002,'本科','25-30',1)$sql$, '23505');

-- ---------------------------------------------------------------- 非空（期望 23502）
SELECT sqltest_expect_error('CON-030', '人才 name 非空', $sql$INSERT INTO dim_talent_wide (talent_id, name) VALUES (900091,NULL)$sql$, '23502');
SELECT sqltest_expect_error('CON-031', '培训 status 非空', $sql$INSERT INTO fact_training_track (id, talent_id, course_id, progress, study_minutes, status, event_at) VALUES (900091,900001,900001,0,0,NULL,'2026-10-25 10:00:00+08')$sql$, '23502');
SELECT sqltest_expect_error('CON-032', '快照 talent_count 非空', $sql$INSERT INTO fact_talent_snapshot (id, snapshot_month, organization_id, education, age_group, talent_count) VALUES (900091,'2026-10-01',900002,'博士','30-40',NULL)$sql$, '23502');

-- ---------------------------------------------------------------- 月份分区路由（建分区->写入->验证->删除）
CREATE TABLE public.fact_behavior_event_2026_11 PARTITION OF public.fact_behavior_event
  FOR VALUES FROM ('2026-11-01 00:00:00+08') TO ('2026-12-01 00:00:00+08');
INSERT INTO fact_behavior_event (id, talent_id, event_type, event_at) VALUES (900099, 900001, 'page_view', '2026-11-15 08:00:00+08');
SELECT sqltest_check('CON-040', '新月份分区正确路由', $sql$SELECT c.relname FROM pg_class c WHERE c.oid=(SELECT tableoid FROM fact_behavior_event WHERE id=900099)$sql$, 'fact_behavior_event_2026_11');
DROP TABLE public.fact_behavior_event_2026_11;

-- ---------------------------------------------------------------- Identity 自增 / 边界值
DO $$
DECLARE v_id bigint;
BEGIN
  INSERT INTO fact_behavior_event (talent_id, event_type, event_at) VALUES (900001, 'identity_test', '2026-10-20 00:00:00+08') RETURNING id INTO v_id;
  INSERT INTO sqltest_results (test_id, test_name, passed, detail)
  VALUES ('CON-041', 'Identity 自动生成 id', v_id IS NOT NULL AND v_id > 0, '生成id=' || v_id);
  DELETE FROM fact_behavior_event WHERE id = v_id;
END $$;
INSERT INTO fact_assessment_history (id, assessment_id, talent_id, total_score, scored_at) VALUES (900092, 900092, 900001, 0, '2026-10-26 10:00:00+08');
INSERT INTO fact_assessment_history (id, assessment_id, talent_id, total_score, scored_at) VALUES (900093, 900093, 900001, 100, '2026-10-26 11:00:00+08');
SELECT sqltest_check('CON-042', 'CHECK 边界值 0 与 100 允许写入', $sql$SELECT count(*)::text FROM fact_assessment_history WHERE id IN (900092,900093)$sql$, '2');
DELETE FROM fact_assessment_history WHERE id IN (900092,900093);
INSERT INTO fact_assessment_history (id, assessment_id, talent_id, total_score, scored_at) VALUES (900094, 900094, 900001, NULL, '2026-10-26 12:00:00+08');
SELECT sqltest_check('CON-043', 'CHECK 允许 NULL 分数（未评分）', $sql$SELECT (total_score IS NULL)::text FROM fact_assessment_history WHERE id=900094$sql$, 'true');
DELETE FROM fact_assessment_history WHERE id = 900094;
