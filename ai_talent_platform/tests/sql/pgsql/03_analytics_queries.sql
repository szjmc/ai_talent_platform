-- ============================================================================
-- 03_analytics_queries.sql — 分析查询验收（NL2SQL 高频问题 / 聚合 / 窗口 / JSONB / 数组）
-- 标签：[Vanna组] [P1]（问数链路 SQL 正确性：这些就是 Vanna 训练语料对应的标准查询）
-- 依赖：01（断言函数）、02（种子数据）。全部限定 900000-900999 测试段。
-- ============================================================================

DO $$
BEGIN
  IF current_database() NOT LIKE '%test%'
     AND coalesce(current_setting('sqltest.allow_non_test_db', true), '') <> 'on' THEN
    RAISE EXCEPTION 'SQL 测试只允许在名称含 test 的数据库执行（如 ai_talent_analytics_test）；确需其他库请先 SET sqltest.allow_non_test_db = on';
  END IF;
END $$;
DELETE FROM sqltest_results WHERE test_id LIKE 'ANA-%';

-- ---------------------------------------------------------------- 人才快照 / 趋势
SELECT sqltest_check('ANA-001', '最新月份全部门人才总数', $sql$SELECT talent_count::text FROM fact_talent_snapshot WHERE organization_id=0 AND snapshot_month='2026-10-01' AND education='' AND age_group=''$sql$, '18');
SELECT sqltest_check('ANA-002', '研发部10月人才数（本科+硕士）', $sql$SELECT sum(talent_count)::text FROM fact_talent_snapshot WHERE organization_id=900002 AND snapshot_month='2026-10-01'$sql$, '18');
SELECT sqltest_check('ANA-003', '研发部环比增长率（9月15→10月18）', $sql$SELECT round(100.0*(18-15)/15, 2)::text$sql$, '20.00');
SELECT sqltest_check('ANA-004', '研发部10月本科占比', $sql$SELECT round(100.0*sum(talent_count) FILTER (WHERE education='本科')/sum(talent_count), 2)::text FROM fact_talent_snapshot WHERE organization_id=900002 AND snapshot_month='2026-10-01'$sql$, '66.67');
SELECT sqltest_check('ANA-005', '按月趋势（date_trunc）', $sql$SELECT count(DISTINCT date_trunc('month', snapshot_month))::text FROM fact_talent_snapshot WHERE organization_id=900002$sql$, '2');

-- ---------------------------------------------------------------- 人才宽表 / 数组 / JSONB
SELECT sqltest_check('ANA-010', '技能含 Python 的人才数（数组包含）', $sql$SELECT count(*)::text FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999 AND skill_tags @> ARRAY['Python']$sql$, '3');
SELECT sqltest_check('ANA-011', '高潜(A)且匹配分>=80 的人才数', $sql$SELECT count(*)::text FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999 AND potential_level='A' AND match_avg_score>=80$sql$, '1');
SELECT sqltest_check('ANA-012', '人才平均匹配分', $sql$SELECT round(avg(match_avg_score), 2)::text FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999$sql$, '70.45');
SELECT sqltest_check('ANA-013', '学历分布（本科人数）', $sql$SELECT count(*)::text FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999 AND education='本科'$sql$, '2');
SELECT sqltest_check('ANA-014', '研发部人才宽表数（org_path 前缀）', $sql$SELECT count(*)::text FROM dim_talent_wide WHERE org_path LIKE '/900001/900002/%'$sql$, '3');
SELECT sqltest_check('ANA-015', '技能标签展开统计（unnest）', $sql$SELECT count(*)::text FROM dim_talent_wide t, unnest(t.skill_tags) AS skill WHERE t.talent_id BETWEEN 900000 AND 900999 AND skill='SQL'$sql$, '2');

