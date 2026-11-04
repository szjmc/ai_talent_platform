-- ============================================================================
-- 02_seed_test_data.sql — 测试数据（覆盖全部 25 张表）
-- 标签：[架构组] [P1]（数据底座：组织/用户/人才/岗位/简历/匹配/测评/培训/知识库/会话/审计）
--
-- 约定：全部测试数据使用固定 ID 段 900000-900999、编码前缀 sqltest_，
--       可重复执行（先清理再插入），99_cleanup.sql 负责最终清理。
-- 依赖：先执行 01_schema_assertions.sql（创建 sqltest_results 与清理过程）。
-- ============================================================================
SET NAMES utf8mb4;
CALL sqltest_guard();
CALL sqltest_cleanup_data();
DELETE FROM sqltest_results WHERE test_id LIKE 'DAT-%';

-- ---------------------------------------------------------------- 组织 / 角色 / 用户
INSERT INTO organizations (id, parent_id, code, name, level, path, sort) VALUES
  (900001, NULL,    'sqltest_hq', '测试总公司',   1, '/',         1),
  (900002, 900001, 'sqltest_rd', '测试研发部',   2, '/900001/',  1),
  (900003, 900001, 'sqltest_hr', '测试人力资源部', 2, '/900001/', 2);
INSERT INTO roles (id, code, name) VALUES
  (900001, 'sqltest_admin', '测试管理员'),
  (900002, 'sqltest_hr',    '测试HR'),
  (900003, 'sqltest_emp',   '测试员工');
INSERT INTO users (id, username, password_hash, real_name, mobile, email, organization_id, is_superuser) VALUES
  (900001, 'sqltest_admin', '$2b$12$01234567890123456789012345678901234567890123456789012', '测试管理员', '19900000001', 'admin@sqltest.local', 900001, 1),
  (900002, 'sqltest_hr01',  '$2b$12$01234567890123456789012345678901234567890123456789012', '测试HR',     '19900000002', 'hr01@sqltest.local',  900003, 0),
  (900003, 'sqltest_emp01', '$2b$12$01234567890123456789012345678901234567890123456789012', '测试员工',   '19900000003', 'emp01@sqltest.local', 900002, 0);
INSERT INTO user_roles (user_id, role_id) VALUES (900001, 900001), (900002, 900002), (900003, 900003);
UPDATE organizations SET leader_id = 900002 WHERE id = 900003;

-- ---------------------------------------------------------------- 人才 / 画像
INSERT INTO talents (id, name, gender, birth_date, mobile, email, education, school, major, work_years,
                     current_company, current_position, organization_id, skill_tags, status, potential_level, source) VALUES
  (900001, '测试张三', 'male',   '1995-03-15', '19900001001', 'zhangsan@sqltest.local', '本科', '测试大学', '计算机科学', 5,
   '测试科技公司', '后端工程师', 900002, JSON_ARRAY('Python', 'SQL', 'Docker'), 'active', 'A', 'manual'),
  (900002, '测试李四', 'female', '1998-07-20', '19900001002', 'lisi@sqltest.local',     '硕士', '测试大学', '软件工程',   3,
   '测试互联网公司', '算法工程师', 900002, JSON_ARRAY('Python', '机器学习', 'SQL'), 'active', 'B', 'ai_resume'),
  (900003, '测试王五', 'male',   '1990-11-02', '19900001003', 'wangwu@sqltest.local',   '博士', '测试研究院', '人工智能',   8,
   '测试研究院', '研究员', 900002, JSON_ARRAY('深度学习', 'Python', 'NLP'), 'reserve', 'A', 'manual');
INSERT INTO talent_portraits (id, talent_id, version, dimensions, labels, potential_level, model_name, generated_at) VALUES
  (900001, 900001, 1, JSON_OBJECT('skill', 85, 'experience', 80, 'quality', 90, 'potential', 88), JSON_ARRAY('后端骨干', '学习能力强'), 'A', 'sqltest-model', '2026-10-01 10:00:00'),
  (900002, 900002, 1, JSON_OBJECT('skill', 78, 'experience', 70, 'quality', 82, 'potential', 80), JSON_ARRAY('算法潜力'), 'B', 'sqltest-model', '2026-10-01 10:05:00');

