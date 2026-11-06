import dayjs from 'dayjs';
export const formatDate = (value?: string | null, withTime = false) => value ? dayjs(value).format(withTime ? 'YYYY-MM-DD HH:mm' : 'YYYY-MM-DD') : '—';
export const formatFileSize = (bytes: number) => bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
export const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN').format(value);
export function downloadText(filename: string, text: string) {
  const blob = new Blob(['\ufeff', text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
}
