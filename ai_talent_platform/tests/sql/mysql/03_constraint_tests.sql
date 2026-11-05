-- ============================================================================
-- 03_constraint_tests.sql — 约束行为测试（唯一键 / 外键 / 级联 / SET NULL / 默认值）
-- 标签：[架构组] [P0]（安全与一致性红线：越权数据、脏数据必须被数据库拒绝）
--
-- 原理：sqltest_expect_error 执行一条「必须失败」的 SQL，
--       捕获 MYSQL_ERRNO 并与期望错误码比对（1062 唯一冲突 / 1452 外键拒绝 /
--       1451 外键限制 / 1048 非空 / 1364 缺省缺失 / 1264 越界 / 3140 JSON 非法）。
-- 依赖：01（结果表/护栏）、02（种子数据）。
-- ============================================================================
SET NAMES utf8mb4;
CALL sqltest_guard();
DELETE FROM sqltest_results WHERE test_id LIKE 'CON-%';

DROP PROCEDURE IF EXISTS sqltest_expect_error;
DELIMITER $$
CREATE PROCEDURE sqltest_expect_error(IN p_id VARCHAR(16), IN p_name VARCHAR(255), IN p_sql TEXT, IN p_errno INT)
BEGIN
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(512) DEFAULT '';
  DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
  END;
  SET @sqltest_stmt = p_sql;
  PREPARE sqltest_ps FROM @sqltest_stmt;
  EXECUTE sqltest_ps;
  DEALLOCATE PREPARE sqltest_ps;
  INSERT INTO sqltest_results (test_id, test_name, passed, detail)
  VALUES (p_id, p_name, v_errno = p_errno, CONCAT('期望错误=', p_errno, ' 实际错误=', v_errno, ' ', LEFT(v_msg, 200)));
END$$
DELIMITER ;

