export interface ApiResponse<T> { code: number; message: string; data: T; trace_id?: string }
export interface PageResult<T> { items: T[]; total: number; page: number; page_size: number; pages: number }
export interface Role { id: number; code: string; name: string }
export interface User {
  id: number; username: string; real_name?: string; mobile?: string; email?: string; avatar?: string;
  organization_id?: number; organization_name?: string; position_title?: string; is_active: boolean;
  is_superuser: boolean; roles: Role[]; last_login_at?: string; created_at?: string;
}
export interface LoginUser {
  id: number; username: string; real_name?: string; avatar?: string; organization_id?: number;
  organization_name?: string; position_title?: string; is_superuser: boolean; roles: string[]; permissions: string[];
}
export interface LoginResult { access_token: string; refresh_token: string; token_type: string; expires_in: number; user: LoginUser }
export interface OrgNode {
  id: number; parent_id?: number; code: string; name: string; leader_id?: number; leader_name?: string;
  level?: number; path?: string; sort: number; is_active: boolean; user_count: number; remark?: string;
  created_at?: string; children?: OrgNode[];
}
export interface Talent {
  id: number; name: string; avatar?: string; gender: 'male'|'female'|'unknown'; mobile: string; email?: string;
  education: string; school: string; major: string; work_years: number; current_company: string;
  current_position: string; organization_id: number; organization_name: string; skill_tags: string[];
  certificates?: string[]; status: 'active'|'left'|'reserve'; potential_level: 'A'|'B'|'C'|'D';
  portrait_summary?: string; source?: string; match_score?: number; created_at?: string;
}
export interface Position {
  id: number; title: string; code: string; organization_id: number; organization_name: string;
  level: 'junior'|'middle'|'senior'|'expert'; headcount: number; headcount_filled: number;
  status: 'open'|'paused'|'closed'; description: string; skill_tags: string[]; salary_min: number; salary_max: number;
  requirements?: { education: string; experience_years: number; skills: string[] }; publish_at?: string;
}
export interface Resume {
  id: number; talent_id?: number; talent_name?: string; file_name: string; file_size: number; file_type: string;
  status: 'uploaded'|'parsing'|'success'|'failed'; accuracy?: number; created_at: string; parsed_at?: string;
}
export interface Assessment {
  id: number; title: string; talent_id: number; talent_name: string; position_title: string; question_count: number;
  status: 'pending'|'in_progress'|'submitted'|'scored'; deadline: string; total_score?: number; level?: string;
}
export interface Matching {
  talent_id: number; talent_name: string; avatar?: string; current_position: string; organization_name: string;
  score: number; dimension_scores: { skill: number; experience: number; education: number; quality: number };
  highlights: string[]; gaps: string[]; suggestion: string; skill_tags: string[];
}
export interface KnowledgeDoc {
  id: number; title: string; category: string; source_type: string; status: 'indexing'|'indexed'|'failed';
  chunk_count: number; permission_scope: string; indexed_at?: string; file_size?: string;
}
