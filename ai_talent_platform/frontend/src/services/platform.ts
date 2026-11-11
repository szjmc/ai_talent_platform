import { downloadWithToken, request } from './api';
import type {
  Assessment, AssessmentReport, AssessmentSummary, AgentConversation, AuditEntry, Course,
  DistributionResult, KnowledgeDoc, MatchingRunResult, NL2SQLResult, OrgNode, PageResult,
  Position, ReportItem, Resume, ResumeParseResult, Talent, TalentPortrait, TrainingNotification,
  TrainingPlan, TrainingRecommendation, TrendResult, User,
  AnalyticsAgentResponse, PeriodReportResponse,
  AiInterviewConfig, AiInterviewSession, AiInterviewDetail, AiInterviewReport, AiInterviewPublicState, AiInterviewAnswerResult,
} from '../types';

/** 与后端 OpenAPI 对齐的业务接口封装；不在前端构造任何模拟业务数据。 */
export const usersApi = {
  list: (params?: Record<string, unknown>) => request<PageResult<User>>({ url: '/users', params }),
  create: (data: Record<string, unknown>) => request<User>({ url: '/users', method: 'POST', data }),
  update: (id: number, data: Record<string, unknown>) => request<User>({ url: `/users/${id}`, method: 'PUT', data }),
  assignRoles: (id: number, role_codes: string[]) => request<User>({ url: `/users/${id}/roles`, method: 'PUT', data: { role_codes } }),
  status: (id: number, is_active: boolean, reason?: string) => request<User>({ url: `/users/${id}/status`, method: 'PATCH', data: { is_active, reason } }),
  resetPassword: (id: number, new_password: string) => request<unknown>({ url: `/users/${id}/reset-password`, method: 'POST', data: { new_password } }),
  remove: (id: number) => request<unknown>({ url: `/users/${id}`, method: 'DELETE' }),
};

export const organizationsApi = {
  tree: () => request<OrgNode[]>({ url: '/organizations/tree' }),
  list: (params?: Record<string, unknown>) => request<PageResult<OrgNode>>({ url: '/organizations', params }),
  create: (data: Record<string, unknown>) => request<OrgNode>({ url: '/organizations', method: 'POST', data }),
  update: (id: number, data: Record<string, unknown>) => request<OrgNode>({ url: `/organizations/${id}`, method: 'PUT', data }),
  move: (id: number, new_parent_id: number | null) => request<OrgNode>({ url: `/organizations/${id}/move`, method: 'PATCH', data: { new_parent_id } }),
  remove: (id: number) => request<unknown>({ url: `/organizations/${id}`, method: 'DELETE' }),
};

export interface TalentListParams { page?: number; page_size?: number; keyword?: string; education?: string; organization_id?: number; status?: string; skill?: string; work_years_min?: number; work_years_max?: number }
export const talentsApi = {
  list: (params?: TalentListParams) => request<PageResult<Talent>>({ url: '/talents', params }),
  detail: (id: number) => request<Talent & { resumes?: { id: number; file_name: string; status: string }[] }>({ url: `/talents/${id}` }),
  create: (data: Record<string, unknown>) => request<Talent>({ url: '/talents', method: 'POST', data }),
  update: (id: number, data: Record<string, unknown>) => request<Talent>({ url: `/talents/${id}`, method: 'PUT', data }),
  remove: (id: number) => request<unknown>({ url: `/talents/${id}`, method: 'DELETE' }),
  portrait: (id: number) => request<TalentPortrait>({ url: `/talents/${id}/portrait` }),
  refreshPortrait: (id: number) => request<unknown>({ url: `/talents/${id}/portrait/refresh`, method: 'POST' }),
  semanticSearch: (data: { query: string; top_k?: number; filters?: Record<string, unknown> }) => request<{ items: unknown[] }>({ url: '/talents/search/semantic', method: 'POST', data }),
};

