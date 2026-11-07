import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, DatePicker, Drawer, Input, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { AuditOutlined, DownloadOutlined, EyeOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { auditApi, type AuditStats } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText } from '../utils';
import type { AuditEntry } from '../types';

const ACTION_LABEL: Record<string, string> = { create: '创建', update: '更新', delete: '删除', login: '登录', logout: '登出', export: '导出', ai_call: 'AI 调用', read: '查询' };
const MODULE_OPTIONS = ['talent', 'position', 'knowledge', 'matching', 'training', 'assessment', 'auth', 'file', 'ai', 'analytics'];

export default function AuditPage() {
  const [data, setData] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [keyword, setKeyword] = useState('');
  const [module, setModule] = useState<string | undefined>();
  const [action, setAction] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const { message } = App.useApp();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const res = await auditApi.logs({
        page, page_size: pageSize,
        keyword: keyword || undefined, module, action, status,
        start_at: range?.[0]?.startOf('day').toISOString(),
        end_at: range?.[1]?.endOf('day').toISOString(),
      });
      setData(res.items || []); setTotal(res.total || 0);
    } catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, [page, pageSize, keyword, module, action, status, range]);

  const loadStats = useCallback(async () => {
    try { setStats(await auditApi.stats()); } catch { setStats(null); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadStats(); }, [loadStats]);

  const reset = () => { setKeyword(''); setModule(undefined); setAction(undefined); setStatus(undefined); setRange(null); setPage(1); };

  const exportCsv = () => {
    const header = '时间,用户,模块,操作,状态,来源IP,TraceID,详情\n';
    const body = data.map((l) => [l.created_at || '', l.username || '', l.module || '', l.action || '', l.status || '', l.request_ip || '', l.trace_id || '', (l.request_method || '') + ' ' + (l.request_path || '')].join(',')).join('\n');
    downloadText('操作审计日志.csv', header + body);
    message.success('当前页审计日志已导出');
  };

  const columns: ColumnsType<AuditEntry> = [
    { title: '操作时间', dataIndex: 'created_at', width: 165, render: (v) => v ? String(v).replace('T', ' ').slice(0, 19) : '—' },
    { title: '操作人', dataIndex: 'username', width: 110, render: (v) => <b style={{ fontWeight: 550 }}>{v || '系统'}</b> },
    { title: '业务模块', dataIndex: 'module', width: 110, render: (v) => v ? <Tag>{v}</Tag> : '—' },
    { title: '操作类型', dataIndex: 'action', width: 110, render: (v) => ACTION_LABEL[v] ? `${ACTION_LABEL[v]}（${v}）` : v },
    { title: '请求', dataIndex: 'request_path', render: (v, r) => <code style={{ fontSize: 11 }}>{r.request_method} {v || '—'}</code> },
    { title: '来源 IP', dataIndex: 'request_ip', width: 125, render: (v) => <code style={{ fontSize: 11 }}>{v || '—'}</code> },
    { title: '状态', dataIndex: 'status', width: 80, render: (v) => <StatusBadge status={v} /> },
    { title: '操作', width: 65, fixed: 'right', render: (_, r) => <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => setSelected(r)} /> },
  ];

  return <>
    <PageHeader title="操作与 AI 审计" description="全操作留痕、AI 调用可溯、数据导出可查，满足企业安全与合规要求"
      actions={<>
        <Button icon={<ReloadOutlined />} onClick={() => { void load(); void loadStats(); }}>刷新</Button>
        <Button icon={<DownloadOutlined />} disabled={!data.length} onClick={exportCsv}>导出当前页</Button>
      </>} />
    {loadError && <Alert type="warning" showIcon message="审计接口暂不可用" description={loadError} style={{ marginBottom: 16 }} />}
    <div className="metric-strip">
      <div className="metric-strip-item"><div className="metric-strip-label">审计日志</div><div className="metric-strip-value">{stats?.total ?? total}<span className="metric-strip-extra">条</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">成功</div><div className="metric-strip-value">{stats?.success ?? '—'}<span className="metric-strip-extra">条</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">失败</div><div className="metric-strip-value">{stats?.failed ?? '—'}<span className="metric-strip-extra">条</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">失败率</div><div className="metric-strip-value">{stats ? `${(stats.fail_rate * 100).toFixed(1)}%` : '—'}<span className="metric-strip-extra">全量</span></div></div>
    </div>
    <div className="insight-strip"><div className="insight-icon"><SafetyCertificateOutlined /></div><div className="insight-content"><div className="insight-title">安全审计</div><div className="insight-text">审计日志只增不改，仅具备 audit:read 权限的管理员可查询；敏感字段（密码、手机号、身份证）在入库前已脱敏。</div></div></div>
    <div className="card filter-card"><div className="filter-row">
      <Input prefix={<SearchOutlined />} placeholder="搜索操作人、路径或失败原因" style={{ width: 260 }} value={keyword} allowClear
        onChange={(e) => { setKeyword(e.target.value); setPage(1); }} onPressEnter={() => void load()} />
      <Select placeholder="业务模块" allowClear style={{ width: 140 }} value={module} onChange={(v) => { setModule(v); setPage(1); }}
        options={MODULE_OPTIONS.map((v) => ({ value: v, label: v }))} />
      <Select placeholder="操作类型" allowClear style={{ width: 130 }} value={action} onChange={(v) => { setAction(v); setPage(1); }}
        options={Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label }))} />
      <Select placeholder="操作状态" allowClear style={{ width: 115 }} value={status} onChange={(v) => { setStatus(v); setPage(1); }}
        options={[{ value: 'success', label: '成功' }, { value: 'fail', label: '失败' }]} />
      <DatePicker.RangePicker value={range} onChange={(v) => { setRange(v as [Dayjs, Dayjs] | null); setPage(1); }} />
      <Button type="primary" ghost onClick={() => void load()}>查询</Button>
      <Button onClick={reset}>重置</Button>
    </div></div>
    <div className="card content-card">
      <div className="table-toolbar"><div className="table-title">审计日志<span className="table-count">共 {total} 条</span></div></div>
      <Table rowKey="id" columns={columns} dataSource={data} loading={loading} scroll={{ x: 1250 }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, onChange: (p, size) => { setPage(p); setPageSize(size); } }}
        locale={{ emptyText: loadError ? '后端审计接口暂不可用' : '暂无审计日志' }} />
    </div>
    <Drawer width={560} open={!!selected} onClose={() => setSelected(null)} title="审计日志详情" extra={<Tag icon={<AuditOutlined />}>只读记录</Tag>}>
      {selected && <>
        <div className="detail-hero"><div className="ai-logo"><AuditOutlined /></div><div>
          <div className="detail-name" style={{ fontSize: 17 }}>{ACTION_LABEL[selected.action] || selected.action}</div>
          <div className="detail-meta">{selected.created_at ? String(selected.created_at).replace('T', ' ').slice(0, 19) : '—'} · {selected.username || '系统'}</div>
        </div></div>
        <div className="drawer-section-title">调用信息</div>
        <div className="description-list">{[['业务模块', selected.module], ['操作状态', selected.status], ['来源 IP', selected.request_ip], ['请求', `${selected.request_method || ''} ${selected.request_path || ''}`.trim()], ['对象', selected.target_type ? `${selected.target_type}#${selected.target_id ?? '-'}` : '—']].map(([k, v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v || '—'}</div></div>)}</div>
        <div className="drawer-section-title">全链路 Trace ID</div><div className="audit-detail">{selected.trace_id || '—'}</div>
        {selected.error_message && <><div className="drawer-section-title">失败原因</div><div className="portrait-summary">{selected.error_message}</div></>}
        <div className="drawer-section-title">变更前 / 变更后</div>
        <pre className="audit-detail" style={{ maxHeight: 220, overflow: 'auto' }}>{JSON.stringify({ before: selected.before_data ?? null, after: selected.after_data ?? null }, null, 2)}</pre>
      </>}
    </Drawer>
  </>;
}
