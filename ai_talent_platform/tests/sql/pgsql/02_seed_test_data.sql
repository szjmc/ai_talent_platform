-- ============================================================================
-- 02_seed_test_data.sql — PgSQL 分析库测试数据（覆盖 6 张表）
-- 标签：[Vanna组] [P1]（分析数据底座：组织维表/人才宽表/快照/测评历史/培训轨迹/行为事件）
--
-- 约定：固定 ID 段 900000-900999，可重复执行（先清理再插入）。
-- 依赖：01（结果表/护栏/断言函数）。fact 表显式指定 id（Identity BY DEFAULT 允许）。
-- ============================================================================

DO $$
BEGIN
  IF current_database() NOT LIKE '%test%'
     AND coalesce(current_setting('sqltest.allow_non_test_db', true), '') <> 'on' THEN
    RAISE EXCEPTION 'SQL 测试只允许在名称含 test 的数据库执行（如 ai_talent_analytics_test）；确需其他库请先 SET sqltest.allow_non_test_db = on';
  END IF;
END $$;
DELETE FROM sqltest_results WHERE test_id LIKE 'DAT-%';
-- 清理旧测试数据（幂等）
DELETE FROM fact_talent_snapshot WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_assessment_history WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_training_track WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999;
DELETE FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999;
DELETE FROM dim_organization WHERE org_id BETWEEN 900000 AND 900999;

-- ---------------------------------------------------------------- 维表
INSERT INTO dim_organization (org_id, code, name, level, path, parent_id) VALUES
  (900001, 'sqltest_hq', '测试总公司',     1, '/',            NULL),
  (900002, 'sqltest_rd', '测试研发部',     2, '/900001/',     900001),
  (900003, 'sqltest_hr', '测试人力资源部', 2, '/900001/',     900001);
INSERT INTO dim_talent_wide (talent_id, name, org_path, education, work_years, skill_tags, potential_level, match_avg_score) VALUES
  (900001, '测试张三', '/900001/900002/', '本科', 5.0, ARRAY['Python','SQL','Docker'],     'A', 88.50),
  (900002, '测试李四', '/900001/900002/', '硕士', 3.0, ARRAY['Python','机器学习','SQL'],   'B', 76.00),
  (900003, '测试王五', '/900001/900002/', '博士', 8.0, ARRAY['深度学习','Python','NLP'],   'A', 62.30),
  (900004, '测试赵六', '/900001/900003/', '本科', 2.0, ARRAY['Excel','沟通'],              'C', 55.00);

-- ---------------------------------------------------------------- 人才月度快照（分区键 snapshot_month）
INSERT INTO fact_talent_snapshot (id, snapshot_month, organization_id, education, age_group, talent_count, avg_work_years) VALUES
  (900001, '2026-09-01', 900002, '本科', '25-30', 10, 4.50),
  (900002, '2026-09-01', 900002, '硕士', '25-30',  5, 3.20),
  (900003, '2026-10-01', 900002, '本科', '25-30', 12, 4.80),
  (900004, '2026-10-01', 900002, '硕士', '25-30',  6, 3.50),
  (900005, '2026-10-01',      0, '',     '',      18, 4.30),
  (900006, '2026-10-01', 900003, '本科', '25-30',  4, 2.00);

-- ---------------------------------------------------------------- 测评历史（分区键 scored_at）
INSERT INTO fact_assessment_history (id, assessment_id, talent_id, position_id, total_score, level, dimension_scores, scored_at) VALUES
  (900001, 900001, 900001, 900001, 85.00, '高级', '{"skill":85,"logic":88}'::jsonb, '2026-10-05 10:00:00+08'),
  (900002, 900002, 900002, 900002, 72.50, '中级', '{"skill":72,"logic":75}'::jsonb, '2026-10-06 10:00:00+08'),
  (900003, 900003, 900001,   NULL, 90.00, '专家', '{"skill":90,"logic":92}'::jsonb, '2026-10-20 10:00:00+08');

-- ---------------------------------------------------------------- 培训轨迹（分区键 event_at）
INSERT INTO fact_training_track (id, talent_id, course_id, plan_id, progress, study_minutes, status, event_at) VALUES
  (900001, 900001, 900001, 900001, 100.00, 720, 'completed',   '2026-10-10 09:00:00+08'),
  (900002, 900001, 900002, 900001,  40.00, 192, 'learning',    '2026-10-12 09:00:00+08'),
  (900003, 900002, 900001, 900002, 100.00, 600, 'completed',   '2026-10-11 09:00:00+08'),
  (900004, 900003, 900003,   NULL,   0.00,   0, 'not_started', '2026-10-13 09:00:00+08');

-- ---------------------------------------------------------------- 行为事件（分区键 event_at）
INSERT INTO fact_behavior_event (id, talent_id, event_type, page, duration_ms, event_at) VALUES
  (900001, 900001, 'page_view', '/talents/900001',          5000,  '2026-10-15 08:00:00+08'),
  (900002, 900001, 'ai_call',   '/agents/tasks',            12000, '2026-10-15 08:05:00+08'),
  (900003, 900002, 'page_view', '/positions/900001',        3000,  '2026-10-15 09:00:00+08'),
  (900004, 900002, 'search',    '/talents?skill=Python',    2000,  '2026-10-15 09:10:00+08'),
  (900005, 900003, 'page_view', '/talents/900003',          4000,  '2026-10-16 08:00:00+08'),
  (900006, 900001, 'export',    '/analytics/export',        8000,  '2026-10-16 10:00:00+08');

-- ---------------------------------------------------------------- 种子自检
SELECT sqltest_check('DAT-001', '种子数据：dim_organization', $sql$SELECT (count(*)>0)::text FROM dim_organization WHERE org_id BETWEEN 900000 AND 900999$sql$, 'true');
SELECT sqltest_check('DAT-002', '种子数据：dim_talent_wide', $sql$SELECT (count(*)>0)::text FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999$sql$, 'true');
SELECT sqltest_check('DAT-003', '种子数据：fact_talent_snapshot', $sql$SELECT (count(*)>0)::text FROM fact_talent_snapshot WHERE id BETWEEN 900000 AND 900999$sql$, 'true');
SELECT sqltest_check('DAT-004', '种子数据：fact_assessment_history', $sql$SELECT (count(*)>0)::text FROM fact_assessment_history WHERE id BETWEEN 900000 AND 900999$sql$, 'true');
SELECT sqltest_check('DAT-005', '种子数据：fact_training_track', $sql$SELECT (count(*)>0)::text FROM fact_training_track WHERE id BETWEEN 900000 AND 900999$sql$, 'true');
SELECT sqltest_check('DAT-006', '种子数据：fact_behavior_event', $sql$SELECT (count(*)>0)::text FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999$sql$, 'true');