-- ---------------------------------------------------------------- 唯一键冲突（期望 1062）
CALL sqltest_expect_error('CON-001', '唯一键：users.username 唯一', 'INSERT INTO users (id, username, password_hash) VALUES (900091, ''sqltest_admin'', ''x'')', 1062);
CALL sqltest_expect_error('CON-002', '唯一键：users.mobile 唯一', 'INSERT INTO users (id, username, password_hash, mobile) VALUES (900091, ''sqltest_dup1'', ''x'', ''19900000001'')', 1062);
CALL sqltest_expect_error('CON-003', '唯一键：users.email 唯一', 'INSERT INTO users (id, username, password_hash, email) VALUES (900091, ''sqltest_dup2'', ''x'', ''admin@sqltest.local'')', 1062);
CALL sqltest_expect_error('CON-004', '唯一键：roles.code 唯一', 'INSERT INTO roles (id, code, name) VALUES (900091, ''sqltest_admin'', ''x'')', 1062);
CALL sqltest_expect_error('CON-005', '唯一键：organizations.code 唯一', 'INSERT INTO organizations (id, code, name) VALUES (900091, ''sqltest_hq'', ''x'')', 1062);
CALL sqltest_expect_error('CON-006', '唯一键：organizations (parent_id,name) 唯一', 'INSERT INTO organizations (id, parent_id, code, name) VALUES (900091, 900001, ''sqltest_dup'', ''测试研发部'')', 1062);
CALL sqltest_expect_error('CON-007', '唯一键：positions.code 唯一', 'INSERT INTO positions (id, title, code, organization_id) VALUES (900091, ''x'', ''sqltest_p001'', 900002)', 1062);
CALL sqltest_expect_error('CON-008', '唯一键：resumes.file_hash 唯一（秒传/查重）', 'INSERT INTO resumes (id, file_name, file_hash) VALUES (900091, ''x'', REPEAT(''a'', 64))', 1062);
CALL sqltest_expect_error('CON-009', '唯一键：talents (mobile,is_deleted) 唯一', 'INSERT INTO talents (id, name, mobile) VALUES (900091, ''x'', ''19900001001'')', 1062);
CALL sqltest_expect_error('CON-010', '唯一键：talent_portraits (talent_id,version) 唯一', 'INSERT INTO talent_portraits (id, talent_id, version) VALUES (900091, 900001, 1)', 1062);
CALL sqltest_expect_error('CON-011', '唯一键：position_requirements (position_id,version) 唯一', 'INSERT INTO position_requirements (id, position_id, version) VALUES (900091, 900001, 1)', 1062);
CALL sqltest_expect_error('CON-012', '唯一键：matching_records (batch,position,talent) 唯一', 'INSERT INTO matching_records (id, batch_id, position_id, talent_id) VALUES (900091, ''sqltest_batch_01'', 900001, 900001)', 1062);
CALL sqltest_expect_error('CON-013', '唯一键：assessment_answers (assessment,question) 唯一', 'INSERT INTO assessment_answers (id, assessment_id, question_id) VALUES (900091, 900001, 900001)', 1062);
CALL sqltest_expect_error('CON-014', '唯一键：assessment_results.assessment_id 唯一（1:1）', 'INSERT INTO assessment_results (id, assessment_id) VALUES (900091, 900001)', 1062);
CALL sqltest_expect_error('CON-015', '唯一键：knowledge_chunks (doc_id,chunk_index) 唯一', 'INSERT INTO knowledge_chunks (id, doc_id, chunk_index, content) VALUES (900091, 900001, 0, ''x'')', 1062);
CALL sqltest_expect_error('CON-016', '唯一键：plan_courses (plan_id,course_id) 唯一', 'INSERT INTO plan_courses (plan_id, course_id) VALUES (900001, 900001)', 1062);
CALL sqltest_expect_error('CON-017', '唯一键：learning_records (plan,course,talent) 唯一', 'INSERT INTO learning_records (id, plan_id, course_id, talent_id) VALUES (900091, 900001, 900001, 900001)', 1062);
CALL sqltest_expect_error('CON-018', '唯一键：user_roles (user_id,role_id) 联合主键', 'INSERT INTO user_roles (user_id, role_id) VALUES (900001, 900001)', 1062);