export const resumesApi = {
  list: (params?: Record<string, unknown>) => request<PageResult<Resume>>({ url: '/resumes', params }),
  upload: (file: File) => {
    const form = new FormData(); form.append('file', file);
    return request<Resume & { duplicate: boolean }>({ url: '/resumes/upload', method: 'POST', data: form, headers: { 'Content-Type': 'multipart/form-data' } });
  },
  parse: (id: number, force = false) => request<{ resume_id: number; status: string }>({ url: `/resumes/${id}/parse`, method: 'POST', data: { force } }),
  result: (id: number) => request<ResumeParseResult>({ url: `/resumes/${id}/result` }),
  bind: (id: number, talent_id: number) => request<Resume>({ url: `/resumes/${id}/bind`, method: 'POST', data: { talent_id } }),
};

export const assessmentsApi = {
  list: (params?: Record<string, unknown>) => request<PageResult<Assessment>>({ url: '/assessments', params }),
  create: (data: { file_id: number; question_count?: number; title?: string }) => request<{ id: number; talent_id: number; question_count: number; exam_url: string; email?: Record<string, unknown> }>({ url: '/assessments', method: 'POST', data }),
  detail: (id: number) => request<Assessment & { resume_keywords?: Record<string, unknown>; exam_url?: string }>({ url: `/assessments/${id}` }),
  report: (id: number) => request<AssessmentReport>({ url: `/assessments/${id}/report` }),
  paper: (id: number) => request<{ assessment_id: number; questions: unknown[] }>({ url: `/assessments/${id}/paper` }),
  start: (id: number) => request<{ assessment_id: number; questions: unknown[] }>({ url: `/assessments/${id}/start`, method: 'POST' }),
  submit: (id: number, answers: { question_id: number; answer: unknown }[]) => request<Record<string, unknown>>({ url: `/assessments/${id}/submit`, method: 'POST', data: { answers } }),
  interviewRecords: (id: number) => request<unknown[]>({ url: `/assessments/${id}/interview/records` }),
};

export const matchingsApi = {
  run: (data: { position_id: number; strategy?: string; top_k?: number; min_score?: number; talent_ids?: number[] | null; with_explanation?: boolean }) =>
    request<MatchingRunResult>({ url: '/matchings/run', method: 'POST', data }),
  latestByPosition: (positionId: number) => request<MatchingRunResult>({ url: `/matchings/positions/${positionId}` }),
  batch: (batchId: string) => request<MatchingRunResult>({ url: `/matchings/batches/${batchId}` }),
  explain: (data: { position_id: number; talent_id: number; requirement_version?: number }) => request<Record<string, unknown>>({ url: '/matchings/explain', method: 'POST', data }),
};

export interface PositionListParams { page?: number; page_size?: number; keyword?: string; organization_id?: number; level?: string; status?: string }
export interface PositionPayload {
  title: string; code?: string; organization_id: number; level?: string; headcount?: number; status?: string;
  description?: string | null; skill_tags?: string[]; salary_min?: number | null; salary_max?: number | null;
  requirements?: Record<string, unknown> | null;
}
export const positionsApi = {
  /** 岗位 CRUD（后端 /positions 已由 501 占位切换为真实实现）。 */
  list: (params?: PositionListParams) => request<PageResult<Position>>({ url: '/positions', params }),
  detail: (id: number) => request<Position>({ url: `/positions/${id}` }),
  create: (data: PositionPayload) => request<Position>({ url: '/positions', method: 'POST', data }),
  update: (id: number, data: Partial<PositionPayload>) => request<Position>({ url: `/positions/${id}`, method: 'PUT', data }),
  setStatus: (id: number, status: string, reason?: string) =>
    request<Position>({ url: `/positions/${id}/status`, method: 'PATCH', data: { status, reason } }),
  remove: (id: number) => request<unknown>({ url: `/positions/${id}`, method: 'DELETE' }),
  /** 岗位画像（JD 解析 -> HR 确认 -> 生成版本）。 */
  profile: (id: number) => request<{ position_id: number; position_title: string; version: number; confirmed: boolean; profile: Record<string, unknown> }>({ url: `/positions/${id}/profile` }),
  parseJd: (id: number, data: { jd_text: string; job_title?: string }) => request<Record<string, unknown>>({ url: `/positions/${id}/jd/parse`, method: 'POST', data }),
  confirmProfile: (id: number, data: Record<string, unknown>) => request<unknown>({ url: `/positions/${id}/profile/confirm`, method: 'POST', data }),
};

