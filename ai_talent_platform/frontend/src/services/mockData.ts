import type { Assessment, KnowledgeDoc, LoginUser, Matching, OrgNode, Position, Resume, Talent, User } from '../types';

export const demoUser: LoginUser = {
  id: 1, username: 'admin', real_name: '林致远', organization_name: '集团人力资源中心', position_title: '人才发展总监',
  is_superuser: true, roles: ['super_admin', 'admin'], permissions: ['*'],
};

export const demoTalents: Talent[] = [
  { id: 12, name: '张明远', gender: 'male', mobile: '138****0001', email: 'zhangmy@example.com', education: '硕士', school: '浙江大学', major: '计算机科学与技术', work_years: 8, current_company: '智远科技', current_position: '高级算法工程师', organization_id: 3, organization_name: '技术研发部', skill_tags: ['Python', '机器学习', 'RAG', 'LangChain'], status: 'active', potential_level: 'A', match_score: 96, portrait_summary: '具备扎实的算法工程能力与大模型应用落地经验，主导过多个从 0 到 1 的 AI 项目，具备较强的技术影响力。', created_at: '2026-10-18T09:30:00' },
  { id: 18, name: '陈思琪', gender: 'female', mobile: '139****2618', email: 'chensq@example.com', education: '硕士', school: '复旦大学', major: '人力资源管理', work_years: 6, current_company: '云启集团', current_position: '人才发展专家', organization_id: 2, organization_name: '人力资源部', skill_tags: ['人才盘点', '组织发展', '领导力', '胜任力模型'], status: 'active', potential_level: 'A', match_score: 93, portrait_summary: '兼具业务视角与专业深度，在干部管理与组织人才盘点方面经验丰富。', created_at: '2026-10-12T10:20:00' },
  { id: 23, name: '王皓宇', gender: 'male', mobile: '136****8203', education: '本科', school: '华中科技大学', major: '软件工程', work_years: 5, current_company: '数联信息', current_position: '后端技术负责人', organization_id: 3, organization_name: '技术研发部', skill_tags: ['Java', '微服务', 'MySQL', '架构设计'], status: 'active', potential_level: 'A', match_score: 91, portrait_summary: '分布式系统架构经验突出，擅长复杂业务抽象，团队管理与跨部门协作能力良好。', created_at: '2026-10-08T08:00:00' },
  { id: 31, name: '李嘉宁', gender: 'female', mobile: '137****0916', education: '本科', school: '中山大学', major: '市场营销', work_years: 7, current_company: '新锐零售', current_position: '高级市场经理', organization_id: 4, organization_name: '市场品牌部', skill_tags: ['品牌战略', '增长营销', '数据分析', '团队管理'], status: 'active', potential_level: 'B', match_score: 88, portrait_summary: '具有全渠道营销和品牌增长实战经验，数据敏感度高。', created_at: '2026-09-25T09:00:00' },
  { id: 42, name: '赵子昂', gender: 'male', mobile: '135****7702', education: '博士', school: '上海交通大学', major: '人工智能', work_years: 4, current_company: '前沿智能实验室', current_position: 'NLP 算法专家', organization_id: 3, organization_name: '技术研发部', skill_tags: ['NLP', 'PyTorch', '大模型微调', '知识图谱'], status: 'active', potential_level: 'A', match_score: 87, portrait_summary: 'NLP 研究与工程落地能力兼备，有多篇高水平论文及专利成果。', created_at: '2026-09-20T08:00:00' },
  { id: 51, name: '周雨桐', gender: 'female', mobile: '188****3239', education: '硕士', school: '同济大学', major: '数据科学', work_years: 3, current_company: '慧数科技', current_position: '数据分析师', organization_id: 5, organization_name: '数据智能部', skill_tags: ['SQL', 'Python', 'Tableau', '商业分析'], status: 'active', potential_level: 'B', match_score: 85, portrait_summary: '业务分析思路清晰，擅长将复杂数据转化为经营洞察。', created_at: '2026-09-16T08:00:00' },
  { id: 66, name: '刘一辰', gender: 'male', mobile: '186****4452', education: '本科', school: '武汉大学', major: '信息安全', work_years: 6, current_company: '安盾网络', current_position: '安全工程师', organization_id: 3, organization_name: '技术研发部', skill_tags: ['安全合规', '渗透测试', '零信任', '等保'], status: 'reserve', potential_level: 'B', match_score: 82, portrait_summary: '企业安全体系建设经验丰富，熟悉政企安全合规要求。', created_at: '2026-09-06T08:00:00' },
];