-- ---------------------------------------------------------------- 外键拒绝（期望 1452）
CALL sqltest_expect_error('CON-030', '外键：users.organization_id 必须存在', 'INSERT INTO users (id, username, password_hash, organization_id) VALUES (900091, ''sqltest_fk1'', ''x'', 999999)', 1452);
CALL sqltest_expect_error('CON-031', '外键：user_roles.user_id 必须存在', 'INSERT INTO user_roles (user_id, role_id) VALUES (999999, 900001)', 1452);
CALL sqltest_expect_error('CON-032', '外键：user_roles.role_id 必须存在', 'INSERT INTO user_roles (user_id, role_id) VALUES (900001, 999999)', 1452);
CALL sqltest_expect_error('CON-033', '外键：talents.organization_id 必须存在', 'INSERT INTO talents (id, name, organization_id) VALUES (900091, ''x'', 999999)', 1452);
CALL sqltest_expect_error('CON-034', '外键：positions.organization_id 必须存在', 'INSERT INTO positions (id, title, code, organization_id) VALUES (900091, ''x'', ''sqltest_fk'', 999999)', 1452);
CALL sqltest_expect_error('CON-035', '外键：resumes.talent_id 必须存在', 'INSERT INTO resumes (id, file_name, file_hash, talent_id) VALUES (900091, ''x'', REPEAT(''f'', 64), 999999)', 1452);
CALL sqltest_expect_error('CON-036', '外键：resumes.file_id 必须存在', 'INSERT INTO resumes (id, file_name, file_hash, file_id) VALUES (900091, ''x'', REPEAT(''f'', 64), 999999)', 1452);
CALL sqltest_expect_error('CON-037', '外键：resume_experiences.resume_id 必须存在', 'INSERT INTO resume_experiences (id, resume_id, company) VALUES (900091, 999999, ''x'')', 1452);
CALL sqltest_expect_error('CON-038', '外键：assessments.talent_id 必须存在', 'INSERT INTO assessments (id, talent_id, title) VALUES (900091, 999999, ''x'')', 1452);
CALL sqltest_expect_error('CON-039', '外键：assessment_questions.assessment_id 必须存在', 'INSERT INTO assessment_questions (id, assessment_id, content) VALUES (900091, 999999, ''x'')', 1452);
CALL sqltest_expect_error('CON-040', '外键：assessment_answers.question_id 必须存在', 'INSERT INTO assessment_answers (id, assessment_id, question_id) VALUES (900091, 900001, 999999)', 1452);
CALL sqltest_expect_error('CON-041', '外键：training_plans.talent_id 必须存在', 'INSERT INTO training_plans (id, name, talent_id) VALUES (900091, ''x'', 999999)', 1452);
CALL sqltest_expect_error('CON-042', '外键：plan_courses.course_id 必须存在', 'INSERT INTO plan_courses (plan_id, course_id) VALUES (900001, 999999)', 1452);
CALL sqltest_expect_error('CON-043', '外键：learning_records.talent_id 必须存在', 'INSERT INTO learning_records (id, talent_id) VALUES (900091, 999999)', 1452);
CALL sqltest_expect_error('CON-044', '外键：knowledge_docs.file_id 必须存在', 'INSERT INTO knowledge_docs (id, title, file_id) VALUES (900091, ''x'', 999999)', 1452);
CALL sqltest_expect_error('CON-045', '外键：knowledge_chunks.doc_id 必须存在', 'INSERT INTO knowledge_chunks (id, doc_id, chunk_index, content) VALUES (900091, 999999, 0, ''x'')', 1452);
CALL sqltest_expect_error('CON-046', '外键：conversations.user_id 必须存在', 'INSERT INTO conversations (id, user_id) VALUES (900091, 999999)', 1452);
CALL sqltest_expect_error('CON-047', '外键：chat_messages.conversation_id 必须存在', 'INSERT INTO chat_messages (id, conversation_id) VALUES (900091, 999999)', 1452);
CALL sqltest_expect_error('CON-048', '外键：files.uploader_id 必须存在', 'INSERT INTO files (id, file_name, stored_name, file_path, biz_type, uploader_id) VALUES (900091, ''x'', ''x'', ''x'', ''resume'', 999999)', 1452);
CALL sqltest_expect_error('CON-049', '外键：audit_logs.user_id 必须存在', 'INSERT INTO audit_logs (id, action, user_id) VALUES (900091, ''create'', 999999)', 1452);
CALL sqltest_expect_error('CON-050', '外键：organizations.parent_id 必须存在', 'INSERT INTO organizations (id, parent_id, code, name) VALUES (900091, 999999, ''sqltest_fk'', ''x'')', 1452);