export const trainingsApi = {
  courses: (params?: Record<string, unknown>) => request<PageResult<Course>>({ url: '/trainings/courses', params }),
  createCourse: (data: Record<string, unknown>) => request<Course>({ url: '/trainings/courses', method: 'POST', data }),
  updateCourse: (id: number, data: Record<string, unknown>) => request<Course>({ url: `/trainings/courses/${id}`, method: 'PUT', data }),
  removeCourse: (id: number) => request<unknown>({ url: `/trainings/courses/${id}`, method: 'DELETE' }),
  plans: (params?: Record<string, unknown>) => request<PageResult<TrainingPlan>>({ url: '/trainings/plans', params }),
  plan: (id: number) => request<Record<string, unknown>>({ url: `/trainings/plans/${id}` }),
  generatePlan: (data: Record<string, unknown>) => request<Record<string, unknown>>({ url: '/trainings/plans/generate', method: 'POST', data }),
  progress: (id: number) => request<Record<string, unknown>>({ url: `/trainings/plans/${id}/progress` }),
  recommend: (data: { talent_id: number; top_k?: number; gaps?: unknown[] }) => request<{ talent_id: number; summary: string; recommendations: TrainingRecommendation[] }>({ url: '/trainings/recommend', method: 'POST', data }),
  lagScan: () => request<{ scanned: number; alerts: unknown[]; reminded: number; warned: number }>({ url: '/trainings/lag-scan', method: 'POST' }),
  notifications: (params?: Record<string, unknown>) => request<{ total: number; unread: number; items: TrainingNotification[] }>({ url: '/trainings/notifications', params }),
  readNotification: (id: number) => request<unknown>({ url: `/trainings/notifications/${id}/read`, method: 'POST' }),
  readAllNotifications: () => request<unknown>({ url: '/trainings/notifications/read-all', method: 'POST' }),
  passRate: (params?: Record<string, unknown>) => request<Record<string, unknown>>({ url: '/trainings/stats/pass-rate', params }),
};

export const knowledgeApi = {
  docs: (params?: Record<string, unknown>) => request<PageResult<KnowledgeDoc>>({ url: '/knowledge/docs', params }),
  upload: (files: File[], category: string, title?: string, doc_id?: number) => {
    const form = new FormData(); files.forEach((file) => form.append('file', file)); form.append('category', category);
    if (title) form.append('title', title); if (doc_id) form.append('doc_id', String(doc_id));
    return request<Record<string, unknown>>({ url: '/files/upload', method: 'POST', data: form, headers: { 'Content-Type': 'multipart/form-data' } });
  },
  retry: (docId: number) => request<unknown>({ url: `/files/docs/${docId}/retry`, method: 'POST' }),
  /** 文档版本列表（草稿/历史/当前生效；含 file_id 与乐观锁 version）。 */
  revisions: (docId: number, params?: { offset?: number; limit?: number }) =>
    request<{ total: number; items: RevisionItem[] }>({ url: `/knowledge/docs/${docId}/revisions`, params }),
  /** 版本正文（version 用于编辑/发布时的并发校验）。 */
  revision: (revisionId: number) => request<RevisionItem & { body?: string; content?: string }>({ url: `/knowledge/revisions/${revisionId}` }),
  /** 生成切片预览：发布前必须先预览。
   *  返回的 version 是预览后**新的**乐观锁版本（发布时要用它，不能用列表里的旧 version），
   *  chunks 供人工确认切片是否符合预期。 */
  revisionPreview: (revisionId: number, expectedVersion: number) =>
    request<RevisionItem & { version: number; chunks: { content: string; metadata?: Record<string, unknown> }[]; chunk_count?: number }>({
      url: `/knowledge/revisions/${revisionId}/preview`, method: 'POST', data: { expected_version: expectedVersion },
    }),
  ask: (data: { question: string; session_id?: string | null; category?: string | null; top_k?: number; use_rerank?: boolean; suggest?: boolean }) =>
    request<{ answer: string; session_id: string; intent: string; answer_mode?: 'model' | 'extractive'; sources: { doc_id?: number; chunk_id?: number; file_id?: number; source?: string; page?: number | null; score?: number }[]; suggestions: string[] }>({ url: '/text2answer', method: 'POST', data }),
  conversations: (params?: Record<string, unknown>) => request<PageResult<{ session_id: string; title: string; message_count: number; last_message_at?: string | null }>>({ url: '/conversations', params }),
  messages: (sessionId: string | number) => request<{ id: number; role: string; content: string; citations?: unknown[]; created_at?: string | null }[]>({ url: `/conversations/${sessionId}/messages` }),
};

