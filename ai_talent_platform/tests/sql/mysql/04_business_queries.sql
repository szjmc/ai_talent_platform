-- ============================================================================
-- 04_business_queries.sql — 业务查询验收（权限过滤 / JSON / 聚合 / TopN / 分页）
-- 标签：[业务组] [P1]（核心业务 SQL 的正确性：与 Service 层查询语义一致）
--
-- 注意：03 会破坏部分种子数据（级联测试），本文件自带所需数据，不依赖 03 之后的状态。
-- 依赖：01（结果表/护栏）。全部查询限定在 900000-900999 测试段。
-- ============================================================================
SET NAMES utf8mb4;
CALL sqltest_guard();
DELETE FROM sqltest_results WHERE test_id LIKE 'BIZ-%';

-- 重建本文件专用数据（幂等）
DELETE FROM resume_experiences WHERE resume_id = 900001;
DELETE FROM resumes WHERE id IN (900001, 900002);
DELETE FROM knowledge_chunks WHERE doc_id IN (900001, 900002, 900003);
DELETE FROM knowledge_docs WHERE id IN (900001, 900002, 900003);
DELETE FROM files WHERE id IN (900001, 900002);
DELETE FROM matching_records WHERE batch_id LIKE 'sqltest_batch%';
DELETE FROM training_plans WHERE id = 900002;
DELETE FROM learning_records WHERE id IN (900001, 900002, 900003);
INSERT INTO files (id, file_name, stored_name, file_path, file_size, extension, file_hash, biz_type, uploader_id) VALUES
  (900001, '测试简历_张三.pdf', 'sqltest_r1.pdf', 'resume/2026/10/sqltest_r1.pdf', 204800, '.pdf', REPEAT('a', 64), 'resume', 900002),
  (900002, '测试制度手册.docx', 'sqltest_k1.docx', 'knowledge/2026/10/sqltest_k1.docx', 102400, '.docx', REPEAT('b', 64), 'knowledge', 900002);
INSERT INTO resumes (id, talent_id, file_id, file_name, file_type, file_size, file_hash, status, accuracy, parsed_at) VALUES
  (900001, 900001, 900001, '测试简历_张三.pdf', 'pdf', 204800, REPEAT('a', 64), 'success', 0.950, '2026-10-01 11:00:00'),
  (900002, NULL, NULL, '测试简历_待解析.pdf', 'pdf', 102400, REPEAT('c', 64), 'uploaded', NULL, NULL);
INSERT INTO resume_experiences (id, resume_id, company, title, start_date, sort) VALUES
  (900001, 900001, '测试科技公司', '后端工程师', '2021-07-01', 1),
  (900002, 900001, '测试软件公司', '初级工程师', '2019-07-01', 2);
INSERT INTO knowledge_docs (id, title, category, source_type, file_id, status, chunk_count, permission_scope, allowed_roles, organization_id) VALUES
  (900001, '测试员工手册', '制度', 'file', 900002, 'indexed', 2, 'role', JSON_ARRAY('sqltest_hr', 'sqltest_emp'), NULL),
  (900002, '测试技术FAQ',  '技术', 'text', NULL,   'indexed', 1, 'public', NULL, NULL),
  (900003, '测试研发机密', '技术', 'text', NULL,   'indexed', 0, 'dept', NULL, 900002);
INSERT INTO knowledge_chunks (id, doc_id, chunk_index, content, token_count, page_no) VALUES
  (900001, 900001, 0, '测试员工手册第一章：考勤制度。', 18, 1),
  (900002, 900001, 1, '测试员工手册第二章：报销流程。', 16, 2),
  (900003, 900002, 0, '测试技术FAQ第一条。', 10, 1);
INSERT INTO matching_records (id, batch_id, position_id, talent_id, score, dimension_scores, strategy, matched_at) VALUES
  (900001, 'sqltest_batch_01', 900001, 900001, 88.50, JSON_OBJECT('skill', 90, 'experience', 85, 'education', 80, 'quality', 92), 'hybrid', '2026-10-03 10:00:00'),
  (900002, 'sqltest_batch_01', 900001, 900002, 76.00, JSON_OBJECT('skill', 75, 'experience', 70, 'education', 90, 'quality', 80), 'hybrid', '2026-10-03 10:00:00'),
  (900003, 'sqltest_batch_01', 900001, 900003, 62.30, JSON_OBJECT('skill', 60, 'experience', 65, 'education', 95, 'quality', 70), 'hybrid', '2026-10-03 10:00:00'),
  (900004, 'sqltest_batch_02', 900002, 900002, 91.20, JSON_OBJECT('skill', 95, 'experience', 85, 'education', 90, 'quality', 88), 'soft', '2026-10-04 10:00:00');
