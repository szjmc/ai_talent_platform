import axios, { AxiosError } from 'axios';
import type { ApiResponse } from '../types';

export const TOKEN_KEY = 'talentos_access_token';
export const REFRESH_KEY = 'talentos_refresh_token';
export const USER_KEY = 'talentos_user';
export const isDemoFallback = import.meta.env.VITE_DEMO_FALLBACK !== 'false';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

let refreshing: Promise<string> | null = null;
client.interceptors.response.use((response) => response, async (error: AxiosError<ApiResponse<unknown>>) => {
  const original = error.config as (typeof error.config & { _retried?: boolean });
  const code = error.response?.data?.code;
  const refreshToken = localStorage.getItem(REFRESH_KEY);
  if ((code === 2002 || (error.response?.status === 401 && code === 2001)) && refreshToken && original && !original._retried && !original.url?.includes('/auth/')) {
    original._retried = true;
    try {
      refreshing ||= client.post<ApiResponse<{ access_token: string; refresh_token: string }>>('/auth/refresh', { refresh_token: refreshToken })
        .then(({ data }) => {
          localStorage.setItem(TOKEN_KEY, data.data.access_token);
          localStorage.setItem(REFRESH_KEY, data.data.refresh_token);
          return data.data.access_token;
        }).finally(() => { refreshing = null; });
      await refreshing;
      return client(original);
    } catch {
      localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(USER_KEY);
      window.dispatchEvent(new Event('talentos:unauthorized'));
    }
  }
  return Promise.reject(error);
});

export class ApiError extends Error {
  code: number;
  status: number;
  traceId?: string;
  details?: unknown;
  constructor(error: unknown) {
    const axiosError = error as AxiosError<ApiResponse<unknown>>;
    const data = axiosError.response?.data;
    super(data?.message || (axiosError.code === 'ERR_NETWORK' ? '后端服务暂不可用' : '请求失败，请稍后重试'));
    this.code = data?.code || axiosError.response?.status || 0;
    this.status = axiosError.response?.status || 0;
    this.traceId = data?.trace_id;
    this.details = data?.data;
  }
}

export async function request<T>(config: Parameters<typeof client.request>[0]): Promise<T> {
  try {
    const response = await client.request<ApiResponse<T>>(config);
    return response.data.data;
  } catch (error) { throw new ApiError(error); }
}

/** 未完成的 501 后端模块使用演示数据；网络错误仅在显式开启时回退。 */
export async function requestWithFallback<T>(config: Parameters<typeof client.request>[0], fallback: T | (() => T)): Promise<{ data: T; demo: boolean }> {
  try { return { data: await request<T>(config), demo: false }; }
  catch (error) {
    const e = error as ApiError;
    if (isDemoFallback && (e.code === 501 || e.status === 501 || e.code === 0)) {
      return { data: typeof fallback === 'function' ? (fallback as () => T)() : fallback, demo: true };
    }
    throw error;
  }
}

export interface StreamEvent { event: string; data: Record<string, unknown> }
/** FastAPI /agents/chat/stream SSE 客户端，兼容 start/delta/citation/done/error 标准事件。 */
export async function streamAgentChat(
  payload: { agent_type: string; conversation_id?: string; message: string; stream: boolean },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
) {
  const baseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
  const token = localStorage.getItem(TOKEN_KEY);
  const response = await fetch(`${baseURL}/agents/chat/stream`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError({ response: { status: response.status, data: body }, code: 'ERR_BAD_RESPONSE' });
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应');
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  const consume = (block: string) => {
    const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim() || 'message';
    const raw = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
    if (!raw) return;
    let data: Record<string, unknown>;
    try { data = JSON.parse(raw); } catch { data = { text: raw }; }
    onEvent({ event, data });
  };
  while (true) {
    const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
    const blocks = buffer.split('\n\n'); buffer = blocks.pop() || ''; blocks.forEach(consume);
    if (done) { if (buffer.trim()) consume(buffer); break; }
  }
}

export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    const validation = error.details as { errors?: { field: string; message: string }[] } | null;
    if (validation?.errors?.length) return validation.errors.map((e) => `${e.field}: ${e.message}`).join('；');
    return error.message;
  }
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