export interface AnalyticsFilterParams { organization_ids?: number[]; educations?: string[]; age_groups?: string[]; skills?: string[]; potential_levels?: string[]; month?: string; limit?: number }
export const analyticsApi = {
  distribution: (type: 'education'|'age'|'skill'|'org', params?: AnalyticsFilterParams) => request<DistributionResult>({ url: '/analytics/distribution', params: { type, ...params } }),
  trend: (params: { start_month: string; end_month: string; comparison?: 'none'|'mom'|'yoy'|'both' } & AnalyticsFilterParams) => request<TrendResult>({ url: '/analytics/trend', params }),
  assessmentSummary: (params?: AnalyticsFilterParams & { start_at?: string; end_at?: string; min_score?: number; max_score?: number }) => request<AssessmentSummary>({ url: '/analytics/assessment/summary', params }),
  nl2sql: (data: { question: string; max_rows?: number; with_insight?: boolean; chart_type?: string }) => request<NL2SQLResult>({ url: '/analytics/nl2sql', method: 'POST', data }),
  createReport: (data: { query_id?: string; question?: string; title?: string; format?: string }) => request<ReportItem>({ url: '/analytics/report', method: 'POST', data }),
  reports: (params?: Record<string, unknown>) => request<PageResult<ReportItem>>({ url: '/analytics/reports', params }),
  /** 报告下载：接口需要 Authorization，不能用 window.open 直接打开（会 401 提示登录）。 */
  downloadReport: (item: ReportItem) =>
    downloadWithToken(item.url || `/analytics/reports/${item.report_id}/download`, `${item.title || '分析报告'}.${(item.format || 'pdf').replace(/^\./, '')}`),
  talentGeo: () => request<{ talent_id: number; country: string; country_code?: string; province?: string; work_city?: string }[]>({ url: '/analytics/talents/geo' }),
};

export const agentsApi = {
  conversations: (params?: Record<string, unknown>) => request<PageResult<AgentConversation>>({ url: '/agents/conversations', params }),
  messages: (conversationId: string | number) => request<unknown[]>({ url: `/agents/conversations/${conversationId}/messages` }),
  deleteConversation: (conversationId: string | number) => request<{ deleted: boolean }>({ url: `/agents/conversations/${conversationId}`, method: 'DELETE' }),
  /** 单次分析报告：analytics Agent 完整推理，返回结构化 report（含 charts/risks/conclusions）。 */
  analyticsChat: (data: { message: string; conversation_id?: string | null; report_type?: string }) =>
    request<AnalyticsAgentResponse>({ url: '/agents/chat', method: 'POST', data: { agent_type: 'analytics', ...data } }),
  /** 周期报告（周报/月报/年报）：固定模板，可命中缓存；force=true 强制重算。 */
  periodReport: (report_type: 'weekly' | 'monthly' | 'yearly', force = false) =>
    request<PeriodReportResponse>({ url: '/agents/report/period', method: 'POST', data: { report_type, force } }),
  /** 评估分数汇总（用于刷新左栏 KPI 的测评均分）。 */
  assessmentSummary: () => request<AssessmentSummary>({ url: '/analytics/assessment/summary' }),
  /** 智能问答：自然语言问数 + 文字解读。 */
  nl2sqlAsk: (question: string) =>
    request<NL2SQLResult>({ url: '/analytics/nl2sql', method: 'POST', data: { question, with_insight: true } }),
};