-- ---------------------------------------------------------------- 岗位 / 能力模型
INSERT INTO positions (id, title, code, organization_id, level, headcount, status, skill_tags, salary_min, salary_max, publish_at) VALUES
  (900001, '测试高级后端工程师', 'sqltest_p001', 900002, 'senior', 2, 'open',   JSON_ARRAY('Python', 'SQL', 'Docker'), 25000.00, 40000.00, '2026-10-01 09:00:00'),
  (900002, '测试算法工程师',     'sqltest_p002', 900002, 'middle', 1, 'paused', JSON_ARRAY('机器学习', 'Python'),       20000.00, 35000.00, '2026-10-02 09:00:00');
INSERT INTO position_requirements (id, position_id, version, dimensions, weights, source) VALUES
  (900001, 900001, 1, JSON_OBJECT('skill', 'Python/SQL', 'experience', '3年以上'), JSON_OBJECT('skill', 0.4, 'experience', 0.3, 'education', 0.2, 'quality', 0.1), 'manual'),
  (900002, 900001, 2, JSON_OBJECT('skill', 'Python/SQL/K8s'), JSON_OBJECT('skill', 0.5, 'experience', 0.3, 'quality', 0.2), 'ai_parse');

-- ---------------------------------------------------------------- 文件 / 简历
INSERT INTO files (id, file_name, stored_name, file_path, file_size, content_type, extension, file_hash, biz_type, uploader_id) VALUES
  (900001, '测试简历_张三.pdf', 'sqltest_r1.pdf', 'resume/2026/10/sqltest_r1.pdf', 204800, 'application/pdf', '.pdf', REPEAT('a', 64), 'resume',    900002),
  (900002, '测试制度手册.docx', 'sqltest_k1.docx', 'knowledge/2026/10/sqltest_k1.docx', 102400, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx', REPEAT('b', 64), 'knowledge', 900002);
INSERT INTO resumes (id, talent_id, file_id, file_name, file_type, file_size, file_hash, status, accuracy, parsed_data, parsed_at) VALUES
  (900001, 900001, 900001, '测试简历_张三.pdf', 'pdf', 204800, REPEAT('a', 64), 'success', 0.950,
   JSON_OBJECT('basic_info', JSON_OBJECT('name', '测试张三', 'mobile', '19900001001'), 'skills', JSON_ARRAY('Python', 'SQL', 'Docker')), '2026-10-01 11:00:00'),
  (900002, NULL, NULL, '测试简历_待解析.pdf', 'pdf', 102400, REPEAT('c', 64), 'uploaded', NULL, NULL, NULL);
INSERT INTO resume_experiences (id, resume_id, company, title, start_date, end_date, description, sort) VALUES
  (900001, 900001, '测试科技公司', '后端工程师', '2021-07-01', NULL, '负责测试平台后端开发', 1),
  (900002, 900001, '测试软件公司', '初级工程师', '2019-07-01', '2021-06-30', '参与测试系统维护', 2);

-- ---------------------------------------------------------------- 人岗匹配
INSERT INTO matching_records (id, batch_id, position_id, talent_id, score, dimension_scores, highlights, gaps, strategy, matched_at) VALUES
  (900001, 'sqltest_batch_01', 900001, 900001, 88.50, JSON_OBJECT('skill', 90, 'experience', 85, 'education', 80, 'quality', 92), JSON_ARRAY('技能高度匹配'), JSON_ARRAY('缺少K8s经验'), 'hybrid', '2026-10-03 10:00:00'),
  (900002, 'sqltest_batch_01', 900001, 900002, 76.00, JSON_OBJECT('skill', 75, 'experience', 70, 'education', 90, 'quality', 80), JSON_ARRAY('学历优势'), JSON_ARRAY('后端经验不足'), 'hybrid', '2026-10-03 10:00:00'),
  (900003, 'sqltest_batch_01', 900001, 900003, 62.30, JSON_OBJECT('skill', 60, 'experience', 65, 'education', 95, 'quality', 70), JSON_ARRAY('博士学历'), JSON_ARRAY('工程经验偏少'), 'hybrid', '2026-10-03 10:00:00'),
  (900004, 'sqltest_batch_02', 900002, 900002, 91.20, JSON_OBJECT('skill', 95, 'experience', 85, 'education', 90, 'quality', 88), JSON_ARRAY('算法方向契合'), JSON_ARRAY(), 'soft', '2026-10-04 10:00:00');

-- ---------------------------------------------------------------- 测评
INSERT INTO assessments (id, talent_id, position_id, title, status, question_count, total_score, created_by_ai) VALUES
  (900001, 900001, 900001, '测试后端能力测评', 'scored', 2, 85.00, 1),
  (900002, 900002, 900002, '测试算法能力测评', 'pending', 0, NULL, 0);
INSERT INTO assessment_questions (id, assessment_id, type, content, options, answer, score, dimension, sort) VALUES
  (900001, 900001, 'single', 'SQL 中用于分组的关键字是？', JSON_ARRAY(JSON_OBJECT('key','A','text','GROUP BY'), JSON_OBJECT('key','B','text','ORDER BY')), JSON_QUOTE('A'), 40.00, 'skill', 1),
  (900002, 900001, 'text',   '简述索引失效的常见场景。', NULL, NULL, 60.00, 'skill', 2);
INSERT INTO assessment_answers (id, assessment_id, question_id, answer, score, scored_by, scored_at) VALUES
  (900001, 900001, 900001, JSON_QUOTE('A'), 40.00, 'rule', '2026-10-05 10:00:00'),
  (900002, 900001, 900002, JSON_OBJECT('text', '对索引列使用函数会导致失效'), 45.00, 'ai', '2026-10-05 10:01:00');
INSERT INTO assessment_results (id, assessment_id, total_score, dimension_scores, level, model_name) VALUES
  (900001, 900001, 85.00, JSON_OBJECT('skill', 85, 'logic', 88), '高级', 'sqltest-model');

-- ---------------------------------------------------------------- 培训
INSERT INTO courses (id, title, category, level, duration_hours, skill_tags, is_active) VALUES
  (900001, '测试Python进阶', '技术', 'middle', 12.00, JSON_ARRAY('Python'), 1),
  (900002, '测试SQL优化',   '技术', 'senior',  8.00, JSON_ARRAY('SQL'),    1),
  (900003, '测试沟通技巧',   '通用', 'junior',  4.00, JSON_ARRAY('沟通'),   0);
INSERT INTO training_plans (id, name, talent_id, goal, start_date, end_date, status, created_by_ai, source_assessment_id) VALUES
  (900001, '测试张三提升计划', 900001, '补齐K8s与SQL优化能力', '2026-10-10', '2026-12-31', 'ongoing', 1, 900001),
  (900002, '测试李四算法计划', 900002, '强化工程落地能力',     '2026-10-15', '2027-01-31', 'draft',   0, NULL);
INSERT INTO plan_courses (plan_id, course_id, sort, require_pass) VALUES
  (900001, 900001, 1, 1), (900001, 900002, 2, 1), (900002, 900001, 1, 0);
INSERT INTO learning_records (id, plan_id, course_id, talent_id, progress, study_minutes, status, score, passed) VALUES
  (900001, 900001, 900001, 900001, 100, 720, 'completed', 92.00, 1),
  (900002, 900001, 900002, 900001, 40,  192, 'learning',  NULL,   NULL),
  (900003, 900002, 900001, 900002, 0,   0,   'not_started', NULL, NULL);

-- ---------------------------------------------------------------- 知识库 / 会话 / 审计
INSERT INTO knowledge_docs (id, title, category, source_type, file_id, status, chunk_count, permission_scope, allowed_roles, organization_id, embedding_model, indexed_at) VALUES
  (900001, '测试员工手册', '制度', 'file', 900002, 'indexed', 2, 'role', JSON_ARRAY('sqltest_hr', 'sqltest_emp'), NULL, 'text-embedding-v3', '2026-10-06 10:00:00'),
  (900002, '测试技术FAQ',  '技术', 'text', NULL,   'indexing', 0, 'public', NULL, NULL, NULL, NULL);
INSERT INTO knowledge_chunks (id, doc_id, chunk_index, content, token_count, milvus_pk, page_no) VALUES
  (900001, 900001, 0, '测试员工手册第一章：考勤制度。', 18, 900001, 1),
  (900002, 900001, 1, '测试员工手册第二章：报销流程。', 16, 900002, 2);
INSERT INTO conversations (id, user_id, title, agent_type, status, message_count, last_message_at) VALUES
  (900001, 900002, '测试知识问答', 'assistant', 'active', 2, '2026-10-07 10:01:00'),
  (900002, 900003, '测试简历助手', 'resume',    'active', 0, NULL);
INSERT INTO chat_messages (id, conversation_id, role, content, citations, prompt_tokens, completion_tokens, model_name) VALUES
  (900001, 900001, 'user',      '公司的报销流程是什么？', NULL, 0, 0, NULL),
  (900002, 900001, 'assistant', '根据员工手册，报销流程如下……', JSON_ARRAY(JSON_OBJECT('doc_id', 900001, 'chunk', 1, 'page', 2, 'score', 0.92)), 120, 80, 'sqltest-model');
INSERT INTO audit_logs (id, user_id, username, action, module, target_type, target_id, request_method, request_path, trace_id, status) VALUES
  (900001, 900002, 'sqltest_hr01', 'create', 'talent', 'talent', 900001, 'POST', '/api/v1/talents', 'sqltest-trace-001', 'success'),
  (900002, 900002, 'sqltest_hr01', 'ai_call', 'agent', 'agent_task', NULL, 'POST', '/api/v1/agents/tasks', 'sqltest-trace-002', 'success');

-- ---------------------------------------------------------------- 种子数据自检（25 张表都有数据）
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-001', '种子数据：organizations', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM organizations WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-002', '种子数据：roles', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM roles WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-003', '种子数据：users', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM users WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-004', '种子数据：user_roles', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM user_roles WHERE user_id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-005', '种子数据：talents', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM talents WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-006', '种子数据：talent_portraits', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM talent_portraits WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-007', '种子数据：positions', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM positions WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-008', '种子数据：position_requirements', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM position_requirements WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-009', '种子数据：files', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM files WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-010', '种子数据：resumes', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM resumes WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-011', '种子数据：resume_experiences', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM resume_experiences WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-012', '种子数据：matching_records', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM matching_records WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-013', '种子数据：assessments', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM assessments WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-014', '种子数据：assessment_questions', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM assessment_questions WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-015', '种子数据：assessment_answers', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM assessment_answers WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-016', '种子数据：assessment_results', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM assessment_results WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-017', '种子数据：courses', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM courses WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-018', '种子数据：training_plans', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM training_plans WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-019', '种子数据：plan_courses', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM plan_courses WHERE plan_id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-020', '种子数据：learning_records', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM learning_records WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-021', '种子数据：knowledge_docs', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM knowledge_docs WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-022', '种子数据：knowledge_chunks', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM knowledge_chunks WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-023', '种子数据：conversations', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM conversations WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-024', '种子数据：chat_messages', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM chat_messages WHERE id BETWEEN 900000 AND 900999;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'DAT-025', '种子数据：audit_logs', COUNT(*) > 0, CONCAT('测试段行数=', COUNT(*)) FROM audit_logs WHERE id BETWEEN 900000 AND 900999;