/* ======================= 人才邀约（笔试 / 测评 / 面试间）[Agent组] ======================= */
export type InviteType = 'written_test' | 'assessment' | 'interview';
export type InviteChannel = 'sms' | 'email';

export interface InviteCreatePayload {
  type: InviteType;
  talent_id?: number;
  talent_name?: string;
  mobile?: string;
  email?: string;
  channels: InviteChannel[];
  question_count?: number;
  duration_minutes?: number;
  interview_time?: string;
  expires_hours?: number;
  title?: string;
}
export interface InviteSendResult { channel: string; ok: boolean; mode: string; error?: string | null }
export interface InviteInfo {
  id: number; talent_id?: number | null; talent_name?: string | null; type: InviteType; title: string;
  token: string; url: string; status: string; channels: InviteChannel[]; send_results: InviteSendResult[];
  question_count?: number | null; duration_minutes: number; interview_time?: string | null;
  expires_at?: string | null; created_at?: string | null;
}
export interface InviteVisit {
  type: InviteType; title: string; status: string; candidate_name?: string | null;
  interview_time?: string | null; expires_at?: string | null; duration_minutes: number;
  question_count?: number | null; proctor_enabled: boolean; proctor_engine: string;
  frame_interval_seconds: number; ice_servers: { urls: string; username?: string; credential?: string }[];
}
export interface ExamQuestion { id: number; seq: number; type: 'single' | 'multiple' | 'text'; content: string; options?: { key: string; text: string }[] | null }
export interface ExamStart {
  status: string; started_at?: string | null; deadline?: string | null; questions: ExamQuestion[];
  total_score: number; proctor_enabled: boolean; proctor_engine: string; frame_interval_seconds: number;
}
export interface ExamResult {
  status: string; score?: number | null; total_score?: number | null; submitted_at?: string | null;
  violations_count: number; pending_manual: boolean;
}
export interface FrameCheck {
  enabled: boolean; engine: string; violations: { ts: number; type: string; detail: string }[];
  total_violations: number; person_like: number;
}

export const invitesApi = {
  create: (data: InviteCreatePayload) => request<InviteInfo>({ url: '/invites', method: 'post', data }),
  list: (params?: { talent_id?: number; page?: number; page_size?: number }) =>
    request<{ items: InviteInfo[]; total: number }>({ url: '/invites', params }),
  visit: (token: string) => request<InviteVisit>({ url: `/invites/visit/${encodeURIComponent(token)}` }),
  examStart: (token: string) => request<ExamStart>({ url: `/invites/exam/${encodeURIComponent(token)}` }),
  examSubmit: (token: string, answers: Record<string, unknown>) =>
    request<ExamResult>({ url: `/invites/exam/${encodeURIComponent(token)}/submit`, method: 'post', data: { answers } }),
  examFrame: (token: string, image: string) =>
    request<FrameCheck>({ url: `/invites/exam/${encodeURIComponent(token)}/frame`, method: 'post', data: { image } }),
  examResult: (token: string) => request<ExamResult>({ url: `/invites/exam/${encodeURIComponent(token)}/result` }),
};

/** 面试间信令 WS 地址（相对 baseURL 或绝对地址均可；保持与页面同协议）。 */
export function inviteWsUrl(token: string, role: 'candidate' | 'interviewer'): string {
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1';
  const url = base.startsWith('http') ? new URL(base) : new URL(base, window.location.origin);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `${url.pathname.replace(/\/$/, '')}/invites/ws/${encodeURIComponent(token)}`;
  url.search = `role=${role}`;
  return url.toString();
}