export const demoPositions: Position[] = [
  { id: 3, title: '高级后端工程师', code: 'RD-BE-026', organization_id: 3, organization_name: '技术研发部', level: 'senior', headcount: 3, headcount_filled: 1, status: 'open', description: '负责人才平台核心服务设计与开发，参与高并发系统架构演进。', skill_tags: ['Python', 'FastAPI', 'MySQL', '微服务'], salary_min: 25000, salary_max: 40000, requirements: { education: '本科', experience_years: 5, skills: ['Python', 'FastAPI', 'MySQL'] }, publish_at: '2026-10-28T09:00:00' },
  { id: 4, title: 'AI 算法专家', code: 'RD-AI-018', organization_id: 3, organization_name: '技术研发部', level: 'expert', headcount: 2, headcount_filled: 0, status: 'open', description: '负责大模型、RAG 与智能体核心技术研发及落地。', skill_tags: ['NLP', 'RAG', 'LangChain', 'PyTorch'], salary_min: 45000, salary_max: 65000, requirements: { education: '硕士', experience_years: 5, skills: ['NLP', 'PyTorch', 'RAG'] }, publish_at: '2026-10-26T09:00:00' },
  { id: 8, title: '组织发展经理', code: 'HR-OD-009', organization_id: 2, organization_name: '人力资源部', level: 'senior', headcount: 1, headcount_filled: 0, status: 'open', description: '牵头组织诊断、人才盘点及关键岗位继任计划。', skill_tags: ['组织诊断', '人才盘点', '干部管理'], salary_min: 28000, salary_max: 38000, requirements: { education: '本科', experience_years: 6, skills: ['组织发展', '人才盘点'] }, publish_at: '2026-10-20T09:00:00' },
  { id: 11, title: '数据产品经理', code: 'DP-PM-012', organization_id: 5, organization_name: '数据智能部', level: 'middle', headcount: 2, headcount_filled: 1, status: 'paused', description: '负责企业数据产品规划与 AI 数据应用设计。', skill_tags: ['产品规划', '数据分析', 'AI 应用'], salary_min: 22000, salary_max: 32000, requirements: { education: '本科', experience_years: 3, skills: ['产品规划', 'SQL'] }, publish_at: '2026-10-16T09:00:00' },
  { id: 15, title: '品牌增长负责人', code: 'MK-BG-006', organization_id: 4, organization_name: '市场品牌部', level: 'expert', headcount: 1, headcount_filled: 0, status: 'open', description: '制定品牌增长策略，建设全域品牌营销体系。', skill_tags: ['品牌战略', '增长营销', '团队管理'], salary_min: 35000, salary_max: 50000, requirements: { education: '本科', experience_years: 8, skills: ['品牌战略', '增长营销'] }, publish_at: '2026-10-10T09:00:00' },
];

export const demoResumes: Resume[] = [
  { id: 81, file_name: '张明远-高级算法工程师.pdf', file_size: 842660, file_type: 'pdf', status: 'success', accuracy: .98, talent_id: 12, talent_name: '张明远', created_at: '2026-11-04T09:26:00', parsed_at: '2026-11-04T09:26:18' },
  { id: 80, file_name: '陈思琪-人才发展专家.docx', file_size: 328921, file_type: 'docx', status: 'success', accuracy: .96, talent_id: 18, talent_name: '陈思琪', created_at: '2026-11-04T09:18:00', parsed_at: '2026-11-04T09:18:22' },
  { id: 79, file_name: '王皓宇-技术负责人.pdf', file_size: 1250920, file_type: 'pdf', status: 'parsing', created_at: '2026-11-04T09:06:00' },
  { id: 78, file_name: '候选人简历-扫描件.jpg', file_size: 2389920, file_type: 'jpg', status: 'uploaded', created_at: '2026-11-04T08:52:00' },
  { id: 77, file_name: '周雨桐-数据分析.pdf', file_size: 628200, file_type: 'pdf', status: 'success', accuracy: .94, talent_id: 51, talent_name: '周雨桐', created_at: '2026-11-03T17:35:00', parsed_at: '2026-11-03T17:35:15' },
  { id: 76, file_name: '未知候选人-简历.doc', file_size: 229120, file_type: 'doc', status: 'failed', created_at: '2026-11-03T16:20:00' },
];

