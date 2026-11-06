import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, DatePicker, Drawer, Input, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AuditOutlined, DownloadOutlined, EyeOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { stubModulesApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText } from '../utils';

interface Log { id: number; user?: string | null; action?: string | null; module?: string | null; detail?: string | null; ip?: string | null; status?: string | null; time?: string | null; trace?: string | null }
export default function AuditPage() {
  const [data, setData] = useState<Log[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const [selected, setSelected] = useState<Log | null>(null); const { message } = App.useApp();
  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try { const res = await stubModulesApi.audit() as { items?: Log[]; total?: number }; setData(res.items || []); setTotal(res.total || (res.items || []).length); }
    catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const columns: ColumnsType<Log> = [
    { title: '操作时间', dataIndex: 'time', width: 165, render: (v) => v || '—' },
    { title: '操作人', dataIndex: 'user', width: 100, render: (v) => <b style={{ fontWeight: 550 }}>{v || '—'}</b> },
    { title: '业务模块', dataIndex: 'module', width: 105, render: (v) => v ? <Tag>{v}</Tag> : '—' },
    { title: '操作类型', dataIndex: 'action', width: 115, render: (v) => v || '—' },
    { title: '操作详情', dataIndex: 'detail', render: (v) => v || '—' },
    { title: '来源 IP', dataIndex: 'ip', width: 120, render: (v) => <code style={{ fontSize: 11 }}>{v || '—'}</code> },
    { title: '状态', dataIndex: 'status', width: 75, render: (v) => v ? <StatusBadge status={v}/> : '—' },
    { title: '操作', width: 65, render: (_, r) => <Button type="text" size="small" icon={<EyeOutlined/>} onClick={() => setSelected(r)}/> },
  ];
  return <>
    <PageHeader title="操作与 AI 审计" description="全操作留痕、AI 调用可溯、数据导出可查，满足企业安全与合规要求" actions={<Button icon={<DownloadOutlined/>} disabled={!data.length} onClick={() => { downloadText('操作审计日志.csv','时间,用户,模块,操作,详情\n'+data.map((l) => `${l.time || ''},${l.user || ''},${l.module || ''},${l.action || ''},${l.detail || ''}`).join('\n')); message.success('审计日志已导出'); }}>导出日志</Button>}/>
    {loadError && <Alert type="warning" showIcon message="审计接口暂不可用" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">审计日志</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">条</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页失败</div><div className="metric-strip-value">{data.filter((l) => l.status === 'failed').length}<span className="metric-strip-extra">条</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页成功</div><div className="metric-strip-value">{data.filter((l) => l.status === 'success').length}<span className="metric-strip-extra">条</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">接口状态</div><div className="metric-strip-value">{loadError ? '待实现' : '正常'}<span className="metric-strip-extra">后端占位</span></div></div></div>
    <div className="insight-strip"><div className="insight-icon"><SafetyCertificateOutlined/></div><div className="insight-content"><div className="insight-title">安全审计</div><div className="insight-text">审计日志以后端真实返回为准；当前后端审计模块若仍为占位接口，页面不会展示模拟日志。</div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} placeholder="搜索操作人、Trace ID 或详情" style={{ width: 275 }}/><Select placeholder="业务模块" allowClear style={{ width: 130 }} options={['人才档案','AI 智能体','人岗匹配','数据分析','知识库'].map((v) => ({ value: v,label: v }))}/><Select placeholder="操作状态" allowClear style={{ width: 115 }} options={[{ value: 'success', label: '成功' },{ value: 'failed', label: '失败' }]}/><DatePicker.RangePicker/><Button type="primary" ghost onClick={() => void load()}>查询</Button><Button onClick={() => void load()}>重置</Button></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">审计日志<span className="table-count">共 {total} 条</span></div></div><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={{ pageSize: 20, total, showSizeChanger: false }} scroll={{ x: 1200 }} locale={{ emptyText: loadError ? '后端审计接口暂不可用' : '暂无审计日志' }}/></div>
    <Drawer width={560} open={!!selected} onClose={() => setSelected(null)} title="审计日志详情" extra={<Tag icon={<AuditOutlined/>}>只读记录</Tag>}>{selected && <><div className="detail-hero"><div className="ai-logo"><AuditOutlined/></div><div><div className="detail-name" style={{ fontSize: 17 }}>{selected.action || '—'}</div><div className="detail-meta">{selected.time || '—'} · {selected.user || '—'}</div></div></div><div className="drawer-section-title">调用信息</div><div className="description-list">{[['业务模块',selected.module],['操作状态',selected.status],['来源 IP',selected.ip]].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v || '—'}</div></div>)}</div><div className="drawer-section-title">全链路 Trace ID</div><div className="audit-detail">{selected.trace || '—'}</div><div className="drawer-section-title">操作详情</div><div className="portrait-summary">{selected.detail || '—'}</div></>}</Drawer>
  </>;
}
