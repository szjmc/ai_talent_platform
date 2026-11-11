export interface ApiResponse<T> { code: number; message: string; data: T; trace_id?: string }
export interface PageResult<T> { items: T[]; total: number; page: number; page_size: number; pages: number }
export interface Role { id: number; code: string; name: string }
export interface User {
  id: number; username: string; real_name?: string | null; mobile?: string | null; email?: string | null; avatar?: string | null;
  organization_id?: number | null; organization_name?: string | null; position_title?: string | null; is_active: boolean;
  is_superuser: boolean; roles: Role[]; last_login_at?: string | null; created_at?: string | null;
}
export interface LoginUser {
  id: number; username: string; real_name?: string | null; avatar?: string | null; organization_id?: number | null;
  organization_name?: string | null; position_title?: string | null; is_superuser: boolean; roles: string[]; permissions: string[];
}
export interface LoginResult { access_token: string; refresh_token: string; token_type: string; expires_in: number; user: LoginUser }
export interface OrgNode {
  id: number; parent_id?: number | null; code: string; name: string; leader_id?: number | null; leader_name?: string | null;
  level?: number; path?: string; sort: number; is_active: boolean; user_count: number; remark?: string | null;
  created_at?: string | null; children?: OrgNode[];
}
/** 人才档案字段命名保持前端既有契约；后端未返回的扩展字段不参与筛选/统计。 */
export interface Talent {
  id: number; name: string; avatar?: string; gender?: 'male'|'female'|'unknown'; mobile?: string | null; email?: string | null;
  education?: string | null; school?: string | null; major?: string | null; work_years: number; current_company?: string | null;
  current_position?: string | null; organization_id?: number | null; organization_name?: string | null; skill_tags: string[];
  certificates?: string[]; status: string; potential_level?: string | null;
  portrait_summary?: string | null; self_evaluation?: string | null; source?: string | null; match_score?: number | null;
  version?: number; created_at?: string | null;
  country?: string; country_code?: string; province?: string; work_city?: string; job_level?: string;
  portrait?: TalentPortrait;
}
export interface TalentPortrait {
  talent_id: number; version?: number; dimensions?: Record<string, number>; labels?: unknown[];
  strengths?: string[]; weaknesses?: string[]; suggested_positions?: string[];
  potential_level?: string | null; model_name?: string; generated_at?: string; status?: string; summary?: string;
}
export interface Position {
  id: number; title: string; code: string; organization_id: number; organization_name: string;
  level: 'junior'|'middle'|'senior'|'expert'; headcount: number; headcount_filled: number;
  status: 'open'|'paused'|'closed'; description: string; skill_tags: string[]; salary_min: number; salary_max: number;
  requirements?: { education: string; experience_years: number; skills: string[] }; publish_at?: string;
}
/** 文件版本（文件管理的核心对象：一个文档有多个版本文件）。
 *  注意：后端 /files 不提供「文件元数据列表」接口，文件按文档版本维度查看，
 *  所以这里没有 FileItem 之类的独立文件实体，避免前端按不存在的契约开发。
 */