-- ---------------------------------------------------------------- 测评历史 / 窗口函数
SELECT sqltest_check('ANA-020', '张三最新测评分（窗口取最新）', $sql$SELECT total_score::text FROM (SELECT total_score, row_number() OVER (PARTITION BY talent_id ORDER BY scored_at DESC) rn FROM fact_assessment_history WHERE talent_id=900001) x WHERE rn=1$sql$, '90.00');
SELECT sqltest_check('ANA-021', '张三测评进步幅度（90-85）', $sql$SELECT round(max(total_score)-min(total_score), 2)::text FROM fact_assessment_history WHERE talent_id=900001$sql$, '5.00');
SELECT sqltest_check('ANA-022', '全部测评平均分', $sql$SELECT round(avg(total_score), 2)::text FROM fact_assessment_history WHERE id BETWEEN 900000 AND 900999$sql$, '82.50');
SELECT sqltest_check('ANA-023', 'JSONB 维度分提取（skill）', $sql$SELECT (dimension_scores->>'skill')::text FROM fact_assessment_history WHERE id=900001$sql$, '85');
SELECT sqltest_check('ANA-024', '测评等级分布（高级及以上）', $sql$SELECT count(*)::text FROM fact_assessment_history WHERE id BETWEEN 900000 AND 900999 AND level IN ('高级','专家')$sql$, '2');

-- ---------------------------------------------------------------- 培训 / 行为
SELECT sqltest_check('ANA-030', '培训完成率', $sql$SELECT round(100.0*count(*) FILTER (WHERE status='completed')/count(*), 2)::text FROM fact_training_track WHERE id BETWEEN 900000 AND 900999$sql$, '50.00');
SELECT sqltest_check('ANA-031', '张三累计学习分钟', $sql$SELECT sum(study_minutes)::text FROM fact_training_track WHERE talent_id=900001$sql$, '912');
SELECT sqltest_check('ANA-032', '行为事件 PV', $sql$SELECT count(*)::text FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999$sql$, '6');
SELECT sqltest_check('ANA-033', '行为事件 UV（去重人才）', $sql$SELECT count(DISTINCT talent_id)::text FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999$sql$, '3');
SELECT sqltest_check('ANA-034', 'page_view 平均停留毫秒', $sql$SELECT round(avg(duration_ms))::text FROM fact_behavior_event WHERE event_type='page_view' AND id BETWEEN 900000 AND 900999$sql$, '4000');
SELECT sqltest_check('ANA-035', '事件类型 TopN（最高频）', $sql$SELECT event_type FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999 GROUP BY event_type ORDER BY count(*) DESC, event_type LIMIT 1$sql$, 'page_view');

-- ---------------------------------------------------------------- ETL 幂等 upsert（ON CONFLICT）
INSERT INTO fact_talent_snapshot (snapshot_month, organization_id, education, age_group, talent_count, avg_work_years)
VALUES ('2026-10-01', 900002, '本科', '25-30', 99, 4.80)
ON CONFLICT (snapshot_month, organization_id, education, age_group)
DO UPDATE SET talent_count = EXCLUDED.talent_count, avg_work_years = EXCLUDED.avg_work_years, updated_at = now();
SELECT sqltest_check('ANA-040', 'upsert 后快照人才数被更新为 99', $sql$SELECT talent_count::text FROM fact_talent_snapshot WHERE snapshot_month='2026-10-01' AND organization_id=900002 AND education='本科' AND age_group='25-30'$sql$, '99');
SELECT sqltest_check('ANA-041', 'upsert 未产生重复维度行', $sql$SELECT count(*)::text FROM fact_talent_snapshot WHERE snapshot_month='2026-10-01' AND organization_id=900002 AND education='本科' AND age_group='25-30'$sql$, '1');

-- ---------------------------------------------------------------- 分区路由
SELECT sqltest_check('ANA-050', '默认分区兜底路由（行为事件）', $sql$SELECT c.relname FROM pg_class c WHERE c.oid=(SELECT tableoid FROM fact_behavior_event WHERE id=900001)$sql$, 'fact_behavior_event_default');
SELECT sqltest_check('ANA-051', '默认分区兜底路由（人才快照）', $sql$SELECT c.relname FROM pg_class c WHERE c.oid=(SELECT tableoid FROM fact_talent_snapshot WHERE id=900001)$sql$, 'fact_talent_snapshot_default');