INSERT INTO training_plans (id, name, talent_id, status) VALUES (900002, '测试李四算法计划', 900002, 'draft');
INSERT INTO plan_courses (plan_id, course_id, sort) VALUES (900002, 900001, 1);
INSERT INTO learning_records (id, plan_id, course_id, talent_id, progress, study_minutes, status, score, passed) VALUES
  (900001, 900001, 900001, 900001, 100, 720, 'completed', 92.00, 1),
  (900002, 900001, 900002, 900001, 40,  192, 'learning',  NULL,   NULL),
  (900003, 900002, 900001, 900002, 0,   0,   'not_started', NULL, NULL);
UPDATE talents SET work_years = 5, version = 1, is_deleted = 0, deleted_at = NULL WHERE id = 900001;

-- ---------------------------------------------------------------- 组织 / 权限 / 用户
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-001', '组织树：按 path 前缀查研发部 subtree', (SELECT COUNT(*) FROM organizations WHERE id = 900002 OR path LIKE '/900001/900002/%') = 1, '研发部 subtree 应含自身';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-002', '数据范围：HR 部门用户数', (SELECT COUNT(*) FROM users u JOIN organizations o ON u.organization_id = o.id WHERE o.code = 'sqltest_hr' AND u.is_deleted = 0) = 1, 'HR 部门应只有 hr01';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-003', 'RBAC：sqltest_hr01 的角色编码', (SELECT GROUP_CONCAT(r.code ORDER BY r.code) FROM users u JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id WHERE u.username = 'sqltest_hr01') = 'sqltest_hr', '角色应为 sqltest_hr';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-004', '部门负责人逻辑外键可 JOIN', (SELECT COUNT(*) FROM organizations o JOIN users u ON o.leader_id = u.id WHERE o.code = 'sqltest_hr') = 1, '负责人应为 hr01';

-- ---------------------------------------------------------------- 人才 / JSON 技能
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-010', 'JSON_CONTAINS：会 Python 的人才数', (SELECT COUNT(*) FROM talents WHERE id BETWEEN 900000 AND 900999 AND is_deleted = 0 AND JSON_CONTAINS(skill_tags, JSON_QUOTE('Python'))) = 3, '张三/李四/王五都会 Python';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-011', 'JSON_TABLE：技能标签展开统计', (SELECT COUNT(*) FROM talents t, JSON_TABLE(t.skill_tags, '$[*]' COLUMNS (skill VARCHAR(64) PATH '$')) jt WHERE t.id BETWEEN 900000 AND 900999 AND jt.skill = 'SQL') = 2, '张三/李四会 SQL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-012', '学历分布聚合', (SELECT COUNT(*) FROM talents WHERE id BETWEEN 900000 AND 900999 AND is_deleted = 0 AND education IN ('硕士','博士')) = 2, '硕士+博士共 2 人';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-013', '画像最新版本（窗口函数）', (SELECT COUNT(*) FROM (SELECT talent_id, ROW_NUMBER() OVER (PARTITION BY talent_id ORDER BY version DESC) rn FROM talent_portraits WHERE talent_id BETWEEN 900000 AND 900999) x WHERE rn = 1) = 2, '两个画像各取最新版';

-- ---------------------------------------------------------------- 岗位 / 匹配 TopN
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-020', '在招岗位过滤', (SELECT COUNT(*) FROM positions WHERE code IN ('sqltest_p001','sqltest_p002') AND status = 'open' AND is_deleted = 0) = 1, '两个种子岗位中在招 1 个';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-021', '匹配 TopN：batch_01 岗位 900001 最高分', (SELECT talent_id FROM matching_records WHERE batch_id = 'sqltest_batch_01' AND position_id = 900001 ORDER BY score DESC, talent_id ASC LIMIT 1) = 900001, '最高分应为张三 88.5';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-022', '匹配 TopN：batch_01 前三平均分', (SELECT ROUND(AVG(score), 2) FROM (SELECT score FROM matching_records WHERE batch_id = 'sqltest_batch_01' AND position_id = 900001 ORDER BY score DESC LIMIT 3) x) = 75.60, '(88.5+76+62.3)/3=75.6';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-023', '匹配维度分 JSON 提取', (SELECT JSON_EXTRACT(dimension_scores, '$.skill') FROM matching_records WHERE id = 900001) = 90, 'skill 维度应为 90';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-024', '匹配批次去重计数', (SELECT COUNT(DISTINCT batch_id) FROM matching_records WHERE position_id = 900001) = 1, '岗位 900001 只有 batch_01';

-- ---------------------------------------------------------------- 简历 / 查重
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-030', '简历秒传：file_hash 命中', (SELECT COUNT(*) FROM resumes WHERE file_hash = REPEAT('a', 64)) = 1, '同 hash 应命中 1 条';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-031', '简历状态机：待解析队列', (SELECT COUNT(*) FROM resumes WHERE status = 'uploaded' AND id BETWEEN 900000 AND 900999) = 1, '待解析 1 条';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-032', '工作经历按时间排序', (SELECT GROUP_CONCAT(company ORDER BY start_date DESC) FROM resume_experiences WHERE resume_id = 900001) = '测试科技公司,测试软件公司', '最新经历在前';