export const demoAssessments: Assessment[] = [
  { id: 5, title: '技术序列高级人才能力测评', talent_id: 12, talent_name: '张明远', position_title: 'AI 算法专家', question_count: 20, status: 'scored', deadline: '2026-11-10 18:00', total_score: 92.5, level: '优秀' },
  { id: 8, title: '管理者领导力综合测评', talent_id: 18, talent_name: '陈思琪', position_title: '组织发展经理', question_count: 30, status: 'in_progress', deadline: '2026-11-08 18:00' },
  { id: 11, title: '后端工程师专业能力测评', talent_id: 23, talent_name: '王皓宇', position_title: '高级后端工程师', question_count: 25, status: 'scored', deadline: '2026-11-05 18:00', total_score: 86, level: '良好' },
  { id: 14, title: '高潜人才综合素质测评', talent_id: 42, talent_name: '赵子昂', position_title: 'AI 算法专家', question_count: 20, status: 'pending', deadline: '2026-11-12 18:00' },
  { id: 17, title: '数据人才专业技能测评', talent_id: 51, talent_name: '周雨桐', position_title: '数据产品经理', question_count: 20, status: 'submitted', deadline: '2026-11-03 18:00' },
];

export const demoMatches: Matching[] = demoTalents.slice(0, 5).map((t, i) => ({
  talent_id: t.id, talent_name: t.name, current_position: t.current_position, organization_name: t.organization_name,
  score: [94.6, 91.2, 88.5, 85.8, 82.4][i],
  dimension_scores: { skill: [96, 91, 92, 85, 82][i], experience: [94, 93, 85, 88, 80][i], education: [95, 90, 85, 85, 90][i], quality: [92, 89, 88, 86, 80][i] },
  highlights: [`${t.work_years} 年相关经验，核心技术栈高度匹配`, `具备${t.skill_tags.slice(0, 2).join('、')}项目实战经验`],
  gaps: [i === 0 ? '大型团队管理经验可进一步考察' : '部分行业业务场景经验有待补充'],
  suggestion: '建议进入下一轮专业面试，重点考察复杂项目架构设计与跨团队协作能力。', skill_tags: t.skill_tags,
}));

export const demoKnowledge: KnowledgeDoc[] = [
  { id: 3, title: '职级晋升管理办法（2026 版）', category: '人才制度', source_type: 'file', status: 'indexed', chunk_count: 86, permission_scope: 'public', indexed_at: '2026-11-01 10:20', file_size: '2.4 MB' },
  { id: 6, title: '人才测评管理制度', category: '测评管理', source_type: 'file', status: 'indexed', chunk_count: 54, permission_scope: 'role', indexed_at: '2026-10-30 14:12', file_size: '1.8 MB' },
  { id: 9, title: '关键岗位继任者培养方案', category: '人才发展', source_type: 'file', status: 'indexed', chunk_count: 112, permission_scope: 'dept', indexed_at: '2026-10-28 09:08', file_size: '3.1 MB' },
  { id: 12, title: '员工培训与学分管理规范', category: '培训管理', source_type: 'file', status: 'indexed', chunk_count: 72, permission_scope: 'public', indexed_at: '2026-10-26 16:40', file_size: '1.2 MB' },
  { id: 15, title: '集团校园招聘操作指引', category: '招聘配置', source_type: 'file', status: 'indexing', chunk_count: 0, permission_scope: 'role', file_size: '4.6 MB' },
];