/* ======================= 文件管理 [架构组] ======================= */
/*
   说明：/files 下是「文件」相关动作，不是一份独立的文件元数据库 ——
   上传/版本下载/索引维护都复用知识库已有的实现（详见后端 file_router.py）。
   因此这里不提供 list/stats/batch-upload：它们在后端并不存在，
   前端若照旧调用只会拿到 404（上一轮曾凭空造过这套接口，本轮已删）。
*/
export interface FileUploadResult { doc_id?: number; revision_id?: number; status?: string; [key: string]: unknown }
export interface FileBatchUploadResult {
  total: number; success_count: number; failed_count: number; category: string;
  items: { file_name?: string; status?: string; error?: string; [key: string]: unknown }[];
}
export interface RevisionItem {
  id?: number; revision_id?: number; doc_id?: number; version: number;
  status?: string; title?: string; category?: string; file_id?: number | null;
  source_revision_id?: number | null; created_at?: string | null; updated_at?: string | null;
}

/** 按文档读取编辑基线：正文来自「当前已发布且生效」的版本，不含 revision_id 选择负担。 */
export interface DocumentEditable {
  doc_id: number;
  revision_id: number;
  revision_no: number;
  title: string;
  category: string;
  status: string;
  published_at?: string | null;
  body: string;
  base_version: number;
}

export interface QuestionImportResult { imported_count: number; failed_count: number; pending_count: number; sync_error?: string | null; [key: string]: unknown }

export const filesApi = {
  /** 上传文件（同 knowledgeApi.upload，保留独立入口便于文件页调用）。 */
  upload: (files: File[], category: string, title?: string, docId?: number) => {
    const form = new FormData();
    files.forEach((file) => form.append('file', file));
    form.append('category', category);
    if (title) form.append('title', title);
    if (docId) form.append('doc_id', String(docId));
    return request<FileUploadResult | FileBatchUploadResult>({ url: '/files/upload', method: 'POST', data: form, headers: { 'Content-Type': 'multipart/form-data' } });
  },
  /** 重试失败索引（doc_id 是文档 ID，不是文件 ID）。 */
  retry: (docId: number) => request<unknown>({ url: `/files/docs/${docId}/retry`, method: 'POST' }),
  /** 下载版本文件（source=true 取上传原件，false 取编辑后生成的版本文件）。 */
  downloadRevision: async (revisionId: number, fileName: string, source = false) =>
    downloadWithToken(`/files/revisions/${revisionId}/download?source=${source}`, fileName),
  /** 上传并导入笔试题库（JSON）。 */
  uploadQuestions: (file: File, syncIndex = true) => {
    const form = new FormData();
    form.append('file', file);
    form.append('sync_index', String(syncIndex));
    return request<QuestionImportResult>({ url: '/files/questions/upload', method: 'POST', data: form, headers: { 'Content-Type': 'multipart/form-data' } });
  },
  /** 版本流转：编辑正文另存 / 发布 / 历史恢复 / 取消草稿（都作用在文件版本上）。 */
  /** 正文编辑以「文档」为入口（doc_id）：后端基于当前已发布生效版本另存新草稿，
   *  调用方无需查找 revision_id，也无需维护 expected_version 乐观锁。 */
  /** 读取「编辑基线」：只需 doc_id，返回当前生效版本正文/标题/分类，用于填充编辑器。 */
  documentEditable: (docId: number) =>
    request<DocumentEditable>({ url: `/files/docs/${docId}/editable`, method: 'GET' }),
  editDocument: (docId: number, data: { body: string; title?: string; category?: string }) =>
    request<RevisionItem>({ url: `/files/docs/${docId}/edit`, method: 'POST', data }),
  /** @deprecated 旧版按 revision_id 编辑，保留供历史版本恢复等特殊场景使用。 */
  editRevision: (revisionId: number, data: { content: string; title?: string; category?: string; expected_version: number }) =>
    request<RevisionItem>({ url: `/files/revisions/${revisionId}/edit`, method: 'POST', data }),
  publishRevision: (revisionId: number, expectedVersion: number) =>
    request<RevisionItem>({ url: `/files/revisions/${revisionId}/publish`, method: 'POST', data: { expected_version: expectedVersion } }),
  restoreRevision: (revisionId: number, expectedVersion: number) =>
    request<RevisionItem>({ url: `/files/revisions/${revisionId}/restore`, method: 'POST', data: { expected_version: expectedVersion } }),
  discardRevision: (revisionId: number, expectedVersion: number) =>
    request<RevisionItem>({ url: `/files/revisions/${revisionId}/discard`, method: 'POST', data: { expected_version: expectedVersion } }),
  /** 删除文档索引（逻辑删除 + 补偿任务清理向量，不代表立即完成）。 */
  deleteIndex: (fileId: string | number, category: string) =>
    request<unknown>({ url: '/files/index/delete', method: 'POST', data: { file_id: String(fileId), category } }),
};