-- ---------------------------------------------------------------- 非空 / 类型 / 取值
CALL sqltest_expect_error('CON-060', '非空：users.password_hash 不允许 NULL', 'INSERT INTO users (id, username, password_hash) VALUES (900091, ''sqltest_nn'', NULL)', 1048);
CALL sqltest_expect_error('CON-061', '非空：talents.name 不允许 NULL', 'INSERT INTO talents (id, name) VALUES (900091, NULL)', 1048);
CALL sqltest_expect_error('CON-062', '长度：users.username 超过 64 字符被拒绝（严格模式）', CONCAT('INSERT INTO users (id, username, password_hash) VALUES (900091, ''', REPEAT('x', 65), ''', ''x'')'), 1406);
CALL sqltest_expect_error('CON-063', 'JSON：talents.skill_tags 非法 JSON 被拒绝', 'INSERT INTO talents (id, name, skill_tags) VALUES (900091, ''x'', ''{bad json'')', 3140);
CALL sqltest_expect_error('CON-064', '精度：matching_records.score 超过 NUMERIC(5,2) 被拒绝', 'INSERT INTO matching_records (id, batch_id, position_id, talent_id, score) VALUES (900091, ''sqltest_x'', 900001, 900002, 1000.00)', 1264);

-- ---------------------------------------------------------------- 默认值行为（插入最小行后校验）
INSERT INTO users (id, username, password_hash) VALUES (900090, 'sqltest_defaults', 'x');
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-070', '默认值：users 最小插入', is_active = 1 AND is_superuser = 0 AND fail_count = 0 AND version = 1 AND is_deleted = 0,
       CONCAT('is_active=', is_active, ' version=', version) FROM users WHERE id = 900090;
INSERT INTO talents (id, name) VALUES (900090, '测试默认值');
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-071', '默认值：talents 最小插入', gender = 'unknown' AND work_years = 0 AND status = 'active' AND source = 'manual',
       CONCAT('gender=', gender, ' status=', status) FROM talents WHERE id = 900090;
INSERT INTO positions (id, title, code, organization_id) VALUES (900090, '测试默认岗位', 'sqltest_pdef', 900002);
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-072', '默认值：positions 最小插入', level = 'middle' AND headcount = 1 AND status = 'open',
       CONCAT('level=', level, ' status=', status) FROM positions WHERE id = 900090;
INSERT INTO resumes (id, file_name, file_hash) VALUES (900090, '测试默认简历', REPEAT('e', 64));
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-073', '默认值：resumes 最小插入', status = 'uploaded' AND file_size = 0, CONCAT('status=', status) FROM resumes WHERE id = 900090;
INSERT INTO knowledge_docs (id, title) VALUES (900090, '测试默认文档');
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-074', '默认值：knowledge_docs 最小插入', source_type = 'text' AND status = 'indexing' AND permission_scope = 'public',
       CONCAT('status=', status) FROM knowledge_docs WHERE id = 900090;
INSERT INTO conversations (id, user_id) VALUES (900090, 900003);
INSERT INTO chat_messages (id, conversation_id) VALUES (900090, 900090);
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-075', '默认值：chat_messages 最小插入', role = 'user' AND prompt_tokens = 0 AND completion_tokens = 0,
       CONCAT('role=', role) FROM chat_messages WHERE id = 900090;

-- ---------------------------------------------------------------- 乐观锁 / 逻辑删除 / 更新语义
UPDATE talents SET work_years = work_years + 1, version = version + 1 WHERE id = 900001 AND version = 1;
SET @rc := ROW_COUNT();
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-080', '乐观锁：version 匹配时更新成功', @rc = 1, CONCAT('affected=', @rc);
UPDATE talents SET work_years = work_years + 1, version = version + 1 WHERE id = 900001 AND version = 1;
SET @rc := ROW_COUNT();
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-081', '乐观锁：version 过期时更新 0 行', @rc = 0, CONCAT('affected=', @rc);
UPDATE talents SET is_deleted = 1, deleted_at = NOW() WHERE id = 900003;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-082', '逻辑删除：is_deleted=1 后默认查询不可见', COUNT(*) = 0, CONCAT('可见行数=', COUNT(*))
FROM talents WHERE id = 900003 AND is_deleted = 0;
INSERT INTO talents (id, name, mobile) VALUES (900092, '测试王五重建', '19900001003');
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-083', '逻辑删除后同手机号可重建（mobile+is_deleted 唯一）', COUNT(*) = 1, CONCAT('新行=', COUNT(*))
FROM talents WHERE id = 900092 AND is_deleted = 0;
DELETE FROM talents WHERE id = 900092;
UPDATE talents SET is_deleted = 0, deleted_at = NULL WHERE id = 900003;

-- ---------------------------------------------------------------- 外键级联 / SET NULL / RESTRICT（用后即毁的隔离数据）
-- 场景 A：删除人才 -> 画像/测评/培训 CASCADE，简历 SET NULL
INSERT INTO talents (id, name) VALUES (900095, '测试级联人才');
INSERT INTO talent_portraits (id, talent_id, version) VALUES (900095, 900095, 1);
INSERT INTO assessments (id, talent_id, title) VALUES (900095, 900095, '测试级联测评');
INSERT INTO assessment_questions (id, assessment_id, content) VALUES (900095, 900095, '测试级联题目');
INSERT INTO assessment_answers (id, assessment_id, question_id) VALUES (900095, 900095, 900095);
INSERT INTO assessment_results (id, assessment_id) VALUES (900095, 900095);
INSERT INTO training_plans (id, name, talent_id) VALUES (900095, '测试级联计划', 900095);
INSERT INTO plan_courses (plan_id, course_id) VALUES (900095, 900001);
INSERT INTO learning_records (id, plan_id, course_id, talent_id) VALUES (900095, 900095, 900001, 900095);
INSERT INTO resumes (id, talent_id, file_name, file_hash) VALUES (900095, 900095, '测试级联简历', REPEAT('d', 64));
DELETE FROM talents WHERE id = 900095;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-090', 'CASCADE：删人才 -> 画像/测评/题目/答题/结果/计划/关联/学习记录全删',
       (SELECT COUNT(*) FROM talent_portraits WHERE talent_id = 900095)
     + (SELECT COUNT(*) FROM assessments WHERE talent_id = 900095)
     + (SELECT COUNT(*) FROM assessment_questions WHERE assessment_id = 900095)
     + (SELECT COUNT(*) FROM assessment_answers WHERE assessment_id = 900095)
     + (SELECT COUNT(*) FROM assessment_results WHERE assessment_id = 900095)
     + (SELECT COUNT(*) FROM training_plans WHERE talent_id = 900095)
     + (SELECT COUNT(*) FROM plan_courses WHERE plan_id = 900095)
     + (SELECT COUNT(*) FROM learning_records WHERE talent_id = 900095) = 0, '残留行总数应为 0';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-091', 'SET NULL：删人才 -> 简历保留且 talent_id 置空', talent_id IS NULL, CONCAT('talent_id=', COALESCE(talent_id, 'NULL'))
FROM resumes WHERE id = 900095;
DELETE FROM resumes WHERE id = 900095;

-- 场景 B：删除用户 -> 角色绑定/会话/消息 CASCADE，文件/审计 SET NULL
INSERT INTO users (id, username, password_hash) VALUES (900096, 'sqltest_cascade_user', 'x');
INSERT INTO user_roles (user_id, role_id) VALUES (900096, 900003);
INSERT INTO conversations (id, user_id) VALUES (900096, 900096);
INSERT INTO chat_messages (id, conversation_id) VALUES (900096, 900096);
INSERT INTO files (id, file_name, stored_name, file_path, biz_type, uploader_id) VALUES (900096, 'x', 'x', 'x', 'resume', 900096);
INSERT INTO audit_logs (id, user_id, username, action) VALUES (900096, 900096, 'sqltest_cascade_user', 'create');
DELETE FROM users WHERE id = 900096;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-092', 'CASCADE：删用户 -> 角色绑定/会话/消息全删',
       (SELECT COUNT(*) FROM user_roles WHERE user_id = 900096)
     + (SELECT COUNT(*) FROM conversations WHERE id = 900096)
     + (SELECT COUNT(*) FROM chat_messages WHERE conversation_id = 900096) = 0, '残留行总数应为 0';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-093', 'SET NULL：删用户 -> 文件保留且 uploader_id 置空', uploader_id IS NULL, 'uploader_id 应为 NULL'
FROM files WHERE id = 900096;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-094', 'SET NULL：删用户 -> 审计保留且 user_id 置空（审计不丢）', user_id IS NULL AND username = 'sqltest_cascade_user', '审计行必须保留'
FROM audit_logs WHERE id = 900096;
DELETE FROM files WHERE id = 900096;
DELETE FROM audit_logs WHERE id = 900096;

-- 场景 C：删除课程 -> 计划关联 CASCADE，学习记录 SET NULL；删除计划 -> 学习记录 plan_id SET NULL
INSERT INTO courses (id, title) VALUES (900097, '测试级联课程');
INSERT INTO plan_courses (plan_id, course_id) VALUES (900002, 900097);
INSERT INTO learning_records (id, plan_id, course_id, talent_id) VALUES (900097, 900002, 900097, 900002);
DELETE FROM courses WHERE id = 900097;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-095', 'CASCADE+SET NULL：删课程 -> 计划关联删除、学习记录 course_id 置空',
       (SELECT COUNT(*) FROM plan_courses WHERE course_id = 900097) = 0
       AND (SELECT course_id FROM learning_records WHERE id = 900097) IS NULL, '关联应删、学习记录应保留';
DELETE FROM training_plans WHERE id = 900002;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-096', 'SET NULL：删计划 -> 学习记录保留且 plan_id 置空', plan_id IS NULL, 'plan_id 应为 NULL'
FROM learning_records WHERE id = 900097;
DELETE FROM learning_records WHERE id = 900097;

-- 场景 D：岗位被引用时删除被 RESTRICT 拒绝；先删岗位后测评 position_id 置空
CALL sqltest_expect_error('CON-097', 'RESTRICT：部门下仍有岗位时删除部门被拒绝', 'DELETE FROM organizations WHERE id = 900002', 1451);
INSERT INTO positions (id, title, code, organization_id) VALUES (900098, '测试级联岗位', 'sqltest_pcas', 900002);
INSERT INTO position_requirements (id, position_id, version) VALUES (900098, 900098, 1);
INSERT INTO assessments (id, talent_id, position_id, title) VALUES (900098, 900001, 900098, '测试级联岗位测评');
DELETE FROM positions WHERE id = 900098;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-098', 'CASCADE：删岗位 -> 能力模型版本全删', COUNT(*) = 0, '残留应为 0' FROM position_requirements WHERE position_id = 900098;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-099', 'SET NULL：删岗位 -> 测评保留且 position_id 置空', position_id IS NULL, 'position_id 应为 NULL' FROM assessments WHERE id = 900098;
DELETE FROM assessments WHERE id = 900098;

-- 场景 E：删简历 -> 工作经历 CASCADE；删文档 -> 切片 CASCADE；删文件 -> 简历/文档 file_id SET NULL
DELETE FROM resumes WHERE id = 900001;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-100', 'CASCADE：删简历 -> 工作经历全删', COUNT(*) = 0, '残留应为 0' FROM resume_experiences WHERE resume_id = 900001;
DELETE FROM knowledge_docs WHERE id = 900001;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-101', 'CASCADE：删文档 -> 切片全删', COUNT(*) = 0, '残留应为 0' FROM knowledge_chunks WHERE doc_id = 900001;
INSERT INTO files (id, file_name, stored_name, file_path, biz_type) VALUES (900099, '测试级联文件', 'x', 'x', 'knowledge');
INSERT INTO knowledge_docs (id, title, source_type, file_id) VALUES (900099, '测试级联文档', 'file', 900099);
INSERT INTO resumes (id, file_id, file_name, file_hash) VALUES (900099, 900099, '测试级联简历', REPEAT('9', 64));
DELETE FROM files WHERE id = 900099;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-102', 'SET NULL：删文件 -> 文档保留且 file_id 置空', file_id IS NULL, 'file_id 应为 NULL'
FROM knowledge_docs WHERE id = 900099;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'CON-103', 'SET NULL：删文件 -> 简历保留且 file_id 置空', file_id IS NULL, 'file_id 应为 NULL'
FROM resumes WHERE id = 900099;
DELETE FROM knowledge_docs WHERE id = 900099;
DELETE FROM resumes WHERE id = 900099;