export interface FileRevision {
  id?: number; revision_id?: number; doc_id?: number; version: number;
  status?: string; title?: string; category?: string; file_id?: number | null;
  created_at?: string | null;
}
/** 审计日志（只增不改，仅 audit:read 可查）。 */
export interface AuditEntry {
  id: number; user_id: number | null; username: string | null;
  action: string; module: string | null; target_type?: string | null; target_id?: number | null;
  request_method?: string | null; request_path?: string | null; request_ip?: string | null;
  user_agent?: string | null; trace_id: string | null;
  before_data?: Record<string, unknown> | null; after_data?: Record<string, unknown> | null;
  status: string; error_message?: string | null; created_at?: string | null;
}
export interface Resume {
  id: number; file_id?: number | null; talent_id?: number | null; talent_name?: string | null; file_name: string;
  file_size: number; file_type?: string | null; status: 'uploaded'|'parsing'|'success'|'failed';
  accuracy?: number | null; created_at: string; parsed_at?: string | null; duplicate?: boolean;
}
export interface ResumeParseResult {
  resume_id: number; status: string; parsed?: {
    basic_info: Record<string, unknown>; education: Record<string, unknown>[];
    work_experience: { company: string; title?: string | null; start_date?: string | null; end_date?: string | null; description?: string | null }[];
    projects: Record<string, unknown>[]; skills: string[]; warnings: string[];
  } | null;
  accuracy?: number | null; warnings: string[]; parsed_at?: string | null; error?: string | null;
}
export interface Assessment {
  id: number; title: string; talent_id: number; talent_name?: string | null; position_id?: number | null; position_title?: string | null;
  question_count: number; status: string; deadline?: string | null; total_score?: number | null; level?: string | null;
  created_by_ai?: boolean; created_at?: string | null;
}
export interface AssessmentReport {
  talent_id: number; position_id?: number | null; written_score: number; interview_score?: number | null;
  radar: Record<string, number | null>; radar_labels: Record<string, string>; total_score: number; level: string;
  conclusion: string; strengths: string[]; weaknesses: string[]; suggestions: string[];
  evidence?: Record<string, unknown>; generator?: string;
}
export interface Matching {
  talent_id: number; talent_name: string; avatar?: string; current_position?: string | null; organization_name?: string | null;
  score: number; dimension_scores: Record<string, number>;
  highlights: string[]; gaps: string[]; suggestion?: string | null; skill_tags: string[];
  rank?: number; decision?: string; matched_skills?: string[];
}
export interface MatchingRunResult {
  batch_id: string; position_id: number; position_title?: string | null; items: Matching[];
  excluded?: unknown[]; stats: { candidates: number; excluded_by_hard: number; needs_review: number; output: number; unknown: number };
  recall_mode: string; requirement_version: number; matched_at?: string | null; weights_used?: Record<string, number> | null;
}
export interface KnowledgeDoc {
  id: number; doc_id?: number; title: string; category: string; source_type?: string; status: string;
  chunk_count: number; permission_scope?: string; indexed_at?: string; file_size?: string; file_id?: number | null;
}
export interface Course {
  id: number; title: string; category?: string | null; level?: string | null; description?: string | null;
  duration_hours?: string | number | null; cover?: string | null; resource_url?: string | null;
  skill_tags?: unknown[] | null; is_active: boolean; created_at?: string | null; updated_at?: string | null;
}
export interface TrainingPlan {
  id: number; name: string; talent_id: number; goal?: string | null; start_date?: string | null; end_date?: string | null;
  status: string; created_by_ai: boolean; completion_rate: number; created_at?: string | null;
}
export interface TrainingRecommendation {
  course_id: number; title: string; gap_dimension: string; reason: string; score: number;
  duration_hours?: number | null; level?: number | null;
}
export interface TrainingNotification {
  id: number; plan_id: number; talent_id?: number | null; type: string; level: string; title: string; content: string;
  notify_date: string; is_read: boolean; read_at?: string | null; created_at?: string | null;
}
export interface DistributionItem { value: string | number | null; label: string; count: number }
export interface DistributionResult { type: string; source: string; month?: string | null; items: DistributionItem[]; truncated: boolean; warnings: string[] }
export interface TrendItem { month: string; value: number | null; missing: boolean; mom?: unknown; yoy?: unknown }
export interface TrendResult { metric: string; granularity: string; source: string; comparison: string; items: TrendItem[]; warnings: string[] }
export interface AssessmentSummary {
  record_count: number; scored_count: number; talent_count: number; average_score?: number | null;
  minimum_score?: number | null; maximum_score?: number | null; source: string; basis: string;
}
export interface NL2SQLResult {
  question: string; sql: string; columns: string[]; rows: Record<string, unknown>[]; insight?: string | null;
  chart?: { type?: string; options?: Record<string, unknown> } | null; query_id?: string | null;
  elapsed_ms: number; cached: boolean; warnings: string[];
}
export interface ReportItem { report_id: string; title: string; format: string; url: string; size: number; created_at: string }
export interface AgentConversation { conversation_id: string | number; title?: string | null; agent_type?: string | null; updated_at?: string | null; created_at?: string | null }

/* ──────────────── 人才决策报告（analytics Agent 结构化输出） ──────────────── */
export interface ChartDataPoint { name: string; value: number }
export interface ChartSeries { name: string; data: ChartDataPoint[] }
export interface AnalyticsChart {
  type: string; title: string;
  x_field?: string; y_field?: string; value_field?: string | null;
  series?: ChartSeries[]; indicators?: unknown; x_labels?: string[]; y_labels?: unknown;
  values?: unknown; style?: unknown; size?: unknown; theme?: unknown; group?: string;
}
export interface RiskItem {
  name: string; priority: string; phenomenon: string;
  business_impact: string; drill_hint?: string;
}
export interface ReportMeta {
  report_purpose?: string; data_cutoff?: string; data_scope?: string; sample_tip?: string;
  threshold_desc?: string; kpi_goal?: Record<string, string>;
}
export interface InsightReport {
  conclusions?: string[]; suggestions?: string[] | { p0?: string[]; p1?: string[]; warn_if_no_action?: string };
  suggestion_p0?: string[]; suggestion_p1?: string[]; warn_if_no_action?: string;
  group_summaries?: Record<string, string>; risk_list?: RiskItem[];
  chart_display?: Record<string, string>; key_talent_top3?: string[];
  report_audience?: string; meta?: ReportMeta; appendix_note?: string;
  answer?: string;
}
export interface AnalyticsAgentResponse {
  conversation_id: string; answer: string; agent: string; elapsed_ms: number;
  report?: (InsightReport & {
    charts?: AnalyticsChart[]; risks?: unknown[]; tenure?: { labels: string[]; data: number[] };
    kpi?: { avg?: number; talent?: number; bad?: number; train_rate?: number };
    data_scope?: string; period_label?: string; report_type?: string;
  }) | null;
}
export interface PeriodReportResponse {
  insight: InsightReport;
  data: { charts?: AnalyticsChart[]; risks?: unknown[]; row_count?: number; [k: string]: unknown };
  report_type: string; period_label: string; from_cache: boolean; period_updated_at?: string;
  source?: string;
}

