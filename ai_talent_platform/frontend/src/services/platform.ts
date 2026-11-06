import { request } from './api';
import type {
  Assessment, AssessmentReport, AssessmentSummary, AgentConversation, Course, DistributionResult,
  KnowledgeDoc, MatchingRunResult, NL2SQLResult, OrgNode, PageResult, ReportItem, Resume,
  ResumeParseResult, Talent, TalentPortrait, TrainingNotification, TrainingPlan,
  TrainingRecommendation, TrendResult, User,
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

export const positionsApi = {
  /** 后端当前仅提供岗位画像接口；岗位 CRUD 占位接口返回 501 时由页面如实提示。 */
  listPlaceholder: () => request<PageResult<unknown>>({ url: '/positions' }),
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
    return request<Record<string, unknown>>({ url: '/knowledge/docs/upload', method: 'POST', data: form, headers: { 'Content-Type': 'multipart/form-data' } });
  },
  retry: (docId: number) => request<unknown>({ url: `/knowledge/docs/${docId}/retry`, method: 'POST' }),
  ask: (data: { question: string; session_id?: string | null; category?: string | null; top_k?: number; use_rerank?: boolean; suggest?: boolean }) =>
    request<{ answer: string; session_id: string; intent: string; sources: { doc_id?: number; chunk_id?: number; file_id?: number; source?: string; page?: number | null; score?: number }[]; suggestions: string[] }>({ url: '/text2answer', method: 'POST', data }),
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
};

export const agentsApi = {
  conversations: (params?: Record<string, unknown>) => request<PageResult<AgentConversation>>({ url: '/agents/conversations', params }),
  messages: (conversationId: string | number) => request<unknown[]>({ url: `/agents/conversations/${conversationId}/messages` }),
};

export const stubModulesApi = {
  files: () => request<unknown>({ url: '/files' }),
  audit: () => request<unknown>({ url: '/audit' }),
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
