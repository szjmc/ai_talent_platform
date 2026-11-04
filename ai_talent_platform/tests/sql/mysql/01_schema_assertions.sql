-- ============================================================================
-- 01_schema_assertions.sql — MySQL 业务库结构断言（25 张表）
-- 标签：[业务组] [P1]（结构验收：表/字段/唯一键/外键删除规则/默认值）
--
-- 本文件由 ORM 元数据（app/models/*）推导生成，模型变更后需同步更新。
-- 运行前提：已在测试库执行 schema/mysql_schema.sql（或 scripts/init_db.py）建表。
-- 结果写入 sqltest_results 表，99_cleanup.sql 输出汇总并清理。
-- ============================================================================
SET NAMES utf8mb4;
SET SESSION group_concat_max_len = 100000;

-- 安全护栏：仅允许在名称含 test 的数据库执行（防误跑生产库）
DROP PROCEDURE IF EXISTS sqltest_guard;
DELIMITER $$
CREATE PROCEDURE sqltest_guard()
BEGIN
  IF DATABASE() IS NULL OR DATABASE() NOT LIKE '%test%' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SQL 测试只允许在名称含 test 的数据库执行（如 ai_talent_platform_test）';
  END IF;
END$$
DELIMITER ;
CALL sqltest_guard();

-- 测试数据清理过程：删除 900000-900999 测试 ID 段（按外键安全顺序）
DROP PROCEDURE IF EXISTS sqltest_cleanup_data;
DELIMITER $$
CREATE PROCEDURE sqltest_cleanup_data()
BEGIN
  DELETE FROM assessment_answers WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM assessment_results WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM assessment_questions WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM assessments WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM resume_experiences WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM resumes WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM learning_records WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM plan_courses WHERE plan_id BETWEEN 900000 AND 900999 OR course_id BETWEEN 900000 AND 900999;
  DELETE FROM training_plans WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM courses WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM knowledge_chunks WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM knowledge_docs WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM chat_messages WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM conversations WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM matching_records WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM talent_portraits WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM talents WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM position_requirements WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM positions WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM user_roles WHERE user_id BETWEEN 900000 AND 900999 OR role_id BETWEEN 900000 AND 900999;
  DELETE FROM users WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM roles WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM organizations WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM files WHERE id BETWEEN 900000 AND 900999;
  DELETE FROM audit_logs WHERE id BETWEEN 900000 AND 900999;
END$$
DELIMITER ;

CREATE TABLE IF NOT EXISTS sqltest_results (
  test_id   VARCHAR(16)  NOT NULL,
  test_name VARCHAR(255) NOT NULL,
  passed    TINYINT(1)   NOT NULL,
  detail    VARCHAR(512) DEFAULT NULL,
  ran_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_sqltest_results_id (test_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SQL 测试结果（99_cleanup 删除）';
DELETE FROM sqltest_results WHERE test_id LIKE 'SCH-%';

-- ---------------------------------------------------------------- 表齐全性
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-001', '25 张业务表全部存在', COUNT(*) = 25, CONCAT('实际表数=', COUNT(*))
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
  AND table_name IN ('assessment_answers', 'assessment_questions', 'assessment_results', 'assessments', 'audit_logs', 'chat_messages', 'conversations', 'courses', 'files', 'knowledge_chunks', 'knowledge_docs', 'learning_records', 'matching_records', 'organizations', 'plan_courses', 'position_requirements', 'positions', 'resume_experiences', 'resumes', 'roles', 'talent_portraits', 'talents', 'training_plans', 'user_roles', 'users');

INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-002', '全部业务表使用 InnoDB 引擎', COUNT(*) = 25, CONCAT('InnoDB 表数=', COUNT(*))
FROM information_schema.tables
WHERE table_schema = DATABASE() AND engine = 'InnoDB'
  AND table_name IN ('assessment_answers', 'assessment_questions', 'assessment_results', 'assessments', 'audit_logs', 'chat_messages', 'conversations', 'courses', 'files', 'knowledge_chunks', 'knowledge_docs', 'learning_records', 'matching_records', 'organizations', 'plan_courses', 'position_requirements', 'positions', 'resume_experiences', 'resumes', 'roles', 'talent_portraits', 'talents', 'training_plans', 'user_roles', 'users');

INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-003', 'users 表字符集为 utf8mb4', COUNT(*) = 1, CONCAT('collation=', MAX(table_collation))
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_name = 'users' AND table_collation LIKE 'utf8mb4%';

-- ---------------------------------------------------------------- 字段齐全性（逐表比对字段名集合）
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-010', 'assessment_answers 字段齐全（14 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'assessment_answers'), '')
       = 'answer,assessment_id,created_at,created_by,deleted_at,id,is_deleted,question_id,score,scored_at,scored_by,updated_at,updated_by,version',
       CONCAT('期望 14 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-011', 'assessment_questions 字段齐全（16 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'assessment_questions'), '')
       = 'answer,assessment_id,content,created_at,created_by,deleted_at,dimension,id,is_deleted,options,score,sort,type,updated_at,updated_by,version',
       CONCAT('期望 16 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-012', 'assessment_results 字段齐全（16 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'assessment_results'), '')
       = 'assessment_id,created_at,created_by,deleted_at,dimension_scores,id,is_deleted,level,model_name,prompt_version,report,suggestions,total_score,updated_at,updated_by,version',
       CONCAT('期望 16 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-013', 'assessments 字段齐全（18 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'assessments'), '')
       = 'created_at,created_by,created_by_ai,deadline,deleted_at,id,is_deleted,position_id,question_count,started_at,status,submitted_at,talent_id,title,total_score,updated_at,updated_by,version',
       CONCAT('期望 18 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-014', 'audit_logs 字段齐全（23 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'audit_logs'), '')
       = 'action,after_data,before_data,created_at,created_by,deleted_at,error_message,id,is_deleted,module,request_ip,request_method,request_path,status,target_id,target_type,trace_id,updated_at,updated_by,user_agent,user_id,username,version',
       CONCAT('期望 23 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-015', 'chat_messages 字段齐全（17 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'chat_messages'), '')
       = 'citations,completion_tokens,content,conversation_id,created_at,created_by,deleted_at,elapsed_ms,id,is_deleted,model_name,prompt_tokens,role,tool_calls,updated_at,updated_by,version',
       CONCAT('期望 17 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-016', 'conversations 字段齐全（14 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'conversations'), '')
       = 'agent_type,created_at,created_by,deleted_at,id,is_deleted,last_message_at,message_count,status,title,updated_at,updated_by,user_id,version',
       CONCAT('期望 14 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-017', 'courses 字段齐全（17 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'courses'), '')
       = 'category,cover,created_at,created_by,deleted_at,description,duration_hours,id,is_active,is_deleted,level,resource_url,skill_tags,title,updated_at,updated_by,version',
       CONCAT('期望 17 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-018', 'files 字段齐全（21 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'files'), '')
       = 'biz_id,biz_type,content_type,created_at,created_by,deleted_at,download_count,extension,file_hash,file_name,file_path,file_size,id,is_deleted,is_public,storage_type,stored_name,updated_at,updated_by,uploader_id,version',
       CONCAT('期望 21 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-019', 'knowledge_chunks 字段齐全（14 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks'), '')
       = 'chunk_index,content,created_at,created_by,deleted_at,doc_id,id,is_deleted,milvus_pk,page_no,token_count,updated_at,updated_by,version',
       CONCAT('期望 14 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-020', 'knowledge_docs 字段齐全（20 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'knowledge_docs'), '')
       = 'allowed_roles,category,chunk_count,created_at,created_by,deleted_at,embedding_model,file_id,id,index_error,indexed_at,is_deleted,organization_id,permission_scope,source_type,status,title,updated_at,updated_by,version',
       CONCAT('期望 20 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-021', 'learning_records 字段齐全（18 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'learning_records'), '')
       = 'course_id,created_at,created_by,deleted_at,id,is_deleted,last_study_at,passed,plan_id,progress,score,status,study_minutes,suspicious,talent_id,updated_at,updated_by,version',
       CONCAT('期望 18 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-022', 'matching_records 字段齐全（20 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'matching_records'), '')
       = 'batch_id,created_at,created_by,deleted_at,dimension_scores,gaps,highlights,id,is_deleted,matched_at,matched_by,model_name,position_id,score,strategy,suggestion,talent_id,updated_at,updated_by,version',
       CONCAT('期望 20 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-023', 'organizations 字段齐全（17 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'organizations'), '')
       = 'code,created_at,created_by,deleted_at,id,is_active,is_deleted,leader_id,level,name,parent_id,path,remark,sort,updated_at,updated_by,version',
       CONCAT('期望 17 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-024', 'plan_courses 字段齐全（6 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'plan_courses'), '')
       = 'course_id,created_at,plan_id,require_pass,sort,updated_at',
       CONCAT('期望 6 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-025', 'position_requirements 字段齐全（12 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'position_requirements'), '')
       = 'created_at,created_by,deleted_at,dimensions,id,is_deleted,position_id,source,updated_at,updated_by,version,weights',
       CONCAT('期望 12 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-026', 'positions 字段齐全（21 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'positions'), '')
       = 'close_at,code,created_at,created_by,deleted_at,description,headcount,id,is_deleted,level,organization_id,publish_at,requirements,salary_max,salary_min,skill_tags,status,title,updated_at,updated_by,version',
       CONCAT('期望 21 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-027', 'resume_experiences 字段齐全（15 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'resume_experiences'), '')
       = 'company,created_at,created_by,deleted_at,description,end_date,id,is_deleted,resume_id,sort,start_date,title,updated_at,updated_by,version',
       CONCAT('期望 15 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-028', 'resumes 字段齐全（19 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'resumes'), '')
       = 'accuracy,created_at,created_by,deleted_at,file_hash,file_id,file_name,file_size,file_type,id,is_deleted,parse_error,parsed_at,parsed_data,status,talent_id,updated_at,updated_by,version',
       CONCAT('期望 19 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-029', 'roles 字段齐全（7 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'roles'), '')
       = 'code,created_at,description,id,is_active,name,updated_at',
       CONCAT('期望 7 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-030', 'talent_portraits 字段齐全（18 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'talent_portraits'), '')
       = 'created_at,created_by,deleted_at,dimensions,generated_at,id,is_deleted,labels,model_name,potential_level,prompt_version,strengths,suggested_positions,talent_id,updated_at,updated_by,version,weaknesses',
       CONCAT('期望 18 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-031', 'talents 字段齐全（29 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'talents'), '')
       = 'birth_date,certificates,created_at,created_by,current_company,current_position,deleted_at,education,email,gender,id,id_card,is_deleted,major,mobile,name,organization_id,portrait_summary,potential_level,resume_id,school,self_evaluation,skill_tags,source,status,updated_at,updated_by,version,work_years',
       CONCAT('期望 29 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-032', 'training_plans 字段齐全（16 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'training_plans'), '')
       = 'created_at,created_by,created_by_ai,deleted_at,end_date,goal,id,is_deleted,name,source_assessment_id,start_date,status,talent_id,updated_at,updated_by,version',
       CONCAT('期望 16 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-033', 'user_roles 字段齐全（3 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'user_roles'), '')
       = 'created_at,role_id,user_id',
       CONCAT('期望 3 个字段')
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-034', 'users 字段齐全（22 个）',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY column_name) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'users'), '')
       = 'avatar,created_at,created_by,deleted_at,email,fail_count,id,is_active,is_deleted,is_superuser,last_login_at,last_login_ip,locked_until,mobile,organization_id,password_hash,position_title,real_name,updated_at,updated_by,username,version',
       CONCAT('期望 22 个字段')
FROM DUAL;

-- ---------------------------------------------------------------- 主键 / 唯一键
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-100', 'assessment_answers 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessment_answers' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-101', 'assessment_answers 唯一约束 uq_assessment_answers_assessment_question',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessment_answers' AND index_name = 'uq_assessment_answers_assessment_question' AND non_unique = 0), '')
       = 'assessment_id,question_id', '期望唯一键 (assessment_id,question_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-102', 'assessment_questions 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessment_questions' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-103', 'assessment_results 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessment_results' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-104', 'assessment_results 唯一索引 ix_assessment_results_assessment_id',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessment_results' AND index_name = 'ix_assessment_results_assessment_id' AND non_unique = 0), '')
       = 'assessment_id', '期望唯一键 (assessment_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-105', 'assessments 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'assessments' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-106', 'audit_logs 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'audit_logs' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-107', 'chat_messages 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'chat_messages' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-108', 'conversations 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'conversations' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-109', 'courses 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'courses' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-110', 'files 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'files' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-111', 'knowledge_chunks 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-112', 'knowledge_chunks 唯一约束 uq_knowledge_chunks_doc_index',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks' AND index_name = 'uq_knowledge_chunks_doc_index' AND non_unique = 0), '')
       = 'doc_id,chunk_index', '期望唯一键 (doc_id,chunk_index)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-113', 'knowledge_docs 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'knowledge_docs' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-114', 'learning_records 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'learning_records' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-115', 'learning_records 唯一约束 uq_learning_records_plan_course_talent',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'learning_records' AND index_name = 'uq_learning_records_plan_course_talent' AND non_unique = 0), '')
       = 'plan_id,course_id,talent_id', '期望唯一键 (plan_id,course_id,talent_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-116', 'matching_records 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'matching_records' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-117', 'matching_records 唯一约束 uq_matching_records_batch_position_talent',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'matching_records' AND index_name = 'uq_matching_records_batch_position_talent' AND non_unique = 0), '')
       = 'batch_id,position_id,talent_id', '期望唯一键 (batch_id,position_id,talent_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-118', 'organizations 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'organizations' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-119', 'organizations 唯一索引 ix_organizations_code',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'organizations' AND index_name = 'ix_organizations_code' AND non_unique = 0), '')
       = 'code', '期望唯一键 (code)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-120', 'organizations 唯一约束 uq_organizations_parent_name',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'organizations' AND index_name = 'uq_organizations_parent_name' AND non_unique = 0), '')
       = 'parent_id,name', '期望唯一键 (parent_id,name)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-121', 'plan_courses 主键 (plan_id, course_id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'plan_courses' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'plan_id,course_id', '期望唯一键 (plan_id,course_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-122', 'plan_courses 唯一约束 uq_plan_courses_plan_course',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'plan_courses' AND index_name = 'uq_plan_courses_plan_course' AND non_unique = 0), '')
       = 'plan_id,course_id', '期望唯一键 (plan_id,course_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-123', 'position_requirements 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'position_requirements' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-124', 'position_requirements 唯一约束 uq_position_requirements_position_version',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'position_requirements' AND index_name = 'uq_position_requirements_position_version' AND non_unique = 0), '')
       = 'position_id,version', '期望唯一键 (position_id,version)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-125', 'positions 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'positions' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-126', 'positions 唯一索引 ix_positions_code',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'positions' AND index_name = 'ix_positions_code' AND non_unique = 0), '')
       = 'code', '期望唯一键 (code)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-127', 'resume_experiences 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'resume_experiences' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-128', 'resumes 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'resumes' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-129', 'resumes 唯一索引 ix_resumes_file_hash',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'resumes' AND index_name = 'ix_resumes_file_hash' AND non_unique = 0), '')
       = 'file_hash', '期望唯一键 (file_hash)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-130', 'roles 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'roles' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-131', 'roles 唯一索引 ix_roles_code',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'roles' AND index_name = 'ix_roles_code' AND non_unique = 0), '')
       = 'code', '期望唯一键 (code)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-132', 'talent_portraits 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'talent_portraits' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-133', 'talent_portraits 唯一约束 uq_talent_portraits_talent_version',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'talent_portraits' AND index_name = 'uq_talent_portraits_talent_version' AND non_unique = 0), '')
       = 'talent_id,version', '期望唯一键 (talent_id,version)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-134', 'talents 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'talents' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-135', 'talents 唯一约束 uq_talents_mobile_is_deleted',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'talents' AND index_name = 'uq_talents_mobile_is_deleted' AND non_unique = 0), '')
       = 'mobile,is_deleted', '期望唯一键 (mobile,is_deleted)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-136', 'training_plans 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'training_plans' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-137', 'user_roles 主键 (user_id, role_id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'user_roles' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'user_id,role_id', '期望唯一键 (user_id,role_id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-138', 'users 主键 (id)',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = 'PRIMARY' AND non_unique = 0), '')
       = 'id', '期望唯一键 (id)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-139', 'users 唯一索引 ix_users_email',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = 'ix_users_email' AND non_unique = 0), '')
       = 'email', '期望唯一键 (email)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-140', 'users 唯一索引 ix_users_mobile',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = 'ix_users_mobile' AND non_unique = 0), '')
       = 'mobile', '期望唯一键 (mobile)'
FROM DUAL;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-141', 'users 唯一索引 ix_users_username',
       COALESCE((SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'users' AND index_name = 'ix_users_username' AND non_unique = 0), '')
       = 'username', '期望唯一键 (username)'
FROM DUAL;

-- ---------------------------------------------------------------- 外键与删除规则
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-200', 'assessment_answers.assessment_id -> assessments.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessment_answers' AND k.column_name = 'assessment_id'
  AND k.referenced_table_name = 'assessments' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-201', 'assessment_answers.question_id -> assessment_questions.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessment_answers' AND k.column_name = 'question_id'
  AND k.referenced_table_name = 'assessment_questions' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-202', 'assessment_questions.assessment_id -> assessments.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessment_questions' AND k.column_name = 'assessment_id'
  AND k.referenced_table_name = 'assessments' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-203', 'assessment_results.assessment_id -> assessments.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessment_results' AND k.column_name = 'assessment_id'
  AND k.referenced_table_name = 'assessments' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-204', 'assessments.position_id -> positions.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessments' AND k.column_name = 'position_id'
  AND k.referenced_table_name = 'positions' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-205', 'assessments.talent_id -> talents.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'assessments' AND k.column_name = 'talent_id'
  AND k.referenced_table_name = 'talents' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-206', 'audit_logs.user_id -> users.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'audit_logs' AND k.column_name = 'user_id'
  AND k.referenced_table_name = 'users' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-207', 'chat_messages.conversation_id -> conversations.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'chat_messages' AND k.column_name = 'conversation_id'
  AND k.referenced_table_name = 'conversations' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-208', 'conversations.user_id -> users.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'conversations' AND k.column_name = 'user_id'
  AND k.referenced_table_name = 'users' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-209', 'files.uploader_id -> users.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'files' AND k.column_name = 'uploader_id'
  AND k.referenced_table_name = 'users' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-210', 'knowledge_chunks.doc_id -> knowledge_docs.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'knowledge_chunks' AND k.column_name = 'doc_id'
  AND k.referenced_table_name = 'knowledge_docs' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-211', 'knowledge_docs.file_id -> files.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'knowledge_docs' AND k.column_name = 'file_id'
  AND k.referenced_table_name = 'files' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-212', 'learning_records.course_id -> courses.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'learning_records' AND k.column_name = 'course_id'
  AND k.referenced_table_name = 'courses' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-213', 'learning_records.plan_id -> training_plans.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'learning_records' AND k.column_name = 'plan_id'
  AND k.referenced_table_name = 'training_plans' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-214', 'learning_records.talent_id -> talents.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'learning_records' AND k.column_name = 'talent_id'
  AND k.referenced_table_name = 'talents' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-215', 'organizations.parent_id -> organizations.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'organizations' AND k.column_name = 'parent_id'
  AND k.referenced_table_name = 'organizations' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-216', 'plan_courses.course_id -> courses.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'plan_courses' AND k.column_name = 'course_id'
  AND k.referenced_table_name = 'courses' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-217', 'plan_courses.plan_id -> training_plans.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'plan_courses' AND k.column_name = 'plan_id'
  AND k.referenced_table_name = 'training_plans' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-218', 'position_requirements.position_id -> positions.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'position_requirements' AND k.column_name = 'position_id'
  AND k.referenced_table_name = 'positions' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-219', 'positions.organization_id -> organizations.id ON DELETE RESTRICT',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'positions' AND k.column_name = 'organization_id'
  AND k.referenced_table_name = 'organizations' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'RESTRICT';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-220', 'resume_experiences.resume_id -> resumes.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'resume_experiences' AND k.column_name = 'resume_id'
  AND k.referenced_table_name = 'resumes' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-221', 'resumes.file_id -> files.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'resumes' AND k.column_name = 'file_id'
  AND k.referenced_table_name = 'files' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-222', 'resumes.talent_id -> talents.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'resumes' AND k.column_name = 'talent_id'
  AND k.referenced_table_name = 'talents' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-223', 'talent_portraits.talent_id -> talents.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'talent_portraits' AND k.column_name = 'talent_id'
  AND k.referenced_table_name = 'talents' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-224', 'talents.organization_id -> organizations.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'talents' AND k.column_name = 'organization_id'
  AND k.referenced_table_name = 'organizations' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-225', 'training_plans.talent_id -> talents.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'training_plans' AND k.column_name = 'talent_id'
  AND k.referenced_table_name = 'talents' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-226', 'user_roles.role_id -> roles.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'user_roles' AND k.column_name = 'role_id'
  AND k.referenced_table_name = 'roles' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-227', 'user_roles.user_id -> users.id ON DELETE CASCADE',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'user_roles' AND k.column_name = 'user_id'
  AND k.referenced_table_name = 'users' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'CASCADE';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-228', 'users.organization_id -> organizations.id ON DELETE SET NULL',
       COUNT(*) = 1, CONCAT('匹配外键数=', COUNT(*))
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = rc.constraint_schema AND k.constraint_name = rc.constraint_name AND k.table_name = rc.table_name
WHERE rc.constraint_schema = DATABASE() AND rc.table_name = 'users' AND k.column_name = 'organization_id'
  AND k.referenced_table_name = 'organizations' AND k.referenced_column_name = 'id' AND rc.delete_rule = 'SET NULL';

-- ---------------------------------------------------------------- 关键默认值 / 类型
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-300', 'users.is_active 默认值 = 1',
       REPLACE(COALESCE(column_default, ''), '''', '') = '1', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'is_active';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-301', 'users.is_superuser 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'is_superuser';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-302', 'users.fail_count 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'fail_count';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-303', 'talents.gender 默认值 = unknown',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'unknown', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'talents' AND column_name = 'gender';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-304', 'talents.work_years 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'talents' AND column_name = 'work_years';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-305', 'talents.status 默认值 = active',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'active', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'talents' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-306', 'talents.source 默认值 = manual',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'manual', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'talents' AND column_name = 'source';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-307', 'positions.level 默认值 = middle',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'middle', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'positions' AND column_name = 'level';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-308', 'positions.headcount 默认值 = 1',
       REPLACE(COALESCE(column_default, ''), '''', '') = '1', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'positions' AND column_name = 'headcount';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-309', 'positions.status 默认值 = open',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'open', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'positions' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-310', 'resumes.status 默认值 = uploaded',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'uploaded', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'resumes' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-311', 'resumes.file_size 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'resumes' AND column_name = 'file_size';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-312', 'matching_records.score 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'matching_records' AND column_name = 'score';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-313', 'assessments.status 默认值 = pending',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'pending', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'assessments' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-314', 'assessment_questions.type 默认值 = single',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'single', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'assessment_questions' AND column_name = 'type';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-315', 'training_plans.status 默认值 = draft',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'draft', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'training_plans' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-316', 'learning_records.progress 默认值 = 0',
       REPLACE(COALESCE(column_default, ''), '''', '') = '0', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'learning_records' AND column_name = 'progress';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-317', 'learning_records.status 默认值 = not_started',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'not_started', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'learning_records' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-318', 'knowledge_docs.source_type 默认值 = text',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'text', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'knowledge_docs' AND column_name = 'source_type';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-319', 'knowledge_docs.status 默认值 = indexing',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'indexing', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'knowledge_docs' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-320', 'knowledge_docs.permission_scope 默认值 = public',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'public', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'knowledge_docs' AND column_name = 'permission_scope';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-321', 'conversations.agent_type 默认值 = assistant',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'assistant', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'conversations' AND column_name = 'agent_type';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-322', 'conversations.status 默认值 = active',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'active', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'conversations' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-323', 'chat_messages.role 默认值 = user',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'user', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'chat_messages' AND column_name = 'role';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-324', 'files.storage_type 默认值 = local',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'local', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'files' AND column_name = 'storage_type';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-325', 'audit_logs.status 默认值 = success',
       REPLACE(COALESCE(column_default, ''), '''', '') = 'success', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'audit_logs' AND column_name = 'status';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-326', 'organizations.level 默认值 = 1',
       REPLACE(COALESCE(column_default, ''), '''', '') = '1', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'organizations' AND column_name = 'level';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-327', 'organizations.path 默认值 = /',
       REPLACE(COALESCE(column_default, ''), '''', '') = '/', CONCAT('实际=', COALESCE(column_default, 'NULL'))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'organizations' AND column_name = 'path';

INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-350', 'users.password_hash 类型 = varchar(128)', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'password_hash' AND data_type = 'varchar' AND character_maximum_length = 128;
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-351', 'talents.skill_tags 类型 = json', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'talents' AND column_name = 'skill_tags' AND data_type = 'json';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-352', 'matching_records.score 类型 = decimal', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'matching_records' AND column_name = 'score' AND data_type = 'decimal';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-353', 'resumes.accuracy 类型 = decimal', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'resumes' AND column_name = 'accuracy' AND data_type = 'decimal';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-354', 'positions.salary_min 类型 = decimal', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'positions' AND column_name = 'salary_min' AND data_type = 'decimal';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-355', 'audit_logs.before_data 类型 = json', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'audit_logs' AND column_name = 'before_data' AND data_type = 'json';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-356', 'chat_messages.citations 类型 = json', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'chat_messages' AND column_name = 'citations' AND data_type = 'json';
INSERT INTO sqltest_results (test_id, test_name, passed, detail)
SELECT 'SCH-357', 'knowledge_chunks.milvus_pk 类型 = bigint', COUNT(*) = 1, CONCAT('实际=', MAX(data_type))
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks' AND column_name = 'milvus_pk' AND data_type = 'bigint';