/* ======================= 审计日志 [架构组] ======================= */
export interface AuditListParams { page?: number; page_size?: number; keyword?: string; user_id?: number; module?: string; action?: string; status?: string; trace_id?: string; start_at?: string; end_at?: string }
export interface AuditStats { total: number; by_action: Record<string, number>; by_module: Record<string, number>; success: number; failed: number; fail_rate: number }

export const auditApi = {
  logs: (params?: AuditListParams) => request<PageResult<AuditEntry>>({ url: '/audit/logs', params }),
  detail: (id: number) => request<AuditEntry>({ url: `/audit/logs/${id}` }),
  stats: (params?: { start_at?: string; end_at?: string }) => request<AuditStats>({ url: '/audit/stats', params }),
};

export function flattenOrgs(items: OrgNode[]): OrgNode[] {
  return items.flatMap((item) => [item, ...flattenOrgs(item.children || [])]);
}

export function orgNameMap(items: OrgNode[]): Map<number, string> {
  return new Map(flattenOrgs(items).map((item) => [item.id, item.name]));
}

export function withOrganizationNames<T extends { organization_id?: number | null; organization_name?: string | null }>(rows: T[], names: Map<number, string>): T[] {
  return rows.map((row) => ({ ...row, organization_name: row.organization_name ?? (row.organization_id != null ? names.get(row.organization_id) ?? null : null) }));
}

/* ======================= AI 数字人面试 [Agent组] ======================= */
export const aiInterviewsApi = {
  config: () => request<AiInterviewConfig>({ url: '/ai-interviews/config' }),
  list: (params?: { page?: number; page_size?: number; status?: string; keyword?: string; talent_id?: number; position_id?: number }) =>
    request<PageResult<AiInterviewSession>>({ url: '/ai-interviews', params }),
  create: (data: Record<string, unknown>) =>
    request<AiInterviewSession & { plan_source?: string; email?: { ok?: boolean; error?: string | null } | null }>(
      { url: '/ai-interviews', method: 'POST', data }),
  detail: (id: number) => request<AiInterviewDetail>({ url: `/ai-interviews/${id}` }),
  report: (id: number) => request<AiInterviewReport & { session_id: number; candidate_name: string }>(
    { url: `/ai-interviews/${id}/report` }),
  cancel: (id: number) => request<AiInterviewSession>({ url: `/ai-interviews/${id}/cancel`, method: 'POST' }),
  /** 以下为候选人公开接口（凭 token，无需登录）。 */
  state: (token: string) => request<AiInterviewPublicState>({ url: `/ai-interviews/session/${encodeURIComponent(token)}` }),
  start: (token: string) => request<AiInterviewPublicState>({ url: `/ai-interviews/session/${encodeURIComponent(token)}/start`, method: 'POST' }),
  answer: (token: string, data: { answer: string; duration_seconds: number; input_mode: 'voice' | 'text' }) =>
    request<AiInterviewAnswerResult>({ url: `/ai-interviews/session/${encodeURIComponent(token)}/answer`, method: 'POST', data }),
  repeat: (token: string) => request<{ text: string; completed: boolean }>({ url: `/ai-interviews/session/${encodeURIComponent(token)}/repeat`, method: 'POST' }),
  finish: (token: string) => request<AiInterviewPublicState>({ url: `/ai-interviews/session/${encodeURIComponent(token)}/finish`, method: 'POST' }),

  /** 生成数字人说话头视频（ComfyUI 驱动，需 ai_interview:manage 权限） */
  generateDigitalHumanVideo: (data: {
    text: string;
    persona_key?: string;
    voice?: string | null;
    language?: string;
  }) =>
    request<{ ok: boolean; provider: string; video_url: string; elapsed_ms: number; error: string }>({
      url: '/ai-interviews/digital-human/video',
      method: 'POST',
      data,
    }),
};