/* ======================= AI 数字人面试 [Agent组] ======================= */
export interface AiInterviewPersona {
  key: string; name: string; title: string; style: string; tagline?: string;
  avatar_theme: string; language: string; voice_hint?: string;
  /** 数字人真人肖像照片 URL；后端配置后前端渲染真人数字人，未配置则使用默认头像。 */
  photo_url?: string;
}
export interface AiInterviewSession {
  id: number; talent_id?: number | null; position_id?: number | null; invitation_id?: number | null;
  assessment_id?: number | null; token: string; url: string; title: string;
  candidate_name: string; candidate_email?: string | null; position_title?: string | null;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled' | 'expired';
  difficulty: string; question_count: number; duration_minutes: number; language: string;
  interviewer: AiInterviewPersona; answered_count: number;
  total_score?: number | null; level?: string | null; recommendation?: string | null;
  recommendation_label?: string | null; started_at?: string | null; finished_at?: string | null;
  expires_at?: string | null; created_at?: string | null;
}
export interface AiInterviewTurn {
  id?: number; seq: number; role: 'interviewer' | 'candidate' | string; kind: string;
  question_index?: number | null; dimension?: string | null; content: string;
  input_mode?: string | null; duration_seconds: number; score?: number | null;
  signals?: Record<string, unknown> | null; feedback?: string | null; created_at?: string | null;
}
export interface AiInterviewPerQuestion {
  index?: number; question: string; dimension?: string; dimension_label?: string;
  answer?: string; score?: number | null; feedback?: string;
  signals?: { keywords_hit?: string[]; numbers?: string[]; chinese_chars?: number; english_words?: number; structure_markers?: string[]; [key: string]: unknown };
}
export interface AiInterviewReport {
  total_score: number; level: string; recommendation: string; recommendation_label: string;
  dimension_scores: Record<string, number | null>; dimension_labels: Record<string, string>;
  dimension_weights?: Record<string, number>; question_count: number; answered_count: number;
  completion_rate: number; per_question: AiInterviewPerQuestion[];
  strengths: string[]; weaknesses: { dimension?: string; label?: string; score?: number; gap?: string }[];
  suggestions: string[]; summary: string; scoring_method: string; prompt_version?: string;
  generated_at?: string; integrity?: Record<string, unknown>; observations?: string[];
}
export interface AiInterviewDetail extends AiInterviewSession {
  plan: { id: number; text: string; dimension: string; intent?: string; keywords?: string[]; follow_up?: string; weight?: number }[];
  turns: AiInterviewTurn[];
  report?: AiInterviewReport | null;
}
export interface AiInterviewQuestion {
  index: number; text: string; kind: 'main' | 'follow_up';
  dimension: string; dimension_label: string; intent?: string;
}
export interface AiInterviewPublicState {
  token: string; title: string; status: 'pending' | 'in_progress' | 'completed' | 'cancelled' | 'expired';
  candidate_name: string; position_title?: string | null; language: string; difficulty: string;
  duration_minutes: number; question_count: number; interviewer: AiInterviewPersona;
  greeting: string; started_at?: string | null; finished_at?: string | null; deadline?: string | null;
  progress: { answered: number; total: number; percent: number; current_index: number; remaining_seconds?: number | null };
  current_question?: AiInterviewQuestion | null;
  transcript: { seq: number; role: string; kind: string; content: string; created_at?: string | null }[];
  result?: {
    total_score: number; level: string; recommendation?: string; recommendation_label?: string;
    dimension_scores: Record<string, number | null>; dimension_labels: Record<string, string>;
    answered_count: number; question_count: number; completion_rate: number;
    summary: string; strengths: string[]; weaknesses: { label?: string; score?: number; gap?: string }[];
    suggestions: string[]; per_question: { index: number; question: string; dimension_label: string; score: number | null; feedback: string }[];
    scoring_method: string;
  } | null;
  server_time?: string | null;
}
export interface AiInterviewAnswerResult {
  accepted: boolean; completed: boolean; timed_out: boolean; interviewer_reply: string;
  follow_up: boolean; next_question?: AiInterviewQuestion | null;
  progress: AiInterviewPublicState['progress']; state: AiInterviewPublicState;
  result?: AiInterviewPublicState['result'] | null;
}
export interface AiInterviewConfig {
  personas: AiInterviewPersona[]; default_question_count: number; max_question_count: number;
  default_duration_minutes: number; default_expires_hours: number;
  languages: { value: string; label: string }[]; difficulties: { value: string; label: string }[];
}
