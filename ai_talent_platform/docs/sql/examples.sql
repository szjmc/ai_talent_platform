-- question: 人才总数是多少？
SELECT count(*) AS talent_count FROM dim_talent_wide;

-- question: 各学历层次的人才分布？
SELECT education, count(*) AS talent_count FROM dim_talent_wide GROUP BY education ORDER BY talent_count DESC;

-- question: 各潜力等级的人才数量？
SELECT potential_level, count(*) AS talent_count FROM dim_talent_wide GROUP BY potential_level;

-- question: 各部门路径的人才数量？
SELECT org_path, count(*) AS talent_count FROM dim_talent_wide GROUP BY org_path;

-- question: 人才平均工作年限是多少？
SELECT avg(work_years) AS avg_work_years FROM dim_talent_wide;

-- question: 工作年限最多的十位人才？
SELECT name, work_years FROM dim_talent_wide ORDER BY work_years DESC NULLS LAST LIMIT 10;

-- question: 各学历人才的平均工作年限？
SELECT education, avg(work_years) AS avg_work_years FROM dim_talent_wide GROUP BY education;

-- question: 平均匹配分最高的十位人才？
SELECT name, match_avg_score FROM dim_talent_wide ORDER BY match_avg_score DESC NULLS LAST LIMIT 10;

-- question: 各潜力等级的平均匹配分？
SELECT potential_level, avg(match_avg_score) AS avg_match_score FROM dim_talent_wide GROUP BY potential_level;

-- question: 工作五年以上有多少人才？
SELECT count(*) AS talent_count FROM dim_talent_wide WHERE work_years >= 5;

-- question: 各月份各部门的人才总量？
SELECT snapshot_month, organization_id, talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' ORDER BY snapshot_month;

-- question: 最新月份各部门的人才总量？
SELECT organization_id, talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) ORDER BY talent_count DESC;

-- question: 每月人才总量趋势？
SELECT snapshot_month, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' GROUP BY snapshot_month ORDER BY snapshot_month;

-- question: 每月各部门平均工龄？
SELECT snapshot_month, organization_id, avg_work_years FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' ORDER BY snapshot_month;

-- question: 最近三个月部门人才总计？
SELECT organization_id, sum(talent_count) AS talent_months FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group = '' AND snapshot_month >= date_trunc('month', current_date) - interval '2 months' GROUP BY organization_id;

-- question: 最新月份学历分布？
SELECT education, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education <> '' AND age_group = '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) GROUP BY education;

-- question: 最新月份年龄段分布？
SELECT age_group, sum(talent_count) AS talent_count FROM fact_talent_snapshot WHERE organization_id > 0 AND education = '' AND age_group <> '' AND snapshot_month = (SELECT max(snapshot_month) FROM fact_talent_snapshot) GROUP BY age_group;

-- question: 上个月各部门的人才数量是多少？
SELECT o.name AS department, sum(f.talent_count) AS talent_count FROM fact_talent_snapshot f JOIN dim_organization o ON o.org_id = f.organization_id WHERE f.snapshot_month = date_trunc('month', current_date - interval '1 month') AND f.organization_id > 0 AND f.education = '' AND f.age_group = '' GROUP BY o.name ORDER BY talent_count DESC;

-- question: 测评平均分是多少？
SELECT avg(total_score) AS avg_score FROM fact_assessment_history;

-- question: 最近半年测评平均分趋势？
SELECT date_trunc('month', scored_at) AS month, avg(total_score) AS avg_score FROM fact_assessment_history WHERE scored_at >= current_date - interval '6 months' GROUP BY 1 ORDER BY 1;

-- question: 各能力等级测评次数？
SELECT level, count(*) AS assessment_count FROM fact_assessment_history GROUP BY level;

-- question: 各岗位的测评平均分？
SELECT position_id, avg(total_score) AS avg_score FROM fact_assessment_history GROUP BY position_id;

-- question: 测评最高分前十条？
SELECT talent_id, total_score FROM fact_assessment_history ORDER BY total_score DESC NULLS LAST LIMIT 10;

-- question: 每月测评次数趋势？
SELECT date_trunc('month', scored_at) AS month, count(*) AS assessment_count FROM fact_assessment_history GROUP BY 1 ORDER BY 1;

-- question: 测评分低于60的次数？
SELECT count(*) AS assessment_count FROM fact_assessment_history WHERE total_score < 60;

-- question: 参与测评的人才数？
SELECT count(DISTINCT talent_id) AS talent_count FROM fact_assessment_history;

-- question: 培训轨迹记录数？
SELECT count(*) AS track_count FROM fact_training_track;

-- question: 各课程累计记录的学习分钟数？
SELECT course_id, sum(study_minutes) AS recorded_minutes FROM fact_training_track GROUP BY course_id;

-- question: 各学习状态的轨迹条数？
SELECT status, count(*) AS track_count FROM fact_training_track GROUP BY status;

-- question: 每月培训轨迹次数？
SELECT date_trunc('month', event_at) AS month, count(*) AS track_count FROM fact_training_track GROUP BY 1 ORDER BY 1;

-- question: 参与学习的人才数？
SELECT count(DISTINCT talent_id) AS talent_count FROM fact_training_track;

-- question: 各培训计划的参与人才数？
SELECT plan_id, count(DISTINCT talent_id) AS talent_count FROM fact_training_track GROUP BY plan_id;

-- question: 各人才最新学习事件时间？
SELECT talent_id, max(event_at) AS last_event_at FROM fact_training_track GROUP BY talent_id;

-- question: 最近七天各行为类型次数？
SELECT event_type, count(*) AS event_count FROM fact_behavior_event WHERE event_at >= current_date - interval '7 days' GROUP BY event_type;

-- question: 每日行为事件趋势？
SELECT date_trunc('day', event_at) AS day, count(*) AS event_count FROM fact_behavior_event GROUP BY 1 ORDER BY 1;

-- question: 各页面访问事件数？
SELECT page, count(*) AS event_count FROM fact_behavior_event GROUP BY page ORDER BY event_count DESC;

-- question: 各事件类型的平均持续时间？
SELECT event_type, avg(duration_ms) AS avg_duration_ms FROM fact_behavior_event GROUP BY event_type;

-- question: 部门列表及层级？
SELECT org_id, name, level, path FROM dim_organization ORDER BY org_id;

-- question: 各层级的部门数量？
SELECT level, count(*) AS department_count FROM dim_organization GROUP BY level;

-- question: 顶层部门有哪些？
SELECT org_id, name FROM dim_organization WHERE parent_id IS NULL ORDER BY org_id;

-- question: 各项技能分别有多少人才，重复技能不要重复计人？
SELECT skill, count(*) AS talent_count FROM (SELECT DISTINCT talent_id, trim(unnest(skill_tags)) AS skill FROM dim_talent_wide) s WHERE skill IS NOT NULL AND skill <> '' GROUP BY skill ORDER BY talent_count DESC;

-- question: 本科且工作三年以上、具备Python技能的人才有多少？
SELECT count(*) AS talent_count FROM dim_talent_wide WHERE education = '本科' AND work_years >= 3 AND talent_id IN (SELECT talent_id FROM (SELECT talent_id, unnest(skill_tags) AS skill FROM dim_talent_wide) s WHERE trim(skill) = 'Python');

-- question: 各测评等级的有效记录数、平均分、最低分和最高分？
SELECT level, count(total_score) AS scored_count, avg(total_score) AS average_score, min(total_score) AS minimum_score, max(total_score) AS maximum_score FROM fact_assessment_history GROUP BY level;
