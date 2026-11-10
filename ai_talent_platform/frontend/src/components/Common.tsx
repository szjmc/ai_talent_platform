import type { ReactNode } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { Avatar as AntAvatar, Tag, Tooltip } from 'antd';
export function Brand({ light = false }: { light?: boolean }) {
  return <div className="login-brand"><div className="brand-mark"><i/><i/><i/><i/></div><div><div className="brand-name" style={light ? { color: '#fff' } : undefined}>智才云</div><div className="brand-sub">TALENTOS · AI</div></div></div>;
}
export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <div className="page-header"><div><div className="breadcrumb-line">智才云 TalentOS <span style={{ margin: '0 7px' }}>/</span> {title}</div><h1 className="page-title">{title}</h1>{description && <p className="page-description">{description}</p>}</div>{actions && <div className="page-actions">{actions}</div>}</div>;
}
export function Chart({ option, height = 280, style }: { option: EChartsOption; height?: number; style?: React.CSSProperties }) {
  return <ReactECharts option={option} style={{ height, width: '100%', ...style }} notMerge lazyUpdate opts={{ renderer: 'canvas' }} />;
}
export function PersonAvatar({ name, size = 36, color, className = '' }: { name: string; size?: number; color?: string; className?: string }) {
  const colors = ['#5273df', '#7a63c8', '#2e9b8b', '#d58945', '#4d8dbb', '#bb6d92'];
  const bg = color || colors[(name.charCodeAt(0) + (name.charCodeAt(1) || 0)) % colors.length];
  return <AntAvatar className={`avatar ${className}`} size={size} style={{ background: bg, fontSize: size * .34 }}>{name.slice(-2)}</AntAvatar>;
}
export function PotentialTag({ level }: { level: string }) {
  const map: Record<string, [string, string]> = { A: ['#e8f7f0', '#15966b'], B: ['#edf2ff', '#476bdd'], C: ['#fff5e5', '#cf891c'], D: ['#f1f2f5', '#7f8897'] };
  const [background, color] = map[level] || map.B;
  return <Tooltip title={`${level} 级潜力人才`}><span className="potential-tag" style={{ background, color }}>{level}</span></Tooltip>;
}
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    active: ['success', '在职'], open: ['success', '招聘中'], success: ['success', '成功'], indexed: ['success', '已索引'], scored: ['success', '已评分'], completed: ['success', '已完成'], enabled: ['success', '已启用'],
    reserve: ['info', '人才储备'], parsing: ['info', '解析中'], indexing: ['info', '索引中'], in_progress: ['info', '作答中'], submitted: ['info', '评分中'], running: ['info', '执行中'], paused: ['warning', '已暂停'], pending: ['warning', '待开始'], uploaded: ['warning', '待解析'],
    left: ['default', '已离职'], closed: ['default', '已关闭'], disabled: ['default', '已停用'], failed: ['error', '失败'], error: ['error', '异常'],
    cancelled: ['default', '已取消'], canceled: ['default', '已取消'], expired: ['warning', '已过期'],
  };
  const [type, text] = map[status] || ['default', status];
  return <span className={`status-badge ${type}`}>{text}</span>;
}
export function SkillTags({ tags, max = 3 }: { tags: string[]; max?: number }) {
  return <span>{tags.slice(0, max).map((tag) => <Tag key={tag} className="skill-tag" color="geekblue">{tag}</Tag>)}{tags.length > max && <Tooltip title={tags.slice(max).join('、')}><Tag className="skill-tag">+{tags.length - max}</Tag></Tooltip>}</span>;
}
export const chartAxis = { axisLine: { lineStyle: { color: '#e8ecf2' } }, axisTick: { show: false }, axisLabel: { color: '#8a95a8', fontSize: 11 } };
export const chartTooltip = { trigger: 'axis' as const, backgroundColor: 'rgba(28,38,57,.94)', borderWidth: 0, textStyle: { color: '#fff', fontSize: 12 }, padding: [10, 13] };
export const baseGrid = { left: 42, right: 18, top: 30, bottom: 30 };