-- ---------------------------------------------------------------- 测评 / 培训聚合
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-040', '测评答题总分与结果一致', (SELECT COALESCE(SUM(score), 0) FROM assessment_answers WHERE assessment_id = 900001) = 85.00, '40+45=85';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-041', '测评结果等级', (SELECT level FROM assessment_results WHERE assessment_id = 900001) = '高级', '等级应为高级';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-042', '培训完成率：张三计划 900001', (SELECT ROUND(100.0 * SUM(status = 'completed') / COUNT(*), 2) FROM learning_records WHERE plan_id = 900001) = 50.00, '2 门课完成 1 门';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-043', '累计学习分钟', (SELECT SUM(study_minutes) FROM learning_records WHERE talent_id = 900001) = 912, '720+192=912';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-044', '课程上架过滤', (SELECT COUNT(*) FROM courses WHERE id BETWEEN 900000 AND 900999 AND is_active = 1) = 2, '上架 2 门';

-- ---------------------------------------------------------------- 知识库权限 / 会话 / 审计
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-050', '知识库权限：public + role 命中（HR 视角）', (SELECT COUNT(*) FROM knowledge_docs WHERE id BETWEEN 900000 AND 900999 AND status = 'indexed' AND (permission_scope = 'public' OR (permission_scope = 'role' AND JSON_CONTAINS(allowed_roles, JSON_QUOTE('sqltest_hr'))))) = 2, 'HR 可见手册+FAQ';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-051', '知识库权限：dept 范围（研发部视角）', (SELECT COUNT(*) FROM knowledge_docs WHERE id BETWEEN 900000 AND 900999 AND status = 'indexed' AND (permission_scope = 'public' OR (permission_scope = 'dept' AND organization_id = 900002))) = 2, '研发可见 FAQ+机密';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-052', '知识库权限：普通员工看不到研发机密', (SELECT COUNT(*) FROM knowledge_docs WHERE id BETWEEN 900000 AND 900999 AND status = 'indexed' AND (permission_scope = 'public' OR (permission_scope = 'role' AND JSON_CONTAINS(allowed_roles, JSON_QUOTE('sqltest_emp'))))) = 2, '员工可见手册+FAQ，不含机密';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-053', '切片按 doc+index 有序', (SELECT GROUP_CONCAT(chunk_index ORDER BY chunk_index) FROM knowledge_chunks WHERE doc_id = 900001) = '0,1', '切片序号 0,1';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-054', '会话消息窗口（最近 N 条）', (SELECT COUNT(*) FROM (SELECT id FROM chat_messages WHERE conversation_id = 900001 ORDER BY created_at DESC, id DESC LIMIT 10) x) = 2, '会话 900001 共 2 条';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-055', '会话 token 成本合计', (SELECT SUM(prompt_tokens + completion_tokens) FROM chat_messages WHERE conversation_id = 900001) = 200, '120+80=200';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-056', 'RAG 引用 JSON 提取', (SELECT JSON_EXTRACT(citations, '$[0].doc_id') FROM chat_messages WHERE id = 900002) = 900001, '引用应指向文档 900001';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-057', '审计按模块+动作统计', (SELECT COUNT(*) FROM audit_logs WHERE user_id = 900002 AND module = 'agent' AND action = 'ai_call') = 1, 'ai_call 审计 1 条';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-058', '审计 trace_id 检索', (SELECT action FROM audit_logs WHERE trace_id = 'sqltest-trace-001') = 'create', 'trace 对应 create';

-- ---------------------------------------------------------------- 分页 / 逻辑删除
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-060', '分页：第 1 页 2 条（按 id）', (SELECT GROUP_CONCAT(id ORDER BY id) FROM (SELECT id FROM talents WHERE id BETWEEN 900000 AND 900999 AND is_deleted = 0 ORDER BY id LIMIT 2 OFFSET 0) x) = '900001,900002', '第一页应为 900001,900002';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-061', '分页：第 2 页 2 条', (SELECT GROUP_CONCAT(id ORDER BY id) FROM (SELECT id FROM talents WHERE id BETWEEN 900000 AND 900999 AND is_deleted = 0 ORDER BY id LIMIT 2 OFFSET 2) x) = '900003,900090', '第二页应为 900003,900090';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'BIZ-062', '逻辑删除过滤后总数', (SELECT COUNT(*) FROM talents WHERE id BETWEEN 900000 AND 900999 AND is_deleted = 0) = 4, '900001/2/3 + 默认值行 900090';
