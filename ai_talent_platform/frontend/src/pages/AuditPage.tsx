import { useState } from 'react';
import { App, Button, DatePicker, Drawer, Input, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AuditOutlined, CheckCircleFilled, DownloadOutlined, EyeOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { auditLogs } from '../services/mockData';
import { useAuth } from '../store/AuthContext';
import { downloadText } from '../utils';

type Log = (typeof auditLogs)[number];
export default function AuditPage() {
  const [selected, setSelected] = useState<Log | null>(null); const { demo } = useAuth(); const { message } = App.useApp();
  const columns: ColumnsType<Log> = [
    { title: '操作时间', dataIndex: 'time', width: 165 },
    { title: '操作人', dataIndex: 'user', width: 100, render: (v) => <b style={{ fontWeight: 550 }}>{v}</b> },
    { title: '业务模块', dataIndex: 'module', width: 105, render: (v) => <Tag>{v}</Tag> },
    { title: '操作类型', dataIndex: 'action', width: 115 },
    { title: '操作详情', dataIndex: 'detail' },
    { title: '来源 IP', dataIndex: 'ip', width: 120, render: (v) => <code style={{ fontSize: 11 }}>{v}</code> },
    { title: '状态', dataIndex: 'status', width: 75, render: (v) => <StatusBadge status={v}/> },
    { title: '操作', width: 65, render: (_, r) => <Button type="text" size="small" icon={<EyeOutlined/>} onClick={() => setSelected(r)}/> },
  ];
  return <>
    <PageHeader title="操作与 AI 审计" description="全操作留痕、AI 调用可溯、数据导出可查，满足企业安全与合规要求" demo={demo} actions={<Button icon={<DownloadOutlined/>} onClick={() => { downloadText('操作审计日志.csv','时间,用户,模块,操作,详情\n'+auditLogs.map((l) => `${l.time},${l.user},${l.module},${l.action},${l.detail}`).join('\n')); message.success('审计日志已导出并记录本次导出行为'); }}>导出日志</Button>}/>
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">今日操作日志</div><div className="metric-strip-value">8,426<span className="metric-strip-extra">条</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">AI 调用记录</div><div className="metric-strip-value">2,168<span className="metric-strip-extra">25.7%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">异常操作</div><div className="metric-strip-value">12<span className="metric-strip-extra text-danger">0.14%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">审计留存周期</div><div className="metric-strip-value">365<span className="metric-strip-extra">天</span></div></div></div>
    <div className="insight-strip"><div className="insight-icon"><SafetyCertificateOutlined/></div><div className="insight-content"><div className="insight-title">安全审计状态正常</div><div className="insight-text">日志采用<b>只增不改</b>策略，所有记录携带全链路 Trace ID。今日未发现敏感数据越权访问，12 条失败记录均为外部 AI 服务超时。</div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} placeholder="搜索操作人、Trace ID 或详情" style={{ width: 275 }}/><Select placeholder="业务模块" allowClear style={{ width: 130 }} options={['人才档案','AI 智能体','人岗匹配','数据分析','知识库'].map((v) => ({ value: v,label: v }))}/><Select placeholder="操作状态" allowClear style={{ width: 115 }} options={[{ value: 'success', label: '成功' },{ value: 'failed', label: '失败' }]}/><DatePicker.RangePicker/><Button type="primary" ghost>查询</Button><Button>重置</Button></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">审计日志<span className="table-count">实时更新</span></div><span className="status-badge success">审计服务运行中</span></div><Table rowKey="id" columns={columns} dataSource={auditLogs} pagination={{ pageSize: 20, total: 8426, showSizeChanger: false }} scroll={{ x: 1200 }}/></div>
    <Drawer width={560} open={!!selected} onClose={() => setSelected(null)} title="审计日志详情" extra={<Tag icon={<AuditOutlined/>}>只读记录</Tag>}>{selected && <><div className="detail-hero"><div className="ai-logo"><AuditOutlined/></div><div><div className="detail-name" style={{ fontSize: 17 }}>{selected.action}</div><div className="detail-meta">{selected.time} · {selected.user}</div></div></div><div className="drawer-section-title">调用信息</div><div className="description-list">{[['业务模块',selected.module],['操作状态',selected.status === 'success' ? '成功' : '失败'],['来源 IP',selected.ip],['客户端','Chrome 142 · macOS'],['请求方法','POST'],['响应耗时','326 ms']].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v}</div></div>)}</div><div className="drawer-section-title">全链路 Trace ID</div><div className="audit-detail">{selected.trace}</div><div className="drawer-section-title">操作详情</div><div className="portrait-summary">{selected.detail}</div><div className="drawer-section-title">请求与变更快照</div><pre className="audit-detail">{JSON.stringify({ action: selected.action, module: selected.module, operator: selected.user, before: null, after: { status: selected.status }, sensitive_fields: ['mobile', 'token'], masked: true }, null, 2)}</pre><div style={{ marginTop: 16, color: '#43927b', fontSize: 11 }}><CheckCircleFilled/> 日志完整性校验通过，未发现篡改</div></>}</Drawer>
  </>;
}
