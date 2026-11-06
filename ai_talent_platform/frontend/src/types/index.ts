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
