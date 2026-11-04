-- ============================================================================
-- 01_schema_assertions.sql — PgSQL 分析库结构断言（6 张表 + 4 个默认分区）
-- 标签：[Vanna组] [P1]（结构验收：表/字段/约束/索引/Identity/分区）
--
-- 运行前提：已在测试库执行 schema/pgsql_schema.sql（或 scripts/init_pgsql.py）。
-- 结果写入 sqltest_results 表，99_cleanup.sql 输出汇总并清理。
-- ============================================================================

DO $$
BEGIN
  IF current_database() NOT LIKE '%test%'
     AND coalesce(current_setting('sqltest.allow_non_test_db', true), '') <> 'on' THEN
    RAISE EXCEPTION 'SQL 测试只允许在名称含 test 的数据库执行（如 ai_talent_analytics_test）；确需其他库请先 SET sqltest.allow_non_test_db = on';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS sqltest_results (
  test_id   text NOT NULL,
  test_name text NOT NULL,
  passed    boolean NOT NULL,
  detail    text,
  ran_at    timestamptz NOT NULL DEFAULT now()
);
DELETE FROM sqltest_results WHERE test_id LIKE 'SCH-%';

-- 通用断言函数：执行返回单值的 SQL，与期望文本比对
CREATE OR REPLACE FUNCTION sqltest_check(p_id text, p_name text, p_sql text, p_expected text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  EXECUTE p_sql INTO v;
  INSERT INTO sqltest_results (test_id, test_name, passed, detail)
  VALUES (p_id, p_name, v IS NOT DISTINCT FROM p_expected, '期望=' || p_expected || ' 实际=' || coalesce(v, '<NULL>'));
END $$;

-- 负向断言函数：执行必须失败的 SQL，比对 SQLSTATE
CREATE OR REPLACE FUNCTION sqltest_expect_error(p_id text, p_name text, p_sql text, p_state text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_state text := '00000'; v_msg text := '未抛出错误';
BEGIN
  BEGIN
    EXECUTE p_sql;
  EXCEPTION WHEN OTHERS THEN
    v_state := SQLSTATE; v_msg := SQLERRM;
  END;
  INSERT INTO sqltest_results (test_id, test_name, passed, detail)
  VALUES (p_id, p_name, v_state = p_state, '期望SQLSTATE=' || p_state || ' 实际=' || v_state || ' ' || left(v_msg, 160));
END $$;

-- ---------------------------------------------------------------- 表 / 分区齐全性
SELECT sqltest_check('SCH-001', '6 张分析表全部存在',
  $sql$SELECT count(*)::text FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('fact_talent_snapshot','fact_assessment_history','fact_training_track','fact_behavior_event','dim_organization','dim_talent_wide')$sql$, '6');
SELECT sqltest_check('SCH-002', '4 个默认分区全部存在',
  $sql$SELECT count(*)::text FROM pg_tables WHERE schemaname='public' AND tablename IN ('fact_talent_snapshot_default','fact_assessment_history_default','fact_training_track_default','fact_behavior_event_default')$sql$, '4');
SELECT sqltest_check('SCH-003', 'fact_talent_snapshot 为 RANGE 分区表（分区键 snapshot_month）',
  $sql$SELECT (c.relkind='p' AND pg_get_partkeydef(c.oid) ILIKE '%snapshot_month%')::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='fact_talent_snapshot'$sql$, 'true');
SELECT sqltest_check('SCH-004', 'fact_assessment_history 为 RANGE 分区表（分区键 scored_at）',
  $sql$SELECT (c.relkind='p' AND pg_get_partkeydef(c.oid) ILIKE '%scored_at%')::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='fact_assessment_history'$sql$, 'true');
SELECT sqltest_check('SCH-005', 'fact_training_track 为 RANGE 分区表（分区键 event_at）',
  $sql$SELECT (c.relkind='p' AND pg_get_partkeydef(c.oid) ILIKE '%event_at%')::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='fact_training_track'$sql$, 'true');
SELECT sqltest_check('SCH-006', 'fact_behavior_event 为 RANGE 分区表（分区键 event_at）',
  $sql$SELECT (c.relkind='p' AND pg_get_partkeydef(c.oid) ILIKE '%event_at%')::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='fact_behavior_event'$sql$, 'true');

-- ---------------------------------------------------------------- 字段齐全性（逐表）
SELECT sqltest_check('SCH-010', 'dim_organization 字段齐全（7 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='dim_organization'$sql$, 'code,level,name,org_id,parent_id,path,synced_at');
SELECT sqltest_check('SCH-011', 'dim_talent_wide 字段齐全（9 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='dim_talent_wide'$sql$, 'education,match_avg_score,name,org_path,potential_level,skill_tags,synced_at,talent_id,work_years');
SELECT sqltest_check('SCH-012', 'fact_assessment_history 字段齐全（9 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_assessment_history'$sql$, 'assessment_id,created_at,dimension_scores,id,level,position_id,scored_at,talent_id,total_score');
SELECT sqltest_check('SCH-013', 'fact_behavior_event 字段齐全（7 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_behavior_event'$sql$, 'created_at,duration_ms,event_at,event_type,id,page,talent_id');
SELECT sqltest_check('SCH-014', 'fact_talent_snapshot 字段齐全（9 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_talent_snapshot'$sql$, 'age_group,avg_work_years,created_at,education,id,organization_id,snapshot_month,talent_count,updated_at');
SELECT sqltest_check('SCH-015', 'fact_training_track 字段齐全（9 个）',
  $sql$SELECT coalesce(string_agg(column_name, ',' ORDER BY column_name),'') FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_training_track'$sql$, 'course_id,created_at,event_at,id,plan_id,progress,status,study_minutes,talent_id');

-- ---------------------------------------------------------------- 主键 / 唯一 / CHECK 约束
SELECT sqltest_check('SCH-030', 'dim_organization CHECK 约束 ck_dim_organization_level_positive',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_organization'::regclass AND conname='ck_dim_organization_level_positive' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-031', 'dim_organization 主键 (org_id)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_organization'::regclass AND conname='pk_dim_organization' AND contype='p'$sql$, '1');
SELECT sqltest_check('SCH-032', 'dim_organization 唯一约束 uq_dim_organization_code',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_organization'::regclass AND conname='uq_dim_organization_code' AND contype='u'$sql$, '1');
SELECT sqltest_check('SCH-033', 'dim_talent_wide CHECK 约束 ck_dim_talent_wide_match_score_range',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_talent_wide'::regclass AND conname='ck_dim_talent_wide_match_score_range' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-034', 'dim_talent_wide CHECK 约束 ck_dim_talent_wide_work_years_non_negative',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_talent_wide'::regclass AND conname='ck_dim_talent_wide_work_years_non_negative' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-035', 'dim_talent_wide 主键 (talent_id)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.dim_talent_wide'::regclass AND conname='pk_dim_talent_wide' AND contype='p'$sql$, '1');
SELECT sqltest_check('SCH-036', 'fact_assessment_history CHECK 约束 ck_fact_assessment_history_score_range',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_assessment_history'::regclass AND conname='ck_fact_assessment_history_score_range' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-037', 'fact_assessment_history 主键 (id,scored_at)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_assessment_history'::regclass AND conname='pk_fact_assessment_history' AND contype='p'$sql$, '1');
SELECT sqltest_check('SCH-038', 'fact_behavior_event CHECK 约束 ck_fact_behavior_event_duration_non_negative',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_behavior_event'::regclass AND conname='ck_fact_behavior_event_duration_non_negative' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-039', 'fact_behavior_event 主键 (id,event_at)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_behavior_event'::regclass AND conname='pk_fact_behavior_event' AND contype='p'$sql$, '1');
SELECT sqltest_check('SCH-040', 'fact_talent_snapshot CHECK 约束 ck_fact_talent_snapshot_count_non_negative',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_talent_snapshot'::regclass AND conname='ck_fact_talent_snapshot_count_non_negative' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-041', 'fact_talent_snapshot CHECK 约束 ck_fact_talent_snapshot_work_years_non_negative',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_talent_snapshot'::regclass AND conname='ck_fact_talent_snapshot_work_years_non_negative' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-042', 'fact_talent_snapshot 主键 (id,snapshot_month)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_talent_snapshot'::regclass AND conname='pk_fact_talent_snapshot' AND contype='p'$sql$, '1');
SELECT sqltest_check('SCH-043', 'fact_talent_snapshot 唯一约束 uq_fact_talent_snapshot_dimensions',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_talent_snapshot'::regclass AND conname='uq_fact_talent_snapshot_dimensions' AND contype='u'$sql$, '1');
SELECT sqltest_check('SCH-044', 'fact_training_track CHECK 约束 ck_fact_training_track_minutes_non_negative',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_training_track'::regclass AND conname='ck_fact_training_track_minutes_non_negative' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-045', 'fact_training_track CHECK 约束 ck_fact_training_track_progress_range',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_training_track'::regclass AND conname='ck_fact_training_track_progress_range' AND contype='c'$sql$, '1');
SELECT sqltest_check('SCH-046', 'fact_training_track 主键 (id,event_at)',
  $sql$SELECT count(*)::text FROM pg_constraint WHERE conrelid='public.fact_training_track'::regclass AND conname='pk_fact_training_track' AND contype='p'$sql$, '1');

-- ---------------------------------------------------------------- 索引
SELECT sqltest_check('SCH-060', '索引 ix_dim_organization_parent',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='dim_organization' AND indexname='ix_dim_organization_parent'$sql$, '1');
SELECT sqltest_check('SCH-061', '索引 ix_dim_talent_wide_education',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='dim_talent_wide' AND indexname='ix_dim_talent_wide_education'$sql$, '1');
SELECT sqltest_check('SCH-062', '索引 ix_dim_talent_wide_potential',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='dim_talent_wide' AND indexname='ix_dim_talent_wide_potential'$sql$, '1');
SELECT sqltest_check('SCH-063', '索引 ix_fact_assessment_history_assessment',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_assessment_history' AND indexname='ix_fact_assessment_history_assessment'$sql$, '1');
SELECT sqltest_check('SCH-064', '索引 ix_fact_assessment_history_talent_time',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_assessment_history' AND indexname='ix_fact_assessment_history_talent_time'$sql$, '1');
SELECT sqltest_check('SCH-065', '索引 ix_fact_behavior_event_talent_time',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_behavior_event' AND indexname='ix_fact_behavior_event_talent_time'$sql$, '1');
SELECT sqltest_check('SCH-066', '索引 ix_fact_behavior_event_type_time',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_behavior_event' AND indexname='ix_fact_behavior_event_type_time'$sql$, '1');
SELECT sqltest_check('SCH-067', '索引 ix_fact_talent_snapshot_org_month',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_talent_snapshot' AND indexname='ix_fact_talent_snapshot_org_month'$sql$, '1');
SELECT sqltest_check('SCH-068', '索引 ix_fact_training_track_course',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_training_track' AND indexname='ix_fact_training_track_course'$sql$, '1');
SELECT sqltest_check('SCH-069', '索引 ix_fact_training_track_talent_time',
  $sql$SELECT count(*)::text FROM pg_indexes WHERE schemaname='public' AND tablename='fact_training_track' AND indexname='ix_fact_training_track_talent_time'$sql$, '1');

-- ---------------------------------------------------------------- Identity / 特殊类型
SELECT sqltest_check('SCH-080', 'fact 表 id 均为 Identity 自增',
  $sql$SELECT count(*)::text FROM information_schema.columns WHERE table_schema='public' AND column_name='id' AND is_identity='YES' AND table_name IN ('fact_talent_snapshot','fact_assessment_history','fact_training_track','fact_behavior_event')$sql$, '4');
SELECT sqltest_check('SCH-081', 'dimension_scores 为 JSONB 类型',
  $sql$SELECT count(*)::text FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_assessment_history' AND column_name='dimension_scores' AND data_type='jsonb'$sql$, '1');
SELECT sqltest_check('SCH-082', 'skill_tags 为 text[] 数组类型',
  $sql$SELECT data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='dim_talent_wide' AND column_name='skill_tags'$sql$, 'ARRAY');
SELECT sqltest_check('SCH-083', '时间字段为 timestamptz',
  $sql$SELECT count(*)::text FROM information_schema.columns WHERE table_schema='public' AND table_name='fact_behavior_event' AND column_name IN ('event_at','created_at') AND data_type='timestamp with time zone'$sql$, '2');