export const demoOrgs: OrgNode[] = [
  { id: 1, code: 'HQ', name: '云启科技集团', sort: 1, is_active: true, user_count: 1286, children: [
    { id: 2, parent_id: 1, code: 'HR', name: '人力资源部', sort: 1, is_active: true, user_count: 48, children: [{ id: 21, parent_id: 2, code: 'HR_TD', name: '人才发展组', sort: 1, is_active: true, user_count: 12, children: [] }] },
    { id: 3, parent_id: 1, code: 'RD', name: '技术研发部', sort: 2, is_active: true, user_count: 426, children: [{ id: 31, parent_id: 3, code: 'RD_AI', name: 'AI 平台组', sort: 1, is_active: true, user_count: 86, children: [] }, { id: 32, parent_id: 3, code: 'RD_BE', name: '后端研发组', sort: 2, is_active: true, user_count: 124, children: [] }] },
    { id: 4, parent_id: 1, code: 'MK', name: '市场品牌部', sort: 3, is_active: true, user_count: 128, children: [] },
    { id: 5, parent_id: 1, code: 'DI', name: '数据智能部', sort: 4, is_active: true, user_count: 96, children: [] },
    { id: 6, parent_id: 1, code: 'FIN', name: '财务管理部', sort: 5, is_active: true, user_count: 62, children: [] },
  ] },
];

export const demoUsers: User[] = demoTalents.map((t, i) => ({ id: i + 2, username: ['zhangmy', 'chensq', 'wanghy', 'lijn', 'zhaoza', 'zhouyt', 'liuyc'][i], real_name: t.name, mobile: t.mobile, email: t.email, organization_id: t.organization_id, organization_name: t.organization_name, position_title: t.current_position, is_active: i !== 6, is_superuser: false, roles: [{ id: 3, code: i === 1 ? 'hr' : 'manager', name: i === 1 ? '人力资源' : '部门管理者' }], last_login_at: '2026-11-04T08:30:00', created_at: t.created_at }));

export const courses = [
  { id: 31, title: '大模型应用与 RAG 实战', category: 'AI 技术', level: '进阶', duration: 12, learners: 286, progress: 78, color: '#4e6ef2', icon: 'robot', description: '从检索增强到智能体，掌握企业级 AI 应用开发方法' },
  { id: 32, title: '卓越管理者领导力跃迁', category: '领导力', level: '高级', duration: 16, learners: 128, progress: 64, color: '#8b5cf6', icon: 'leader', description: '面向新任与后备管理者的系统领导力培养项目' },
  { id: 33, title: '数据驱动的人才决策', category: '人力资源', level: '进阶', duration: 8, learners: 356, progress: 86, color: '#13a8a8', icon: 'chart', description: '构建人才数据分析思维，让组织决策更科学' },
  { id: 34, title: '分布式系统架构设计', category: '技术专业', level: '高级', duration: 20, learners: 192, progress: 52, color: '#f59e0b', icon: 'code', description: '深入高并发、高可用系统的核心架构与工程实践' },
];

export const monthlyTrend = {
  months: ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'],
  total: [1080,1102,1126,1148,1170,1194,1208,1226,1241,1258,1272,1286],
  joined: [22,35,41,36,39,42,31,36,32,38,29,34], left: [10,13,17,14,17,18,17,18,17,21,15,20],
};

export const auditLogs = [
  { id: 10241, user: '林致远', action: 'AI 人岗匹配', module: '人岗匹配', detail: '发起「高级后端工程师」人才匹配任务', ip: '10.24.16.82', status: 'success', time: '2026-11-04 10:26:32', trace: 'b7f3c1d2e8a94f60' },
  { id: 10240, user: '陈思琪', action: '更新档案', module: '人才档案', detail: '更新人才「王皓宇」的技能标签', ip: '10.24.18.36', status: 'success', time: '2026-11-04 10:22:18', trace: '3f7a9c1d2e4b5067' },
  { id: 10239, user: '系统任务', action: '简历解析', module: 'AI 智能体', detail: '完成简历「张明远-高级算法工程师.pdf」解析', ip: '127.0.0.1', status: 'success', time: '2026-11-04 10:18:46', trace: '75d180fa97bd4d03' },
  { id: 10238, user: '周雨桐', action: '导出报表', module: '数据分析', detail: '导出 2026 年 10 月人才结构分析报告', ip: '10.24.20.18', status: 'success', time: '2026-11-04 10:08:05', trace: 'a1b2c3d4e5f60718' },
  { id: 10237, user: '赵子昂', action: '知识问答', module: '知识库', detail: '检索「高级工程师晋升条件」', ip: '10.24.16.96', status: 'failed', time: '2026-11-04 09:58:27', trace: '9dd1c3a2b7f04e11' },
];
